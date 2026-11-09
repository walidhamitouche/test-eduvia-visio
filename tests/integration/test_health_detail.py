"""Santé détaillée (étape 17b) : `/health` public minimal, `/v1/health` derrière la clé d'API.

Les dépendances (LiveKit, S3) sont des faux qu'on coupe à volonté ; la base est coupée en
remplaçant la fabrique de sessions. L'âge des tâches se lit sur l'horloge simulée.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tests.conftest import SECRET_API_KEY
from visio import __version__
from visio.config import Settings
from visio.db.models import (
    ArtifactKind,
    ArtifactRow,
    ArtifactStatus,
    JobRow,
    JobStatus,
    WebhookDeliveryRow,
    WebhookStatus,
)
from visio.domain.clock import FixedClock
from visio.domain.errors import MediaServerError
from visio.services import health
from visio.services.recording import MERGE_RECORDING
from visio.services.rooms import FakeRoomGateway, LiveKitGateway
from visio.services.storage import FakeStorage
from visio.services.transcription import TRANSCRIBE

HEALTH = "/v1/health"


# --- 1. la route publique ----------------------------------------------------------------------


def test_the_public_route_only_says_the_service_is_alive(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_the_public_route_does_not_depend_on_any_dependency(
    client: TestClient,
    app: FastAPI,
    rooms: FakeRoomGateway,
    storage: FakeStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une sonde Docker / Coolify ne doit pas redémarrer l'API parce que le S3 est en panne."""

    def broken() -> Session:
        raise OperationalError("SELECT 1", {}, Exception("base cassée"))

    rooms.failing = True
    storage.unreachable = True
    monkeypatch.setattr(app.state, "session_factory", broken)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- 2. l'accès à la route détaillée -----------------------------------------------------------


def test_the_detail_needs_the_api_key(client: TestClient) -> None:
    response = client.get(HEALTH)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_a_wrong_api_key_is_refused(client: TestClient) -> None:
    response = client.get(HEALTH, headers={"Authorization": "Bearer " + "x" * 40})
    assert response.status_code == 401


def test_the_detail_requires_https_in_production(
    client: TestClient, app: FastAPI, settings: Settings, auth: dict[str, str]
) -> None:
    app.state.settings = settings.model_copy(update={"app_env": "production"})
    refused = client.get(HEALTH, headers=auth)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "https_required"
    behind_proxy = client.get(HEALTH, headers={**auth, "X-Forwarded-Proto": "https"})
    assert behind_proxy.status_code == 200


# --- 3. tout va bien ---------------------------------------------------------------------------


