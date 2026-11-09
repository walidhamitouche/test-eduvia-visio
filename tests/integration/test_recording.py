"""Étapes 14a et 14b : démarrer, arrêter puis conclure l'enregistrement d'une réunion.

Contre un faux Egress (`FakeRecorder`) et un faux serveur de médias : aucun Docker. Le vrai
Egress se vérifie à la main (`docker-dev.ps1 up`, une vraie réunion, `uv run task recordings-list`).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import timedelta
from typing import Any

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.integration.conftest import RecordingSender, meeting_body
from tests.integration.helpers import (
    at,
    claims_of,
    egress_ended_webhook,
    join,
    livekit_webhook,
    tick,
    utc_seconds,
)
from visio.config import Settings
from visio.db.models import (
    ArtifactKind,
    ArtifactRow,
    ArtifactStatus,
    JobRow,
    JobStatus,
    ParticipantSessionRow,
)
from visio.domain.clock import FixedClock
from visio.domain.errors import MediaServerError
from visio.services.egress import (
    RECORDING_LAYOUT,
    RECORDING_PRESET,
    EgressGateway,
    EgressInfo,
    FakeRecorder,
)
from visio.services.jobs import JOB_MAX_ATTEMPTS, JOB_RETRY_DELAYS
from visio.services.recording import (
    LOST_AFTER,
    REASON_LOST,
    REASON_NO_FILE,
    REASON_NO_TRACK,
    RECONCILE_GRACE,
    START_RECORDING,
)
from visio.services.rooms import FakeRoomGateway
from visio.services.storage import FakeStorage
from visio.services.webhooks import verify_signature
from visio.tools.recordings import format_size, render

KEY_PATTERN = re.compile(r"^meetings/mtg_[0-9a-f]{32}/recordings/[0-9a-f-]{36}\.mp4$")


@pytest.fixture
def meeting(
    client: TestClient, auth: dict[str, str], clock: FixedClock
) -> Callable[..., dict[str, Any]]:
    def _make(**overrides: Any) -> dict[str, Any]:
        response = client.post(
            "/v1/meetings",
            json=meeting_body(waiting_room=False, early_join_minutes=0, **overrides),
            headers=auth,
        )
        assert response.status_code == 201, response.text
        at(clock, "16:00:00")
        created: dict[str, Any] = response.json()
        return created

    return _make


def connect(
    client: TestClient, settings: Settings, url: str, when: str, sid: str = "PA_1"
) -> dict[str, Any]:
    body = join(client, url).json()
    claims = claims_of(settings, body["livekit_token"])
    response = livekit_webhook(
        client,
        settings,
        "participant_joined",
        claims,
        sid=sid,
        created_at=utc_seconds(when),
        joined_at=utc_seconds(when),
    )
    assert response.status_code == 200, response.text
    return claims


def artifacts(db: Session) -> list[ArtifactRow]:
    db.expire_all()
    return list(db.scalars(select(ArtifactRow)).all())


def start_jobs(db: Session) -> list[JobRow]:
    db.expire_all()
    return list(db.scalars(select(JobRow).where(JobRow.kind == START_RECORDING)).all())


# --- démarrage ------------------------------------------------------------------------------


def test_first_entry_prepares_one_pending_recording_with_an_opaque_key(
    client: TestClient,
    settings: Settings,
    db: Session,
    meeting: Callable[..., dict[str, Any]],
    recorder: FakeRecorder,
) -> None:
    created = meeting()
    assert artifacts(db) == []  # rien avant la première entrée
    connect(client, settings, created["organizer_urls"][0], "16:00:10")

    (artifact,) = artifacts(db)
    assert artifact.kind is ArtifactKind.RECORDING
    assert artifact.status is ArtifactStatus.PENDING
    assert artifact.storage_key is not None
    assert KEY_PATTERN.match(artifact.storage_key)
    assert artifact.storage_key.startswith(f"meetings/{created['id']}/recordings/")
    assert artifact.egress_id is None  # c'est la tâche qui démarre
    assert recorder.started == []  # pas pendant la requête du webhook LiveKit


def test_the_scheduler_starts_the_recording_once(
    client: TestClient,
    settings: Settings,
    db: Session,
    meeting: Callable[..., dict[str, Any]],
    recorder: FakeRecorder,
    rooms: FakeRoomGateway,
) -> None:
    created = meeting()
    claims = connect(client, settings, created["organizer_urls"][0], "16:00:10")
    tick(client)
    tick(client)
    tick(client)

    (artifact,) = artifacts(db)
    assert len(recorder.started) == 1
    room, key = recorder.started[0]
    assert room == claims["video"]["room"]
    assert key == artifact.storage_key
    assert artifact.egress_id == "EG_fake1"
    (job,) = start_jobs(db)
    assert job.status is JobStatus.DONE


def test_a_replayed_join_webhook_does_not_start_a_second_recording(
    client: TestClient,
    settings: Settings,
    db: Session,
    meeting: Callable[..., dict[str, Any]],
    recorder: FakeRecorder,
) -> None:
    created = meeting()
    claims = connect(client, settings, created["organizer_urls"][0], "16:00:10", sid="PA_1")
    tick(client)
    for sid in ("PA_1", "PA_2"):  # même sid rejoué, puis une autre personne
        livekit_webhook(
            client,
            settings,
            "participant_joined",
            claims,
            sid=sid,
            joined_at=utc_seconds("16:00:20"),
        )
        tick(client)
    assert len(recorder.started) == 1
    assert len(artifacts(db)) == 1
    assert len(start_jobs(db)) == 1


def test_recording_false_means_no_artifact_no_job_no_egress_call(
    client: TestClient,
    settings: Settings,
    db: Session,
    meeting: Callable[..., dict[str, Any]],
    recorder: FakeRecorder,
) -> None:
    created = meeting(recording=False)
    connect(client, settings, created["organizer_urls"][0], "16:00:10")
    tick(client)
    assert artifacts(db) == []
    assert start_jobs(db) == []
    assert recorder.started == []


# --- relances -------------------------------------------------------------------------------


def test_an_unavailable_egress_is_retried_until_it_answers(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    meeting: Callable[..., dict[str, Any]],
    recorder: FakeRecorder,
) -> None:
    created = meeting()
    connect(client, settings, created["organizer_urls"][0], "16:00:10")
    recorder.unavailable = True
    tick(client)

    (job,) = start_jobs(db)
    assert job.status is JobStatus.FAILED
    assert job.attempts == 1
    assert job.last_error is not None
    assert recorder.started == []

    clock.advance(timedelta(seconds=1))
    tick(client)  # trop tôt : la relance est dans 5 s
    assert start_jobs(db)[0].attempts == 1

    recorder.unavailable = False
    clock.advance(timedelta(seconds=10))
    tick(client)
    assert len(recorder.started) == 1
    assert artifacts(db)[0].egress_id == "EG_fake1"
    assert start_jobs(db)[0].status is JobStatus.DONE


def test_a_lost_answer_does_not_start_a_second_recording(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    meeting: Callable[..., dict[str, Any]],
    recorder: FakeRecorder,
) -> None:
    """Egress a démarré mais la réponse s'est perdue : l'essai suivant adopte l'existant."""
    created = meeting()
    connect(client, settings, created["organizer_urls"][0], "16:00:10")

    real_start = recorder.start_room_composite
    answers = iter([True, False])

    def start_then_lose_the_answer(room: str, key: str) -> EgressInfo:
        info = real_start(room, key)
        if next(answers):
            msg = "le serveur de médias ne répond pas (simulé)"
            raise MediaServerError(msg)
        return info

    recorder.start_room_composite = start_then_lose_the_answer  # type: ignore[method-assign]
    tick(client)
    assert len(recorder.started) == 1
    assert artifacts(db)[0].egress_id is None

    clock.advance(timedelta(seconds=10))
    tick(client)
    assert len(recorder.started) == 1  # pas de second enregistrement
    assert artifacts(db)[0].egress_id == "EG_fake1"  # l'existant a été adopté


