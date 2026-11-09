"""Échecs visibles et rejouables (étape 17c).

Ce qui peut rester en échec, et ce que « rejouer » veut dire :

- **Tâche** (`jobs`) `failed` (un essai a échoué, le suivant est programmé) ou `abandoned` (les
  six essais ont échoué) : on remet les tentatives à zéro et l'heure à maintenant. Le planificateur
  (ou le worker de transcription) la reprend au tour suivant. **Aucune ligne n'est créée** : rejouer
  deux fois ne fait donc jamais deux tâches.
- **Webhook** `abandoned` (ou en relance après un échec) : même chose. L'événement garde son
  identifiant, donc le récepteur peut dédoublonner (`X-Visio-Event-Id`). Un webhook déjà livré
  n'est jamais rejoué.
- **Artefact en échec** (enregistrement, transcription, piste audio) : il n'a pas de rejeu propre,
  c'est sa tâche qui est rejouée (et l'artefact repasse à `pending`). Un échec sans tâche à
  rejouer (Egress a échoué pendant la réunion, réunion finie avant le démarrage) est définitif :
  la réunion en direct ne se rejoue pas.

Tout est écrit pour s'exécuter dans un processus à part (les commandes `jobs-list` et `jobs-retry`),
pendant que l'API tourne. Les passages d'état sont des `UPDATE ... WHERE status = ...` : si deux
rejeux se croisent, un seul agit, l'autre voit « déjà en attente ».
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from visio.db.models import (
    ArtifactKind,
    ArtifactRow,
    ArtifactStatus,
    AudioTrackRow,
    JobRow,
    JobStatus,
    MeetingRow,
    WebhookDeliveryRow,
    WebhookStatus,
)
from visio.domain.meeting import MeetingState
from visio.services.lifecycle import CLOSE_ROOM
from visio.services.recording import MERGE_RECORDING, START_RECORDING
from visio.services.tracks import START_TRACK
from visio.services.transcription import TRANSCRIBE

logger = logging.getLogger("visio.replay")

MAX_ERROR_LENGTH = 200

JOB = "tâche"
WEBHOOK = "webhook"
ARTIFACT = "artefact"
TRACK = "piste audio"

# Statuts d'une tâche qu'on peut rejouer, et d'un webhook (hors « en relance » : voir `_retrying`).
JOB_REPLAYABLE = (JobStatus.FAILED, JobStatus.ABANDONED)


class Result(StrEnum):
    REQUEUED = "remis en file"
    ALREADY = "déjà en attente"  # rien à faire : pas de doublon
    REFUSED = "refusé"
    NOT_FOUND = "introuvable"


@dataclass(frozen=True)
class Outcome:
    identifier: str
    result: Result
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.result in (Result.REQUEUED, Result.ALREADY)


@dataclass(frozen=True)
class Failure:
    """Une ligne de `jobs-list`."""

    category: str  # JOB, WEBHOOK, ARTIFACT, TRACK
    identifier: str
    label: str  # type de tâche, type d'événement, `recording` / `transcript` / `audio`
    meeting_id: str | None
    status: str
    attempts: int | None
    next_attempt_at: datetime | None
    error: str | None
    replay: str | None  # identifiant à passer à `jobs-retry`, ou `None` si non rejouable
    note: str  # pourquoi non rejouable, ou information utile
    abandoned: bool = False  # plus personne ne s'en occupe : c'est ce que rejoue `--all`


def short(text: str | None) -> str | None:
    """Erreur sur une ligne, de longueur bornée (elle peut venir d'un serveur distant)."""
    if not text:
        return None
    line = " ".join(text.split())
    return line if len(line) <= MAX_ERROR_LENGTH else line[: MAX_ERROR_LENGTH - 1] + "…"


# --- lecture -----------------------------------------------------------------------------------


def _retrying(row: WebhookDeliveryRow) -> bool:
    """Webhook encore `pending` mais déjà en échec : une relance est programmée."""
    return row.status is WebhookStatus.PENDING and row.attempts > 0


def _artifact_job(db: Session, kind: str, artifact: ArtifactRow) -> JobRow | None:
    """La tâche qui produit cet artefact (la plus récente), ou `None`."""
    if artifact.kind is ArtifactKind.TRANSCRIPT:
        query = select(JobRow).where(
            JobRow.kind == TRANSCRIBE, JobRow.meeting_id == artifact.meeting_id
        )
    else:
        query = select(JobRow).where(
            JobRow.kind == kind, JobRow.payload["artifact_id"].as_string() == artifact.id
        )
    return db.scalars(query.order_by(JobRow.created_at.desc())).first()


def _recording_job(db: Session, artifact: ArtifactRow) -> JobRow | None:
    """Pour un enregistrement : la fusion si elle existe, sinon le démarrage."""
    return _artifact_job(db, MERGE_RECORDING, artifact) or _artifact_job(
        db, START_RECORDING, artifact
    )


def list_failures(db: Session, *, meeting_id: str | None = None) -> list[Failure]:
    """Tout ce qui est en échec, tâches d'abord. Lecture seule."""
    found: list[Failure] = []

    jobs = select(JobRow).where(JobRow.status.in_(JOB_REPLAYABLE)).order_by(JobRow.created_at)
    if meeting_id:
        jobs = jobs.where(JobRow.meeting_id == meeting_id)
    for job in db.scalars(jobs):
        found.append(
            Failure(
                category=JOB,
                identifier=job.id,
                label=job.kind,
                meeting_id=job.meeting_id,
                status=(
                    "abandonnée"
                    if job.status is JobStatus.ABANDONED
                    else "en échec, nouvel essai programmé"
                ),
                attempts=job.attempts,
                next_attempt_at=job.run_at if job.status is JobStatus.FAILED else None,
                error=short(job.last_error),
                replay=job.id,
                note="",
                abandoned=job.status is JobStatus.ABANDONED,
            )
        )

    hooks = (
        select(WebhookDeliveryRow)
        .where(
            (WebhookDeliveryRow.status == WebhookStatus.ABANDONED)
            | (
                (WebhookDeliveryRow.status == WebhookStatus.PENDING)
                & (WebhookDeliveryRow.attempts > 0)
            )
        )
        .order_by(WebhookDeliveryRow.created_at)
    )
    if meeting_id:
        hooks = hooks.where(WebhookDeliveryRow.meeting_id == meeting_id)
    for hook in db.scalars(hooks):
        abandoned = hook.status is WebhookStatus.ABANDONED
        found.append(
            Failure(
                category=WEBHOOK,
                identifier=hook.id,
                label=hook.event_type,
                meeting_id=hook.meeting_id,
                status="abandonné" if abandoned else "en échec, nouvelle livraison programmée",
                attempts=hook.attempts,
                next_attempt_at=None if abandoned else hook.next_attempt_at,
                error=short(hook.last_error),
                replay=hook.id,
                note="",
                abandoned=abandoned,
            )
        )

    found.extend(_failed_artifacts(db, meeting_id))
    return found


def _failed_artifacts(db: Session, meeting_id: str | None) -> list[Failure]:
    found: list[Failure] = []
    query = select(ArtifactRow).where(ArtifactRow.status == ArtifactStatus.FAILED)
    if meeting_id:
        query = query.where(ArtifactRow.meeting_id == meeting_id)
    for artifact in db.scalars(query.order_by(ArtifactRow.created_at)):
        job = (
            _artifact_job(db, TRANSCRIBE, artifact)
            if artifact.kind is ArtifactKind.TRANSCRIPT
            else _recording_job(db, artifact)
        )
        replay = job.id if job is not None and job.status is JobStatus.ABANDONED else None
        found.append(
            Failure(
                category=ARTIFACT,
                identifier=artifact.id,
                label=artifact.kind.value,
                meeting_id=artifact.meeting_id,
                status="en échec",
                attempts=job.attempts if job is not None else None,
                next_attempt_at=None,
                error=short(artifact.error),
                replay=replay,
                note=(
                    ""
                    if replay
                    else "échec définitif : le direct ne se rejoue pas"
                    if job is None or job.status is JobStatus.DONE
                    else "sa tâche est encore en cours de relance"
                ),
            )
        )
    tracks = select(AudioTrackRow).where(AudioTrackRow.status == ArtifactStatus.FAILED)
    if meeting_id:
        tracks = tracks.where(AudioTrackRow.meeting_id == meeting_id)
    for track in db.scalars(tracks.order_by(AudioTrackRow.created_at)):
        job = _track_job(db, track)
        replay = job.id if job is not None and job.status is JobStatus.ABANDONED else None
        found.append(
            Failure(
                category=TRACK,
                identifier=track.id,
                label=f"audio de {track.display_name}",
                meeting_id=track.meeting_id,
                status="en échec",
                attempts=job.attempts if job is not None else None,
                next_attempt_at=None,
                error=short(track.error),
                replay=replay,
                note="" if replay else "échec définitif : le direct ne se rejoue pas",
            )
        )
    return found


def _track_job(db: Session, track: AudioTrackRow) -> JobRow | None:
    return db.scalars(
        select(JobRow)
        .where(JobRow.kind == START_TRACK, JobRow.payload["track_id"].as_string() == track.id)
        .order_by(JobRow.created_at.desc())
    ).first()


# --- rejeu -------------------------------------------------------------------------------------


def retry(db: Session, identifier: str, *, now: datetime) -> Outcome:
    """Rejoue une tâche (`job_…`), un webhook (`evt_…`) ou un artefact (`art_…` / `trk_…`).

    Valide la transaction. Ne lève pas : le résultat dit ce qui s'est passé."""
    db.expire_all()  # décider d'après l'état réel de la base, pas d'une lecture ancienne
    if identifier.startswith("job_"):
        outcome = _retry_job(db, identifier, now=now)
    elif identifier.startswith("evt_"):
        outcome = _retry_webhook(db, identifier, now=now)
    elif identifier.startswith("art_"):
        outcome = _retry_artifact(db, identifier, now=now)
    elif identifier.startswith("trk_"):
        outcome = _retry_track(db, identifier, now=now)
    else:
        return Outcome(identifier, Result.NOT_FOUND, "identifiant inconnu (job_, evt_, art_, trk_)")
    if outcome.result is Result.REQUEUED:
        db.commit()
        logger.info("rejeu demandé", extra={"replayed": identifier})
    else:
        db.rollback()
    return outcome


def retry_all(db: Session, *, now: datetime, meeting_id: str | None = None) -> list[Outcome]:
    """Rejoue tout ce qui est **abandonné** (tâches, puis webhooks). Ce qui est seulement en
    relance se rattrape tout seul. Les artefacts suivent leurs tâches : on ne les rejoue pas à
    part, ce serait compter deux fois."""
    outcomes: list[Outcome] = []
    for failure in list_failures(db, meeting_id=meeting_id):
        if failure.category in (JOB, WEBHOOK) and failure.abandoned and failure.replay:
            outcomes.append(retry(db, failure.replay, now=now))
    return outcomes


def _retry_artifact(db: Session, identifier: str, *, now: datetime) -> Outcome:
    artifact = db.get(ArtifactRow, identifier)
    if artifact is None:
        return Outcome(identifier, Result.NOT_FOUND, "artefact inconnu")
    job = (
        _artifact_job(db, TRANSCRIBE, artifact)
        if artifact.kind is ArtifactKind.TRANSCRIPT
        else _recording_job(db, artifact)
    )
    if job is None or job.status is JobStatus.DONE:
        return Outcome(
            identifier,
            Result.REFUSED,
            "échec définitif : aucune tâche à rejouer (le direct est passé)",
        )
    return _retry_job(db, job.id, now=now, asked=identifier)


def _retry_track(db: Session, identifier: str, *, now: datetime) -> Outcome:
    track = db.get(AudioTrackRow, identifier)
    if track is None:
        return Outcome(identifier, Result.NOT_FOUND, "piste inconnue")
    job = _track_job(db, track)
    if job is None or job.status is JobStatus.DONE:
        return Outcome(
            identifier,
            Result.REFUSED,
            "échec définitif : aucune tâche à rejouer (le direct est passé)",
        )
    return _retry_job(db, job.id, now=now, asked=identifier)


def _retry_job(db: Session, identifier: str, *, now: datetime, asked: str | None = None) -> Outcome:
    shown = asked or identifier
    job = db.get(JobRow, identifier)
    if job is None:
        return Outcome(shown, Result.NOT_FOUND, "tâche inconnue")
    if job.status is JobStatus.DONE:
        return Outcome(shown, Result.REFUSED, "la tâche est déjà terminée")
    if job.status is JobStatus.PENDING:
        return Outcome(shown, Result.ALREADY, f"la tâche {job.id} attend déjà son tour")
    refusal = _prepare_job(db, job)
    if refusal is not None:
        return Outcome(shown, Result.REFUSED, refusal)
    reset = cast(
        "CursorResult[Any]",
        db.execute(
            update(JobRow)
            .where(JobRow.id == job.id, JobRow.status.in_(JOB_REPLAYABLE))
            .values(status=JobStatus.PENDING, attempts=0, run_at=now)
        ),
    )
    if reset.rowcount != 1:  # un autre rejeu (ou le planificateur) est passé avant
        return Outcome(shown, Result.ALREADY, f"la tâche {job.id} attend déjà son tour")
    return Outcome(shown, Result.REQUEUED, f"tâche {job.id} ({job.kind})")


def _prepare_job(db: Session, job: JobRow) -> str | None:
    """Remet en état ce que l'échec définitif avait figé. Renvoie un motif de refus, ou `None`.

    Seule une tâche **abandonnée** a figé quelque chose (un artefact `failed`) ; une tâche
    simplement en échec n'a rien changé."""
    if job.status is not JobStatus.ABANDONED:
        return None
    meeting = db.get(MeetingRow, job.meeting_id) if job.meeting_id else None
    # Démarrer un enregistrement n'a de sens que dans une réunion encore en cours.
    if job.kind in (START_RECORDING, START_TRACK) and (
        meeting is None or meeting.state is not MeetingState.LIVE
    ):
        return "la réunion n'est plus en cours : l'enregistrement ne peut plus démarrer"
    if job.kind in (START_RECORDING, MERGE_RECORDING):
        artifact = db.get(ArtifactRow, str(job.payload.get("artifact_id", "")))
        if artifact is None:
            return "l'enregistrement a été supprimé"
        if artifact.status is ArtifactStatus.FAILED:
            artifact.status = ArtifactStatus.PENDING
            artifact.error = None
            artifact.size_bytes = None
    elif job.kind == START_TRACK:
        track = db.get(AudioTrackRow, str(job.payload.get("track_id", "")))
        if track is None:
            return "la piste audio a été supprimée"
        if track.status is ArtifactStatus.FAILED:
            track.status = ArtifactStatus.PENDING
            track.error = None
    elif job.kind == TRANSCRIBE and job.meeting_id:
        rows = db.scalars(
            select(ArtifactRow).where(
                ArtifactRow.meeting_id == job.meeting_id,
                ArtifactRow.kind == ArtifactKind.TRANSCRIPT,
                ArtifactRow.status == ArtifactStatus.FAILED,
            )
        ).all()
        for artifact in rows:
            artifact.status = ArtifactStatus.PENDING
            artifact.error = None
            artifact.size_bytes = None
    elif job.kind != CLOSE_ROOM:
        return f"type de tâche inconnu : {job.kind}"
    return None


def _retry_webhook(db: Session, identifier: str, *, now: datetime) -> Outcome:
    hook = db.get(WebhookDeliveryRow, identifier)
    if hook is None:
        return Outcome(identifier, Result.NOT_FOUND, "webhook inconnu")
    if hook.status is WebhookStatus.DELIVERED:
        return Outcome(identifier, Result.REFUSED, "déjà livré : pas de second envoi")
    if hook.status is WebhookStatus.PENDING and hook.attempts == 0:
        return Outcome(identifier, Result.ALREADY, "il attend déjà sa première livraison")
    reset = cast(
        "CursorResult[Any]",
        db.execute(
            update(WebhookDeliveryRow)
            .where(
                WebhookDeliveryRow.id == identifier,
                WebhookDeliveryRow.status.in_([WebhookStatus.ABANDONED, WebhookStatus.PENDING]),
                WebhookDeliveryRow.attempts > 0,
            )
            .values(status=WebhookStatus.PENDING, attempts=0, next_attempt_at=now)
        ),
    )
    if reset.rowcount != 1:
        return Outcome(identifier, Result.ALREADY, "il attend déjà sa livraison")
    return Outcome(identifier, Result.REQUEUED, f"{hook.event_type}, même identifiant d'événement")


def retry_many(db: Session, identifiers: Sequence[str], *, now: datetime) -> list[Outcome]:
    return [retry(db, identifier, now=now) for identifier in identifiers]
