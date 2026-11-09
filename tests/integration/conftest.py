"""Fixtures de l'API réunions : base SQLite temporaire migrée, horloge simulée, clé d'API."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import SECRET_API_KEY, valid_env
from visio.config import Settings
from visio.db.migrate import upgrade
from visio.domain.clock import FixedClock
from visio.domain.slot import parse_iso8601
from visio.main import create_app
from visio.services.egress import FakeRecorder
from visio.services.rooms import FakeRoomGateway
from visio.services.storage import FakeStorage
from visio.services.webhooks import SendResult

# Réunion du 2 janvier 2027, 16 h - 18 h (heure de Paris), le « scénario 1 » du besoin.
SLOT_START = "2027-01-02T16:00:00+01:00"
SLOT_END = "2027-01-02T18:00:00+01:00"
# Avant le créneau : tout est encore modifiable.
BEFORE_SLOT = parse_iso8601("2027-01-02T10:00:00+01:00")

SetEnv = Callable[[dict[str, str]], None]


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(BEFORE_SLOT)


@pytest.fixture
def settings(set_env: SetEnv, tmp_path: Path) -> Settings:
    db_url = f"sqlite:///{tmp_path / 'data' / 'visio.db'}"
    set_env({**valid_env(), "DATABASE_URL": db_url})
    upgrade(db_url)
    return Settings()  # type: ignore[call-arg]  # lit l'environnement posé ci-dessus


class RecordingSender:
    """Faux récepteur Eduvia : note chaque appel et répond ce qu'on lui demande."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], bytes]] = []
        self.responses: list[SendResult] = []  # consommées dans l'ordre, puis 200

    def __call__(self, url: str, headers: dict[str, str], body: bytes) -> SendResult:
        self.calls.append((url, headers, body))
        return self.responses.pop(0) if self.responses else SendResult(200, None)


@pytest.fixture
def rooms() -> FakeRoomGateway:
    return FakeRoomGateway()


@pytest.fixture
def recorder() -> FakeRecorder:
    return FakeRecorder()


@pytest.fixture
def sender() -> RecordingSender:
    return RecordingSender()


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def app(
    settings: Settings,
    clock: FixedClock,
    rooms: FakeRoomGateway,
    recorder: FakeRecorder,
    sender: RecordingSender,
    storage: FakeStorage,
) -> FastAPI:
    # Pas de planificateur en tâche de fond : les tests le font tourner eux-mêmes (`run_once`),
    # avec l'horloge simulée. Serveur de médias et récepteur de webhooks sont des faux.
    return create_app(
        settings=settings,
        clock=clock,
        rooms=rooms,
        recorder=recorder,
        sender=sender,
        storage=storage,
        run_workers=False,
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db(client: TestClient) -> Iterator[Session]:
    """Session directe sur la base de l'application (vérifier ou préparer des états)."""
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        yield session


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {SECRET_API_KEY}"}


def meeting_body(**overrides: Any) -> dict[str, Any]:
    """Corps de `POST /v1/meetings` valide (l'exemple du besoin, §4)."""
    body: dict[str, Any] = {
        "title": "Cours BTS MCO – Groupe A",
        "starts_at": SLOT_START,
        "ends_at": SLOT_END,
        "early_join_minutes": 10,
        "waiting_room": True,
        "recording": True,
        "transcription": True,
        "organizers": [{"display_name": "Marie Durand", "external_id": "staff_42"}],
        "metadata": {"eduvia_session_id": 1234},
    }
    body.update(overrides)
    return body


@pytest.fixture
def create(client: TestClient, auth: dict[str, str]) -> Callable[..., dict[str, Any]]:
    """Crée une réunion valide et renvoie la réponse JSON (échoue si ce n'est pas un 201)."""

    def _create(**overrides: Any) -> dict[str, Any]:
        response = client.post("/v1/meetings", json=meeting_body(**overrides), headers=auth)
        assert response.status_code == 201, response.text
        created: dict[str, Any] = response.json()
        return created

    return _create