def test_start_is_abandoned_after_the_last_attempt_and_the_artifact_fails(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    meeting: Callable[..., dict[str, Any]],
    recorder: FakeRecorder,
) -> None:
    created = meeting()
    connect(client, settings, created["organizer_urls"][0], "16:00:10")
    recorder.unavailable = True
    # On suit le calendrier de relance (5 s ... 1 h) sans sortir du créneau (16 h - 18 h).
    for attempt in range(JOB_MAX_ATTEMPTS):
        tick(client)
        if attempt < len(JOB_RETRY_DELAYS):
            clock.advance(JOB_RETRY_DELAYS[attempt] + timedelta(seconds=1))
    (job,) = start_jobs(db)
    assert job.status is JobStatus.ABANDONED
    assert job.attempts == JOB_MAX_ATTEMPTS
    (artifact,) = artifacts(db)
    assert artifact.status is ArtifactStatus.FAILED
    assert artifact.error is not None
    assert "démarrage impossible" in artifact.error


def write_lock_is_free(settings: Settings) -> bool:
    """Vrai si personne ne tient le verrou d'écriture de la base (sonde sur une autre connexion)."""
    path = settings.database_url.removeprefix("sqlite:///")
    with closing(sqlite3.connect(path, timeout=0.2)) as probe:
        try:
            probe.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            return False
        probe.rollback()
        return True


