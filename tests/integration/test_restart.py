"""Reprise après redémarrage de l'API (étape 17d).

Un redémarrage ne garde rien en mémoire : tout se relit en base. On le simule en ouvrant une
**nouvelle vie de l'application** (`TestClient(app)`) sur la même base, les mêmes faux serveurs et
la même horloge. Trois scénarios du plan :
1. redémarrage pendant une réunion : elle est maintenue, puis coupée à l'heure ;
2. redémarrage après la fin d'un enregistrement : le fichier est conservé et référencé ;
3. traitement interrompu : il reprend, sans doublon.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.integration.conftest import RecordingSender, meeting_body
from tests.integration.helpers import at, claims_of, join, livekit_webhook, tick, utc_seconds
from visio.config import Settings
from visio.db.models import ArtifactKind, ArtifactRow, ArtifactStatus, JobRow, JobStatus
from visio.domain.clock import FixedClock
from visio.services.egress import FakeRecorder
from visio.services.jobs import JOB_RETRY_DELAYS
from visio.services.recording import MERGE_RECORDING, run_merge_jobs
from visio.services.rooms import FakeRoomGateway
from visio.services.storage import FakeStorage

Meeting = Callable[..., dict[str, Any]]


@pytest.fixture
def meeting(client: TestClient, auth: dict[str, str], clock: FixedClock) -> Meeting:
    def _make(**overrides: Any) -> dict[str, Any]:
        body = meeting_body(waiting_room=False, early_join_minutes=0, **overrides)
        response = client.post("/v1/meetings", json=body, headers=auth)
        assert response.status_code == 201, response.text
        at(clock, "16:00:00")
        created: dict[str, Any] = response.json()
        return created

    return _make


def connect(client: TestClient, settings: Settings, url: str, when: str = "16:00:10") -> None:
    claims = claims_of(settings, join(client, url).json()["livekit_token"])
    response = livekit_webhook(
        client,
        settings,
        "participant_joined",
        claims,
        created_at=utc_seconds(when),
        joined_at=utc_seconds(when),
    )
    assert response.status_code == 200, response.text


def status_of(client: TestClient, auth: dict[str, str], meeting_id: str) -> str:
    response = client.get(f"/v1/meetings/{meeting_id}", headers=auth)
    assert response.status_code == 200, response.text
    return str(response.json()["status"])


def event_types(sender: RecordingSender) -> list[str]:
    return [headers["X-Visio-Event-Type"] for _, headers, _ in sender.calls]


def room_closed(rooms: FakeRoomGateway) -> bool:
    return any(kind == "delete_room" for kind, _ in rooms.calls)


def artifacts(db: Session) -> list[ArtifactRow]:
    db.expire_all()
    return list(db.scalars(select(ArtifactRow)).all())


# --- 1. redémarrage pendant une réunion ---------------------------------------------------------


def test_a_restart_during_a_meeting_keeps_it_open_then_cuts_it_on_time(
    client: TestClient,
    app: FastAPI,
    auth: dict[str, str],
    settings: Settings,
    clock: FixedClock,
    meeting: Meeting,
    rooms: FakeRoomGateway,
    recorder: FakeRecorder,
    sender: RecordingSender,
) -> None:
    created = meeting()
    connect(client, settings, created["organizer_urls"][0])
    tick(client)
    assert status_of(client, auth, created["id"]) == "live"
    assert len(recorder.started) == 1

    at(clock, "16:30:00")
    with TestClient(app) as restarted:  # l'API redémarre en pleine réunion
        tick(restarted)
        # maintenue : toujours en cours, on peut encore entrer, ni salle fermée ni 2e enregistrement
        assert status_of(restarted, auth, created["id"]) == "live"
        assert join(restarted, created["participant_url"], display_name="Léa").status_code == 200
        assert not room_closed(rooms)
        assert len(recorder.started) == 1

        # coupée à l'heure par la nouvelle vie de l'application, sans personne pour la déclencher
        at(clock, "18:00:01")
        tick(restarted)
        assert status_of(restarted, auth, created["id"]) == "ended"
        assert room_closed(rooms)
        assert join(restarted, created["participant_url"], display_name="Léa").status_code >= 400
        tick(restarted)
        assert event_types(sender).count("meeting.ended") == 1


def test_a_restart_before_the_slot_end_does_not_cut_the_meeting_early(
    client: TestClient,
    app: FastAPI,
    auth: dict[str, str],
    clock: FixedClock,
    meeting: Meeting,
    rooms: FakeRoomGateway,
) -> None:
    created = meeting()
    at(clock, "17:59:59")
    with TestClient(app) as restarted:
        tick(restarted)
        assert status_of(restarted, auth, created["id"]) != "ended"
        assert not room_closed(rooms)


def test_a_close_order_interrupted_by_a_crash_is_resumed_after_the_restart(
    client: TestClient,
    app: FastAPI,
    auth: dict[str, str],
    settings: Settings,
    db: Session,
    clock: FixedClock,
    meeting: Meeting,
    rooms: FakeRoomGateway,
) -> None:
    """Fin de créneau, mais le serveur de médias ne répond pas : l'ordre de fermeture reste en
    base. Après le redémarrage il est repris, une seule fois."""
    created = meeting()
    connect(client, settings, created["organizer_urls"][0])
    at(clock, "18:00:01")
    rooms.failing = True
    tick(client)
    assert status_of(client, auth, created["id"]) == "ended"
    assert not room_closed(rooms)  # la salle est encore ouverte côté médias

    rooms.failing = False
    clock.advance(JOB_RETRY_DELAYS[0] + timedelta(seconds=1))
    with TestClient(app) as restarted:
        tick(restarted)
        tick(restarted)
    assert sum(1 for kind, _ in rooms.calls if kind == "delete_room") == 1
    pending = db.scalars(select(JobRow).where(JobRow.status != JobStatus.DONE)).all()
    assert [job for job in pending if job.kind == "close_room"] == []


# --- 2. redémarrage après la fin d'un enregistrement --------------------------------------------


@pytest.fixture
def recorded(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    db: Session,
    clock: FixedClock,
    meeting: Meeting,
) -> dict[str, Any]:
    created = meeting()
    connect(client, settings, created["organizer_urls"][0])
    tick(client)
    at(clock, "16:05:00")
    assert client.post(f"/v1/meetings/{created['id']}/end", headers=auth).status_code == 200
    (artifact,) = artifacts(db)
    assert artifact.status is ArtifactStatus.PENDING
    assert artifact.egress_id is not None
    return {"meeting": created, "key": artifact.storage_key, "egress_id": artifact.egress_id}


def test_a_recording_finished_during_the_downtime_is_kept_and_referenced(
    app: FastAPI,
    auth: dict[str, str],
    db: Session,
    clock: FixedClock,
    storage: FakeStorage,
    recorder: FakeRecorder,
    sender: RecordingSender,
    recorded: dict[str, Any],
) -> None:
    # Egress termine pendant que l'API est arrêtée : le webhook LiveKit est perdu.
    recorder.finish(recorded["egress_id"])
    storage.sizes[recorded["key"]] = 48_000_000

    clock.advance(timedelta(minutes=10))
    with TestClient(app) as restarted:
        tick(restarted)
        tick(restarted)
        (artifact,) = artifacts(db)
        assert artifact.status is ArtifactStatus.READY
        assert artifact.size_bytes == 48_000_000
        assert artifact.storage_key == recorded["key"]  # même objet, rien n'a été recréé
        listing = restarted.get(
            f"/v1/meetings/{recorded['meeting']['id']}/recordings", headers=auth
        )
        assert listing.status_code == 200
        assert artifact.id in json.dumps(listing.json())

    # Un second redémarrage ne change rien et n'annonce pas l'enregistrement une deuxième fois.
    with TestClient(app) as again:
        tick(again)
    assert event_types(sender).count("recording.ready") == 1
    assert storage.sizes[recorded["key"]] == 48_000_000
    assert len(artifacts(db)) == 1


# --- 3. traitement interrompu : il reprend ------------------------------------------------------


def test_a_merge_interrupted_by_a_crash_resumes_after_the_restart_without_duplicate(
    client: TestClient,
    app: FastAPI,
    db: Session,
    clock: FixedClock,
    meeting: Meeting,
    sender: RecordingSender,
) -> None:
    created = meeting()
    artifact = ArtifactRow(
        meeting_id=created["id"],
        kind=ArtifactKind.RECORDING,
        status=ArtifactStatus.PENDING,
        storage_key=f"meetings/{created['id']}/recordings/x.mp4",
    )
    db.add(artifact)
    db.flush()
    job = JobRow(
        kind=MERGE_RECORDING,
        meeting_id=created["id"],
        payload={"artifact_id": artifact.id, "duration_seconds": 60.0},
        status=JobStatus.PENDING,
    )
    db.add(job)
    db.commit()

    def crash(key: str) -> int:
        raise RuntimeError("le processus s'arrête en pleine fusion")

    with pytest.raises(RuntimeError):
        run_merge_jobs(db, crash, now=clock.now())
    db.rollback()
    db.expire_all()
    assert job.status is JobStatus.PENDING  # ni terminée ni abandonnée : elle reste due
    assert artifact.status is ArtifactStatus.PENDING

    with TestClient(app) as restarted:
        session = restarted.app.state.session_factory()  # type: ignore[attr-defined]
        with session:
            assert run_merge_jobs(session, lambda key: 4096, now=clock.now()) == 1
            assert run_merge_jobs(session, lambda key: 4096, now=clock.now()) == 0
        tick(restarted)
        tick(restarted)
    db.expire_all()
    assert (job.status, artifact.status, artifact.size_bytes) == (
        JobStatus.DONE,
        ArtifactStatus.READY,
        4096,
    )
    assert event_types(sender).count("recording.ready") == 1
