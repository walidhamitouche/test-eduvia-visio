"""Planificateur (étape 10) et livraison des webhooks (étape 12).

Une boucle asyncio dans le processus de l'API : à chaque tour (1 s par défaut) elle
1. termine les réunions dont le créneau est fini (`lifecycle.run_due_meetings`) ;
2. rejoue les ordres de fermeture de salle en attente (`lifecycle.run_jobs`, qui arrête
   d'abord l'enregistrement) ;
3. démarre les enregistrements dus (`recording.run_jobs`, étape 14a) et tranche ceux dont la fin
   n'a pas été annoncée (`recording.reconcile`, étape 14b : filet si le webhook est perdu) ;
4. crée les tâches de transcription des réunions terminées (`transcription.enqueue_due`, 16c ;
   le calcul lui-même tourne dans le worker `visio.workers.transcriber`) ;
5. livre les webhooks dus (`webhooks.deliver_due`).

Rien n'est gardé en mémoire d'un tour à l'autre : tout se relit en base, donc un redémarrage
rattrape ce qui était en retard (`run_once` est aussi appelé directement par les tests, avec une
horloge simulée).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from visio.config import Settings
from visio.domain.clock import Clock
from visio.services import lifecycle, recording, segments, tracks, transcription, webhooks
from visio.services.egress import RecorderGateway
from visio.services.rooms import RoomGateway
from visio.services.storage import ObjectSizer, Storage

logger = logging.getLogger("visio.scheduler")


def run_once(
    session_factory: sessionmaker[Session],
    settings: Settings,
    gateway: RoomGateway,
    recorder: RecorderGateway,
    sender: webhooks.Sender,
    clock: Clock,
    storage: ObjectSizer | None = None,
    *,
    with_tracks: bool = True,
    with_deliveries: bool = True,
) -> None:
    """Un tour. Chaque étape est isolée : l'échec de l'une n'empêche pas les autres.

    `with_tracks=False` : le démarrage des pistes audio est fait ailleurs (`_tracks_forever`)."""

    def due_meetings(db: Session, now: datetime) -> int:
        return lifecycle.run_due_meetings(db, gateway, now=now, recorder=recorder)

    def jobs(db: Session, now: datetime) -> int:
        return lifecycle.run_jobs(db, gateway, now=now, recorder=recorder)

    def recordings(db: Session, now: datetime) -> int:
        started = recording.run_jobs(db, recorder, now=now)
        if with_tracks:
            started += tracks.run_jobs(db, recorder, now=now)  # pistes audio (16b)
        if storage is None:
            return started
        started += recording.reconcile(db, recorder, storage, now=now)
        return started + tracks.reconcile(db, recorder, storage, now=now)

    def transcriptions(db: Session, now: datetime) -> int:
        # Crée seulement la tâche (cheap) : le calcul est fait par le processus worker (16c).
        return transcription.enqueue_due(db, now=now)

    def deliveries(db: Session, now: datetime) -> int:
        return webhooks.deliver_due(db, settings, sender, now=now)

    steps = [due_meetings, jobs, recordings, transcriptions]
    if with_deliveries:
        steps.append(deliveries)
    for step in steps:
        with session_factory() as db:
            try:
                step(db, clock.now())
            except Exception:
                db.rollback()
                logger.exception("tour du planificateur en erreur")


async def run_forever(
    session_factory: sessionmaker[Session],
    settings: Settings,
    gateway: RoomGateway,
    recorder: RecorderGateway,
    sender: webhooks.Sender,
    clock: Clock,
    storage: ObjectSizer | None = None,
    *,
    interval: float,
) -> None:
    # Fusion des segments (14d) : boucle à part, car elle peut durer des minutes et ne doit pas
    # retarder la fin des créneaux ni les webhooks.
    merge_task = None
    if isinstance(storage, Storage):
        merge_task = asyncio.create_task(_merge_forever(session_factory, clock, storage, interval))
    tracks_task = asyncio.create_task(_tracks_forever(session_factory, recorder, clock, interval))
    deliveries_task = asyncio.create_task(
        _deliveries_forever(session_factory, settings, sender, clock, interval)
    )
    try:
        await _run_loop(
            session_factory, settings, gateway, recorder, sender, clock, storage, interval
        )
    finally:
        tracks_task.cancel()
        deliveries_task.cancel()
        if merge_task is not None:
            merge_task.cancel()


async def _merge_forever(
    session_factory: sessionmaker[Session], clock: Clock, storage: Storage, interval: float
) -> None:
    def merge_once() -> None:
        with session_factory() as db:
            try:
                recording.run_merge_jobs(
                    db, lambda key: segments.merge(storage, key), now=clock.now()
                )
            except Exception:
                db.rollback()
                logger.exception("fusion des segments en erreur")

    while True:
        await asyncio.to_thread(merge_once)
        await asyncio.sleep(max(interval, 2.0))


async def _deliveries_forever(
    session_factory: sessionmaker[Session],
    settings: Settings,
    sender: webhooks.Sender,
    clock: Clock,
    interval: float,
) -> None:
    """Livre les webhooks dans une boucle **à part** : un backend Eduvia lent ou injoignable
    (jusqu'à 10 s par appel) ne doit jamais retarder la coupure de fin de créneau."""

    def deliver_once() -> None:
        with session_factory() as db:
            try:
                webhooks.deliver_due(db, settings, sender, now=clock.now())
            except Exception:
                db.rollback()
                logger.exception("livraison des webhooks en erreur")

    while True:
        await asyncio.to_thread(deliver_once)
        await asyncio.sleep(max(interval, 0.5))


async def _tracks_forever(
    session_factory: sessionmaker[Session],
    recorder: RecorderGateway,
    clock: Clock,
    interval: float,
) -> None:
    """Démarre les pistes audio dans une boucle **à part** (étape 16d).

    Démarrer l'enregistrement de la réunion (Chrome + gabarit) bloque l'appel à Egress une dizaine
    de secondes. Dans la boucle commune, chaque piste attendait derrière lui : la transcription
    perdait les ~10 premières secondes de parole (mesuré par `uv run task chain-test`)."""

    def start_once() -> None:
        with session_factory() as db:
            try:
                tracks.run_jobs(db, recorder, now=clock.now())
            except Exception:
                db.rollback()
                logger.exception("démarrage des pistes audio en erreur")

    while True:
        await asyncio.to_thread(start_once)
        await asyncio.sleep(max(interval, 0.5))


async def _run_loop(
    session_factory: sessionmaker[Session],
    settings: Settings,
    gateway: RoomGateway,
    recorder: RecorderGateway,
    sender: webhooks.Sender,
    clock: Clock,
    storage: ObjectSizer | None,
    interval: float,
) -> None:
    while True:
        # Dans un fil : les appels HTTP (serveur de médias, webhooks) ne bloquent pas l'API.
        await asyncio.to_thread(
            run_once,
            session_factory,
            settings,
            gateway,
            recorder,
            sender,
            clock,
            storage,
            with_tracks=False,
            with_deliveries=False,
        )
        await asyncio.sleep(interval)