def test_the_database_is_not_write_locked_while_egress_is_being_called(
    client: TestClient,
    settings: Settings,
    meeting: Callable[..., dict[str, Any]],
    recorder: FakeRecorder,
) -> None:
    """Un Egress lent (jusqu'à 10 s) ne doit pas bloquer les écritures de l'API : constaté en E2E,
    la création d'une réunion échouait en « database is locked » pendant un démarrage."""
    created = meeting()
    connect(client, settings, created["organizer_urls"][0], "16:00:10")
    seen: list[bool] = []
    real_start = recorder.start_room_composite

    def start(room: str, key: str) -> EgressInfo:
        seen.append(write_lock_is_free(settings))
        return real_start(room, key)

    recorder.start_room_composite = start  # type: ignore[method-assign]
    tick(client)
    assert seen == [True]


def test_the_database_is_not_write_locked_while_stopping_egress_and_closing_the_room(
    client: TestClient,
    settings: Settings,
    clock: FixedClock,
    meeting: Callable[..., dict[str, Any]],
    recorder: FakeRecorder,
    rooms: FakeRoomGateway,
) -> None:
    created = meeting()
    connect(client, settings, created["organizer_urls"][0], "16:00:10")
    tick(client)
    seen: list[tuple[str, bool]] = []
    real_stop, real_delete = recorder.stop, rooms.delete_room

    def stop(egress_id: str) -> EgressInfo:
        seen.append(("stop", write_lock_is_free(settings)))
        return real_stop(egress_id)

    def delete_room(room: str) -> None:
        seen.append(("delete_room", write_lock_is_free(settings)))
        real_delete(room)

    recorder.stop = stop  # type: ignore[method-assign]
    rooms.delete_room = delete_room  # type: ignore[method-assign]
    at(clock, "18:00:01")
    tick(client)
    assert seen == [("stop", True), ("delete_room", True)]


# --- arrêt ----------------------------------------------------------------------------------


@pytest.fixture
def journal(recorder: FakeRecorder, rooms: FakeRoomGateway) -> list[str]:
    """Ordre des ordres donnés à Egress et au serveur de médias."""
    entries: list[str] = []
    recorder.journal = entries
    real_delete = rooms.delete_room

    def delete_room(room: str) -> None:
        entries.append("room.delete")
        real_delete(room)

    rooms.delete_room = delete_room  # type: ignore[method-assign]
    return entries


def test_ending_the_meeting_stops_the_recording_before_closing_the_room(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    db: Session,
    meeting: Callable[..., dict[str, Any]],
    recorder: FakeRecorder,
    journal: list[str],
) -> None:
    created = meeting()
    connect(client, settings, created["organizer_urls"][0], "16:00:10")
    tick(client)
    assert journal == ["recorder.start"]

    response = client.post(f"/v1/meetings/{created['id']}/end", headers=auth)
    assert response.status_code == 200
    assert journal == ["recorder.start", "recorder.stop", "room.delete"]
    assert recorder.stopped == ["EG_fake1"]
    assert artifacts(db)[0].status is ArtifactStatus.PENDING  # la suite est l'étape 14b


def test_the_slot_end_also_stops_the_recording_first(
    client: TestClient,
    settings: Settings,
    clock: FixedClock,
    meeting: Callable[..., dict[str, Any]],
    journal: list[str],
) -> None:
    created = meeting()
    connect(client, settings, created["organizer_urls"][0], "16:00:10")
    tick(client)
    at(clock, "18:00:01")
    tick(client)
    assert journal == ["recorder.start", "recorder.stop", "room.delete"]