def test_everything_up_gives_200_with_the_full_detail(
    client: TestClient, auth: dict[str, str], clock: FixedClock
) -> None:
    response = client.get(HEALTH, headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["checked_at"].startswith("2027-01-02T10:00:00+01:00")  # heure de Paris
    for name in ("database", "livekit", "storage"):
        assert body["checks"][name]["status"] == "ok"
        assert body["checks"][name]["detail"] is None
        assert body["checks"][name]["latency_ms"] >= 0
    assert body["queue"] == {
        "status": "ok",
        "jobs": {
            "pending": 0,
            "failed": 0,
            "abandoned": 0,
            "oldest_due_age_seconds": None,
            "late": False,
        },
        "webhooks": {"pending": 0, "abandoned": 0, "oldest_due_age_seconds": None, "late": False},
        "artifacts_failed": 0,
    }


def test_the_route_is_in_the_openapi_with_its_503(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    operation = spec["paths"][HEALTH]["get"]
    assert {"200", "401", "503"} <= set(operation["responses"])
    assert "/health" in spec["paths"]  # la route publique reste documentée


# --- 4. une dépendance coupée ------------------------------------------------------------------


def test_livekit_down_is_degraded_with_a_503_then_recovers(
    client: TestClient, auth: dict[str, str], rooms: FakeRoomGateway
) -> None:
    rooms.failing = True
    response = client.get(HEALTH, headers=auth)
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["livekit"]["status"] == "down"
    assert body["checks"]["livekit"]["detail"] == "injoignable"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["storage"]["status"] == "ok"
    assert body["queue"]["status"] == "ok"

    rooms.failing = False
    assert client.get(HEALTH, headers=auth).status_code == 200


def test_s3_down_is_degraded_with_a_503_then_recovers(
    client: TestClient, auth: dict[str, str], storage: FakeStorage
) -> None:
    storage.unreachable = True
    response = client.get(HEALTH, headers=auth)
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["storage"]["status"] == "down"
    assert body["checks"]["livekit"]["status"] == "ok"

    storage.unreachable = False
    assert client.get(HEALTH, headers=auth).status_code == 200


def test_database_down_is_down_with_a_503_and_no_queue(
    client: TestClient,
    app: FastAPI,
    auth: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> Session:
        raise OperationalError("SELECT 1", {}, Exception("unable to open /srv/secret/visio.db"))

    monkeypatch.setattr(app.state, "session_factory", broken)
    response = client.get(HEALTH, headers=auth)
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "down"
    assert body["checks"]["database"] == {
        "status": "down",
        "latency_ms": body["checks"]["database"]["latency_ms"],
        "detail": "injoignable",
    }
    assert body["queue"] is None
    assert body["checks"]["livekit"]["status"] == "ok"  # les autres sondes sont faites quand même


def test_a_dependency_that_hangs_times_out_instead_of_blocking(
    client: TestClient,
    auth: dict[str, str],
    rooms: FakeRoomGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(rooms, "ping", lambda: time.sleep(0.6))
    started = time.perf_counter()
    response = client.get(HEALTH, headers=auth)
    assert time.perf_counter() - started < 0.5
    assert response.status_code == 503
    assert response.json()["checks"]["livekit"]["detail"] == "délai dépassé"


def test_probes_run_in_parallel(
    client: TestClient,
    auth: dict[str, str],
    rooms: FakeRoomGateway,
    storage: FakeStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rooms, "ping", lambda: time.sleep(0.3))
    monkeypatch.setattr(storage, "ping", lambda: time.sleep(0.3))
    started = time.perf_counter()
    assert client.get(HEALTH, headers=auth).status_code == 200
    assert time.perf_counter() - started < 0.55  # 0,3 s et non 0,6 s


# --- 5. rien de sensible -----------------------------------------------------------------------


def secrets_of(settings: Settings) -> list[str]:
    return [
        settings.api_key.get_secret_value(),
        settings.link_signing_secret.get_secret_value(),
        settings.webhook_secret.get_secret_value(),
        settings.livekit_api_secret.get_secret_value(),
        settings.livekit_api_key,
        settings.s3_secret_key.get_secret_value(),
        settings.s3_access_key.get_secret_value(),
        settings.s3_endpoint,
        settings.s3_bucket,
        settings.livekit_url,
        settings.webhook_url,
        SECRET_API_KEY,
        "/srv/secret",
        "Traceback",
    ]


def test_nothing_sensitive_in_the_response_or_the_logs(
    client: TestClient,
    app: FastAPI,
    auth: dict[str, str],
    settings: Settings,
    rooms: FakeRoomGateway,
    storage: FakeStorage,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken() -> Session:
        raise OperationalError("SELECT 1", {}, Exception("unable to open /srv/secret/visio.db"))

    caplog.set_level(logging.DEBUG)
    texts = [client.get(HEALTH, headers=auth).text]  # tout va bien
    rooms.failing = True
    storage.unreachable = True
    texts.append(client.get(HEALTH, headers=auth).text)  # deux pannes
    monkeypatch.setattr(app.state, "session_factory", broken)
    texts.append(client.get(HEALTH, headers=auth).text)  # base coupée
    logs = "\n".join(
        f"{r.getMessage()} {r.__dict__}" for r in caplog.records if r.name.startswith("visio")
    )
    for secret in secrets_of(settings):
        for text in texts:
            assert secret not in text, secret
        assert secret not in logs, secret


def test_a_failed_probe_is_logged_with_its_name_and_type_only(
    client: TestClient,
    auth: dict[str, str],
    rooms: FakeRoomGateway,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    rooms.failing = True
    client.get(HEALTH, headers=auth)
    records = [r for r in caplog.records if r.name == "visio.health"]
    assert len(records) == 1
    assert records[0].dependency == "livekit"  # type: ignore[attr-defined]
    assert records[0].error_type == "MediaServerError"  # type: ignore[attr-defined]
    assert records[0].levelno == logging.WARNING


def test_health_checks_are_quiet_in_the_access_log(
    client: TestClient, auth: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """Une supervision interroge toutes les quelques secondes : pas de ligne INFO à chaque fois."""
    caplog.set_level(logging.INFO)
    client.get(HEALTH, headers=auth)
    assert [r for r in caplog.records if r.name == "visio.access"] == []


def test_a_503_from_the_health_route_is_a_warning_not_an_error(
    client: TestClient,
    auth: dict[str, str],
    rooms: FakeRoomGateway,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """La route dit « pas en forme » : ce n'est pas une erreur du service (la cause, elle, est
    déjà consignée par la sonde). Un vrai 5xx ailleurs reste en ERROR (`test_observability`)."""
    caplog.set_level(logging.INFO)
    rooms.failing = True
    assert client.get(HEALTH, headers=auth).status_code == 503
    access = [r for r in caplog.records if r.name == "visio.access"]
    assert [r.levelno for r in access] == [logging.WARNING]


# --- 6. la file de tâches ----------------------------------------------------------------------


@pytest.fixture
def meeting_id(
    create: Callable[..., dict[str, Any]], db: Session
) -> str:  # une réunion pour les clés étrangères, sans les tâches que sa création a posées
    meeting = create()
    db.execute(delete(JobRow))
    db.execute(delete(WebhookDeliveryRow))
    db.commit()
    return str(meeting["id"])


def add_job(
    db: Session, clock: FixedClock, *, kind: str, status: JobStatus, age: int | timedelta
) -> None:
    """Une tâche dont l'heure est passée depuis `age` (négatif : à venir)."""
    delta = age if isinstance(age, timedelta) else timedelta(seconds=age)
    db.add(JobRow(kind=kind, status=status, run_at=clock.now() - delta))
    db.commit()


def add_webhook(
    db: Session, clock: FixedClock, meeting: str, *, status: WebhookStatus, age: int
) -> None:
    db.add(
        WebhookDeliveryRow(
            meeting_id=meeting,
            event_type="meeting.started",
            payload={},
            status=status,
            next_attempt_at=clock.now() - timedelta(seconds=age),
        )
    )
    db.commit()


def queue_of(client: TestClient, auth: dict[str, str]) -> tuple[int, dict[str, Any]]:
    response = client.get(HEALTH, headers=auth)
    return response.status_code, response.json()


def test_a_due_job_shows_its_age_without_being_late(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    clock: FixedClock,
    meeting_id: str,
) -> None:
    add_job(db, clock, kind="close_room", status=JobStatus.PENDING, age=10)
    code, body = queue_of(client, auth)
    assert code == 200
    assert body["queue"]["jobs"] == {
        "pending": 1,
        "failed": 0,
        "abandoned": 0,
        "oldest_due_age_seconds": 10,
        "late": False,
    }


def test_a_job_whose_time_has_not_come_is_pending_but_not_due(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    clock: FixedClock,
    meeting_id: str,
) -> None:
    add_job(db, clock, kind="close_room", status=JobStatus.PENDING, age=-3600)
    code, body = queue_of(client, auth)
    assert code == 200
    assert body["queue"]["jobs"]["pending"] == 1
    assert body["queue"]["jobs"]["oldest_due_age_seconds"] is None


def test_the_oldest_due_job_is_reported(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    clock: FixedClock,
    meeting_id: str,
) -> None:
    add_job(db, clock, kind="close_room", status=JobStatus.PENDING, age=5)
    add_job(db, clock, kind="start_recording", status=JobStatus.FAILED, age=45)
    _, body = queue_of(client, auth)
    assert body["queue"]["jobs"]["oldest_due_age_seconds"] == 45
    assert body["queue"]["jobs"]["pending"] == 1
    assert body["queue"]["jobs"]["failed"] == 1


@pytest.mark.parametrize(
    ("kind", "age", "late"),
    [
        ("close_room", 60, False),
        ("close_room", 180, True),
        (MERGE_RECORDING, 10 * 60, False),  # la fusion d'une longue réunion prend des minutes
        (MERGE_RECORDING, 40 * 60, True),
        (TRANSCRIBE, 2 * 3600, False),  # « quelques heures » acceptées (besoin §5)
        (TRANSCRIBE, 4 * 3600, True),
    ],
)
def test_lateness_depends_on_the_kind_of_job(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    clock: FixedClock,
    meeting_id: str,
    kind: str,
    age: int,
    late: bool,
) -> None:
    add_job(db, clock, kind=kind, status=JobStatus.PENDING, age=age)
    code, body = queue_of(client, auth)
    assert body["queue"]["jobs"]["late"] is late
    assert body["queue"]["status"] == ("degraded" if late else "ok")
    assert body["status"] == ("degraded" if late else "ok")
    assert code == (503 if late else 200)


def test_a_job_waiting_for_its_retry_is_not_a_problem(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    clock: FixedClock,
    meeting_id: str,
) -> None:
    add_job(db, clock, kind="close_room", status=JobStatus.FAILED, age=-30)  # reprise dans 30 s
    code, body = queue_of(client, auth)
    assert code == 200
    assert body["queue"]["jobs"]["failed"] == 1


def test_an_abandoned_job_makes_the_service_degraded(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    clock: FixedClock,
    meeting_id: str,
) -> None:
    add_job(db, clock, kind="close_room", status=JobStatus.ABANDONED, age=3600)
    code, body = queue_of(client, auth)
    assert code == 503
    assert body["status"] == "degraded"
    assert body["queue"]["jobs"]["abandoned"] == 1
    assert body["queue"]["jobs"]["late"] is False  # un abandon n'est pas une attente


def test_finished_jobs_are_ignored(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    clock: FixedClock,
    meeting_id: str,
) -> None:
    for _ in range(3):
        add_job(db, clock, kind="close_room", status=JobStatus.DONE, age=99999)
    code, body = queue_of(client, auth)
    assert code == 200
    assert body["queue"]["jobs"]["pending"] == 0
    assert body["queue"]["jobs"]["oldest_due_age_seconds"] is None


def test_webhooks_pending_abandoned_and_late(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    clock: FixedClock,
    meeting_id: str,
) -> None:
    add_webhook(db, clock, meeting_id, status=WebhookStatus.PENDING, age=20)
    add_webhook(db, clock, meeting_id, status=WebhookStatus.DELIVERED, age=99999)
    code, body = queue_of(client, auth)
    assert code == 200
    assert body["queue"]["webhooks"] == {
        "pending": 1,
        "abandoned": 0,
        "oldest_due_age_seconds": 20,
        "late": False,
    }

    add_webhook(db, clock, meeting_id, status=WebhookStatus.PENDING, age=300)
    code, body = queue_of(client, auth)
    assert code == 503
    assert body["queue"]["webhooks"]["late"] is True
    assert body["queue"]["webhooks"]["oldest_due_age_seconds"] == 300


def test_an_abandoned_webhook_makes_the_service_degraded(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    clock: FixedClock,
    meeting_id: str,
) -> None:
    add_webhook(db, clock, meeting_id, status=WebhookStatus.ABANDONED, age=3600)
    code, body = queue_of(client, auth)
    assert code == 503
    assert body["queue"]["webhooks"]["abandoned"] == 1


def test_failed_artifacts_are_counted_but_do_not_degrade(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    meeting_id: str,
) -> None:
    """Un enregistrement sans aucune piste est un échec « normal » (14b) : à voir, pas à alerter."""
    db.add(
        ArtifactRow(
            meeting_id=meeting_id, kind=ArtifactKind.RECORDING, status=ArtifactStatus.FAILED
        )
    )
    db.add(
        ArtifactRow(meeting_id=meeting_id, kind=ArtifactKind.RECORDING, status=ArtifactStatus.READY)
    )
    db.commit()
    code, body = queue_of(client, auth)
    assert code == 200
    assert body["queue"]["artifacts_failed"] == 1


def test_the_age_follows_the_clock(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    clock: FixedClock,
    meeting_id: str,
) -> None:
    add_job(db, clock, kind="close_room", status=JobStatus.PENDING, age=0)
    assert queue_of(client, auth)[1]["queue"]["jobs"]["oldest_due_age_seconds"] == 0
    clock.advance(timedelta(minutes=5))
    code, body = queue_of(client, auth)
    assert body["queue"]["jobs"]["oldest_due_age_seconds"] == 300
    assert code == 503  # plus de 2 minutes : le planificateur ne la prend pas


# --- 7. la sonde LiveKit réelle (transport HTTP simulé) ----------------------------------------


def gateway_with(handler: Callable[[httpx.Request], httpx.Response]) -> LiveKitGateway:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return LiveKitGateway("ws://localhost:7880", "devkey", "s" * 40, client=client)


def test_the_livekit_probe_lists_rooms_with_a_token_that_is_not_tied_to_a_room() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"rooms": []})

    gateway_with(handler).ping()
    assert [r.url.path for r in seen] == ["/twirp/livekit.RoomService/ListRooms"]
    claims = jwt.decode(
        seen[0].headers["Authorization"].removeprefix("Bearer "), "s" * 40, algorithms=["HS256"]
    )
    assert claims["video"]["roomList"] is True
    assert "room" not in claims["video"]  # liste toutes les salles, aucune n'est créée


def test_the_livekit_probe_fails_when_the_server_refuses() -> None:
    gateway = gateway_with(lambda request: httpx.Response(401, json={"code": "unauthenticated"}))
    with pytest.raises(MediaServerError):
        gateway.ping()


def test_the_livekit_probe_fails_when_the_server_does_not_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connexion refusée", request=request)

    with pytest.raises(MediaServerError):
        gateway_with(handler).ping()