def test_a_failing_stop_never_prevents_closing_the_room(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    meeting: Callable[..., dict[str, Any]],
    recorder: FakeRecorder,
    rooms: FakeRoomGateway,
    journal: list[str],
) -> None:
    created = meeting()
    connect(client, settings, created["organizer_urls"][0], "16:00:10")
    tick(client)
    recorder.failing_stop = True
    response = client.post(f"/v1/meetings/{created['id']}/end", headers=auth)
    assert response.status_code == 200
    assert journal == ["recorder.start", "recorder.stop", "room.delete"]
    assert len(rooms.called("delete_room")) == 1


def test_ending_before_the_recording_started_cancels_it(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    db: Session,
    meeting: Callable[..., dict[str, Any]],
    recorder: FakeRecorder,
) -> None:
    created = meeting()
    connect(client, settings, created["organizer_urls"][0], "16:00:10")
    # La réunion est close avant que le planificateur n'ait démarré l'enregistrement.
    assert client.post(f"/v1/meetings/{created['id']}/end", headers=auth).status_code == 200
    tick(client)

    assert recorder.started == []
    (artifact,) = artifacts(db)
    assert artifact.status is ArtifactStatus.FAILED
    assert artifact.error is not None
    assert "avant le démarrage" in artifact.error
    assert start_jobs(db)[0].status is JobStatus.DONE


def test_a_meeting_without_recording_never_calls_stop(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    meeting: Callable[..., dict[str, Any]],
    recorder: FakeRecorder,
    journal: list[str],
) -> None:
    created = meeting(recording=False)
    connect(client, settings, created["organizer_urls"][0], "16:00:10")
    tick(client)
    client.post(f"/v1/meetings/{created['id']}/end", headers=auth)
    assert journal == ["room.delete"]
    assert recorder.stopped == []


# --- l'enregistreur n'est pas une personne --------------------------------------------------


def test_the_recorder_joining_the_room_is_not_counted_in_the_attendance(
    client: TestClient,
    settings: Settings,
    db: Session,
    meeting: Callable[..., dict[str, Any]],
) -> None:
    created = meeting()
    claims = connect(client, settings, created["organizer_urls"][0], "16:00:10")
    room = claims["video"]["room"]
    response = livekit_webhook(
        client,
        settings,
        "participant_joined",
        None,
        sid="PA_egress",
        room=room,
        identity="EG_abcdef123456",
        joined_at=utc_seconds("16:00:12"),
    )
    assert response.json() == {"result": "not-a-person"}
    sessions = db.scalars(select(ParticipantSessionRow)).all()
    assert [s.participant_identity for s in sessions] == [claims["sub"]]
    # ... et il ne déclenche pas de second enregistrement ni de `meeting.started`.
    assert len(artifacts(db)) == 1


# --- passerelle Egress ----------------------------------------------------------------------


KEY = "devkey-" + "k" * 20
SECRET = "egress-test-secret-" + "x" * 40


TEMPLATE_URL = "http://api:8000/recording-template"


def gateway_with(handler: Callable[[httpx.Request], httpx.Response]) -> EgressGateway:
    return EgressGateway(
        "ws://livekit:7880",
        KEY,
        SECRET,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        template_url=TEMPLATE_URL,
    )


def test_start_room_composite_sends_a_composite_mp4_order_without_any_s3_secret() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"egressId": "EG_1", "status": "EGRESS_STARTING"})

    info = gateway_with(handler).start_room_composite("rm_abc", "meetings/mtg_1/recordings/x.mp4")
    (request,) = seen
    assert request.url.path == "/twirp/livekit.Egress/StartRoomCompositeEgress"
    body = json.loads(request.content)
    assert body["room_name"] == "rm_abc"
    assert body["layout"] == RECORDING_LAYOUT
    assert body["preset"] == RECORDING_PRESET
    assert body["custom_base_url"] == TEMPLATE_URL  # mise en page maison, toujours
    assert body["file_outputs"] == [
        {"file_type": "MP4", "filepath": "meetings/mtg_1/recordings/x.mp4"}
    ]
    assert "s3" not in request.content.decode().lower()  # les clés S3 restent côté Egress
    token = request.headers["authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, SECRET, algorithms=["HS256"])
    assert claims["video"] == {"roomRecord": True}
    assert (info.egress_id, info.status) == ("EG_1", "starting")


def test_start_room_composite_refuses_without_the_recording_template() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"egressId": "EG_1", "status": "EGRESS_STARTING"})

    gateway = EgressGateway(
        "ws://livekit:7880", KEY, SECRET,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )  # fmt: skip
    with pytest.raises(MediaServerError):
        gateway.start_room_composite("rm_abc", "meetings/mtg_1/recordings/x.mp4")
    assert seen == []  # aucun ordre n'est parti vers Egress


def test_list_can_filter_by_room_and_active() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"items": [{"egressId": "EG_9", "status": 1}]})

    found = gateway_with(handler).list(room_name="rm_abc", active=True)
    assert bodies == [{"room_name": "rm_abc", "active": True}]
    assert [(i.egress_id, i.status) for i in found] == [("EG_9", "active")]


# --- outil recordings-list ------------------------------------------------------------------


def test_recordings_list_shows_state_and_ignores_other_objects() -> None:
    lines = render(
        [
            ("meetings/mtg_1/recordings/a.mp4", 5_242_880),
            ("meetings/mtg_2/recordings/b.mp4", 900),
            ("healthcheck/egress-x.mp4", 1),
        ],
        {"meetings/mtg_1/recordings/a.mp4": ("pending", "EG_1")},
    )
    assert len(lines) == 2
    assert "5.0 Mo" in lines[0] and "[pending · EG_1]" in lines[0]
    assert "900 o" in lines[1] and "[absent de la base]" in lines[1]
    assert format_size(2048) == "2.0 Ko"


# --- 14b : fin, état et webhooks -------------------------------------------------------------


def sent_events(sender: RecordingSender, kind: str) -> list[dict[str, Any]]:
    """Corps des webhooks sortants déjà livrés pour un type d'événement."""
    bodies = [json.loads(body) for _, _, body in sender.calls]
    return [b for b in bodies if b["type"] == kind]


@pytest.fixture
def recorded(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    db: Session,
    clock: FixedClock,
    meeting: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Réunion enregistrée puis terminée par l'organisateur à 16 h 05 : l'artefact attend
    l'issue d'Egress (`pending`, avec son `egress_id`)."""
    created = meeting()
    connect(client, settings, created["organizer_urls"][0], "16:00:10")
    tick(client)
    at(clock, "16:05:00")
    assert client.post(f"/v1/meetings/{created['id']}/end", headers=auth).status_code == 200
    (artifact,) = artifacts(db)
    assert artifact.status is ArtifactStatus.PENDING
    assert artifact.egress_id == "EG_fake1"
    return {"meeting": created, "key": artifact.storage_key, "egress_id": artifact.egress_id}


def test_egress_ended_makes_the_recording_ready_and_announces_it_signed(
    client: TestClient,
    settings: Settings,
    db: Session,
    storage: FakeStorage,
    sender: RecordingSender,
    recorded: dict[str, Any],
) -> None:
    storage.sizes[recorded["key"]] = 48_000_000
    response = egress_ended_webhook(client, settings, recorded["egress_id"], duration_seconds=300)
    assert response.status_code == 200
    assert response.json() == {"result": "ready"}

    (artifact,) = artifacts(db)
    assert artifact.status is ArtifactStatus.READY
    assert artifact.size_bytes == 48_000_000
    assert artifact.error is None

    tick(client)
    (event,) = sent_events(sender, "recording.ready")
    assert event["meeting"]["metadata"] == {"eduvia_session_id": 1234}
    assert event["meeting"]["id"] == recorded["meeting"]["id"]
    assert event["data"] == {
        "recording_id": artifact.id,
        "size_bytes": 48_000_000,
        "duration_seconds": 300,
    }
    # Ni clé S3 ni URL dans le webhook (point 86) : la récupération passe par l'API.
    assert recorded["key"] not in json.dumps(event)
    _, headers, body = sender.calls[-1]
    assert verify_signature(
        settings.webhook_secret.get_secret_value(),
        body,
        timestamp=headers["X-Visio-Timestamp"],
        signature=headers["X-Visio-Signature"],
        now=float(headers["X-Visio-Timestamp"]),  # horloge simulée, pas celle de la machine
    )


def test_a_replayed_egress_ended_announces_the_recording_only_once(
    client: TestClient,
    settings: Settings,
    storage: FakeStorage,
    sender: RecordingSender,
    recorded: dict[str, Any],
) -> None:
    storage.sizes[recorded["key"]] = 1000
    first = egress_ended_webhook(client, settings, recorded["egress_id"])
    second = egress_ended_webhook(client, settings, recorded["egress_id"])
    assert first.json() == {"result": "ready"}
    assert second.json() == {"result": "unchanged"}
    tick(client)
    tick(client)
    assert len(sent_events(sender, "recording.ready")) == 1


def test_a_forced_egress_failure_gives_recording_failed_with_the_reason(
    client: TestClient,
    settings: Settings,
    db: Session,
    sender: RecordingSender,
    recorded: dict[str, Any],
) -> None:
    response = egress_ended_webhook(
        client, settings, recorded["egress_id"], status="EGRESS_FAILED", error="encoder crashed"
    )
    assert response.json() == {"result": "failed"}
    (artifact,) = artifacts(db)
    assert artifact.status is ArtifactStatus.FAILED
    assert artifact.error == "Egress a échoué : encoder crashed"

    tick(client)
    (event,) = sent_events(sender, "recording.failed")
    assert event["data"] == {
        "recording_id": artifact.id,
        "reason": "Egress a échoué : encoder crashed",
    }
    assert event["meeting"]["metadata"] == {"eduvia_session_id": 1234}
    assert sent_events(sender, "recording.ready") == []


def test_a_room_without_any_published_track_fails_with_a_readable_reason(
    client: TestClient,
    settings: Settings,
    db: Session,
    recorded: dict[str, Any],
) -> None:
    egress_ended_webhook(
        client,
        settings,
        recorded["egress_id"],
        status="EGRESS_ABORTED",
        error="Start signal not received",
    )
    (artifact,) = artifacts(db)
    assert artifact.status is ArtifactStatus.FAILED
    assert artifact.error == REASON_NO_TRACK


def test_a_complete_egress_without_a_file_on_the_storage_fails(
    client: TestClient,
    settings: Settings,
    db: Session,
    recorded: dict[str, Any],
) -> None:
    egress_ended_webhook(client, settings, recorded["egress_id"])  # rien dans le faux S3
    (artifact,) = artifacts(db)
    assert artifact.status is ArtifactStatus.FAILED
    assert artifact.error == REASON_NO_FILE


def test_an_unreachable_storage_leaves_the_recording_pending_until_it_answers(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    storage: FakeStorage,
    recorder: FakeRecorder,
    recorded: dict[str, Any],
) -> None:
    storage.unreachable = True
    egress_ended_webhook(client, settings, recorded["egress_id"])
    assert artifacts(db)[0].status is ArtifactStatus.PENDING

    # Le filet réessaie : toujours en panne, puis le S3 revient.
    recorder.finish(recorded["egress_id"])
    clock.advance(RECONCILE_GRACE + timedelta(seconds=5))
    tick(client)
    assert artifacts(db)[0].status is ArtifactStatus.PENDING
    storage.unreachable = False
    storage.sizes[recorded["key"]] = 2048
    tick(client)
    (artifact,) = artifacts(db)
    assert artifact.status is ArtifactStatus.READY
    assert artifact.size_bytes == 2048


def test_a_lost_egress_ended_is_recovered_by_the_scheduler(
    client: TestClient,
    db: Session,
    clock: FixedClock,
    storage: FakeStorage,
    recorder: FakeRecorder,
    sender: RecordingSender,
    recorded: dict[str, Any],
) -> None:
    # Egress a fini mais le webhook ne nous est jamais parvenu.
    recorder.finish(recorded["egress_id"])
    storage.sizes[recorded["key"]] = 5_000_000

    tick(client)  # trop tôt : le webhook peut encore arriver, on n'interroge pas Egress
    assert artifacts(db)[0].status is ArtifactStatus.PENDING

    clock.advance(RECONCILE_GRACE + timedelta(seconds=5))
    tick(client)
    (artifact,) = artifacts(db)
    assert artifact.status is ArtifactStatus.READY
    assert artifact.size_bytes == 5_000_000
    tick(client)  # la boîte d'envoi part au tour suivant
    assert len(sent_events(sender, "recording.ready")) == 1


def test_the_scheduler_catches_up_after_a_restart_without_losing_the_recording(
    client: TestClient,
    app: Any,
    db: Session,
    clock: FixedClock,
    storage: FakeStorage,
    recorder: FakeRecorder,
    recorded: dict[str, Any],
) -> None:
    """Un redémarrage de l'API ne garde rien en mémoire : tout se relit en base."""
    recorder.finish(recorded["egress_id"])
    storage.sizes[recorded["key"]] = 777
    with TestClient(app) as restarted:  # nouvelle vie de l'application, même base
        clock.advance(timedelta(minutes=10))
        tick(restarted)
    (artifact,) = artifacts(db)
    assert artifact.status is ArtifactStatus.READY
    assert artifact.size_bytes == 777


def test_an_egress_that_is_still_ending_is_left_alone(
    client: TestClient,
    db: Session,
    clock: FixedClock,
    recorder: FakeRecorder,
    recorded: dict[str, Any],
) -> None:
    clock.advance(RECONCILE_GRACE + timedelta(seconds=5))
    tick(client)  # Egress répond « en cours d'arrêt » (envoi du fichier) : on attend
    assert artifacts(db)[0].status is ArtifactStatus.PENDING


def test_an_egress_that_vanished_is_declared_lost_after_a_long_wait(
    client: TestClient,
    db: Session,
    clock: FixedClock,
    recorder: FakeRecorder,
    recorded: dict[str, Any],
) -> None:
    recorder.infos.clear()  # Egress n'a plus aucune trace de cet enregistrement
    clock.advance(LOST_AFTER - timedelta(minutes=1))
    tick(client)
    assert artifacts(db)[0].status is ArtifactStatus.PENDING
    clock.advance(timedelta(minutes=2))
    tick(client)
    (artifact,) = artifacts(db)
    assert artifact.status is ArtifactStatus.FAILED
    assert artifact.error == REASON_LOST


def test_an_unreachable_egress_is_not_an_outcome(
    client: TestClient,
    db: Session,
    clock: FixedClock,
    recorder: FakeRecorder,
    recorded: dict[str, Any],
) -> None:
    recorder.finish(recorded["egress_id"])
    recorder.unavailable = True

    def down(*_: Any, **__: Any) -> list[EgressInfo]:
        msg = "le serveur de médias ne répond pas (simulé)"
        raise MediaServerError(msg)

    recorder.list = down  # type: ignore[method-assign]
    clock.advance(timedelta(minutes=30))
    tick(client)
    assert artifacts(db)[0].status is ArtifactStatus.PENDING


def test_an_unknown_or_foreign_egress_changes_nothing(
    client: TestClient, settings: Settings, db: Session, recorded: dict[str, Any]
) -> None:
    response = egress_ended_webhook(client, settings, "EG_not_ours", status="EGRESS_FAILED")
    assert response.json() == {"result": "unknown-egress"}
    assert artifacts(db)[0].status is ArtifactStatus.PENDING


def test_egress_ended_needs_the_livekit_signature(
    client: TestClient, recorded: dict[str, Any]
) -> None:
    response = client.post(
        "/v1/livekit/webhook",
        content=b'{"event":"egress_ended","egressInfo":{"egressId":"EG_fake1","status":"EGRESS_FAILED"}}',
        headers={"Authorization": "Bearer faux"},
    )
    assert response.status_code == 401


def test_no_recording_event_without_the_recording_option(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    sender: RecordingSender,
    meeting: Callable[..., dict[str, Any]],
) -> None:
    created = meeting(recording=False)
    connect(client, settings, created["organizer_urls"][0], "16:00:10")
    tick(client)
    client.post(f"/v1/meetings/{created['id']}/end", headers=auth)
    tick(client)
    tick(client)
    kinds = {json.loads(body)["type"] for _, _, body in sender.calls}
    assert not {"recording.ready", "recording.failed"} & kinds


def test_failures_before_the_start_are_announced_too(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    sender: RecordingSender,
    meeting: Callable[..., dict[str, Any]],
) -> None:
    created = meeting()
    connect(client, settings, created["organizer_urls"][0], "16:00:10")
    assert client.post(f"/v1/meetings/{created['id']}/end", headers=auth).status_code == 200
    tick(client)
    (event,) = sent_events(sender, "recording.failed")
    assert "avant le démarrage" in event["data"]["reason"]


def test_egress_info_reads_the_duration_given_in_nanoseconds_as_a_string() -> None:
    from visio.services.egress import info_from_json

    info = info_from_json(
        {
            "egressId": "EG_1",
            "status": "EGRESS_COMPLETE",
            "fileResults": [{"filename": "a.mp4", "duration": "125500000000", "size": "10"}],
        }
    )
    assert info.status == "complete"
    assert info.duration_seconds == 125
    assert info_from_json({"egressId": "EG_2", "status": 3}).duration_seconds is None


# --- outil `recording-check` (vérification de bout en bout en une commande) -------------------


def verdict(db: Session, storage: FakeStorage, meeting_id: str, **kwargs: Any) -> Any:
    from visio.tools.recordingcheck import evaluate

    return evaluate(db, storage, meeting_id, **kwargs)


def test_recording_check_passes_on_a_delivered_ready_recording(
    client: TestClient,
    settings: Settings,
    db: Session,
    storage: FakeStorage,
    recorded: dict[str, Any],
) -> None:
    storage.sizes[recorded["key"]] = 4096
    egress_ended_webhook(client, settings, recorded["egress_id"])
    tick(client)  # livraison du webhook (le faux récepteur répond 200)
    report = verdict(db, storage, recorded["meeting"]["id"])
    assert not report.failed, report.lines
    assert not report.pending


def test_recording_check_flags_a_webhook_that_was_never_delivered(
    client: TestClient,
    settings: Settings,
    db: Session,
    storage: FakeStorage,
    sender: RecordingSender,
    recorded: dict[str, Any],
) -> None:
    from visio.services.webhooks import SendResult

    sender.responses = [SendResult(501, None)] * 10  # ex. le récepteur n'écoute pas là
    storage.sizes[recorded["key"]] = 4096
    egress_ended_webhook(client, settings, recorded["egress_id"])
    tick(client)
    report = verdict(db, storage, recorded["meeting"]["id"])
    assert report.failed
    assert any("non livré" in text and "501" in text for _, text in report.lines)


def test_recording_check_waits_while_the_outcome_is_pending(
    db: Session, storage: FakeStorage, recorded: dict[str, Any]
) -> None:
    report = verdict(db, storage, recorded["meeting"]["id"])
    assert report.failed
    assert report.pending  # l'outil, avec --wait, réessaie


def test_recording_check_detects_a_size_mismatch_with_the_storage(
    client: TestClient,
    settings: Settings,
    db: Session,
    storage: FakeStorage,
    recorded: dict[str, Any],
) -> None:
    storage.sizes[recorded["key"]] = 4096
    egress_ended_webhook(client, settings, recorded["egress_id"])
    tick(client)
    storage.sizes[recorded["key"]] = 1  # le fichier a changé après coup
    report = verdict(db, storage, recorded["meeting"]["id"])
    assert any("taille différente" in text for _, text in report.lines)


def test_recording_check_accepts_an_expected_failure(
    client: TestClient,
    settings: Settings,
    db: Session,
    storage: FakeStorage,
    recorded: dict[str, Any],
) -> None:
    egress_ended_webhook(
        client,
        settings,
        recorded["egress_id"],
        status="EGRESS_ABORTED",
        error="Start signal not received",
    )
    tick(client)
    assert verdict(db, storage, recorded["meeting"]["id"]).failed  # on attendait « ready »
    assert not verdict(db, storage, recorded["meeting"]["id"], expect="failed").failed


def test_recording_check_on_a_meeting_without_recording(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    db: Session,
    storage: FakeStorage,
    meeting: Callable[..., dict[str, Any]],
) -> None:
    created = meeting(recording=False)
    connect(client, settings, created["organizer_urls"][0], "16:00:10")
    tick(client)
    client.post(f"/v1/meetings/{created['id']}/end", headers=auth)
    tick(client)
    assert not verdict(db, storage, created["id"]).failed


def test_stopping_an_egress_that_already_finished_is_not_a_warning(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    db: Session,
    caplog: pytest.LogCaptureFixture,
    meeting: Callable[..., dict[str, Any]],
    recorder: FakeRecorder,
) -> None:
    created = meeting()
    connect(client, settings, created["organizer_urls"][0], "16:00:10")
    tick(client)
    recorder.failing_stop = True
    recorder.finish("EG_fake1")  # Egress s'est arrêté seul (dernier participant parti)
    with caplog.at_level("INFO", logger="visio.recording"):
        assert client.post(f"/v1/meetings/{created['id']}/end", headers=auth).status_code == 200
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("déjà terminé" in r.getMessage() for r in caplog.records)


def test_recording_check_waits_for_the_first_delivery_attempt(
    client: TestClient,
    settings: Settings,
    db: Session,
    storage: FakeStorage,
    recorded: dict[str, Any],
) -> None:
    storage.sizes[recorded["key"]] = 4096
    egress_ended_webhook(client, settings, recorded["egress_id"])  # écrit, pas encore envoyé
    report = verdict(db, storage, recorded["meeting"]["id"])
    assert report.pending  # avec --wait, l'outil réessaie au lieu de conclure trop tôt
    tick(client)
    report = verdict(db, storage, recorded["meeting"]["id"])
    assert not report.pending
    assert not report.failed, report.lines
