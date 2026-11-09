"""Robustesse (corrections de revue) : plafond de participants, coupure malgré des webhooks en
panne, limitation de débit des routes publiques."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import RecordingSender, meeting_body
from tests.integration.helpers import at, error_code, join
from tests.integration.test_lifecycle import go_live, status_of
from visio.config import Settings
from visio.domain.clock import FixedClock
from visio.services import webhooks
from visio.services.rooms import FakeRoomGateway
from visio.workers.scheduler import run_once


@pytest.fixture
def meeting(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    body = meeting_body(waiting_room=False, early_join_minutes=0)
    response = client.post("/v1/meetings", json=body, headers=auth)
    assert response.status_code == 201
    created: dict[str, Any] = response.json()
    return created


def test_the_participant_cap_refuses_extra_participants_but_not_organizers(
    client: TestClient,
    clock: FixedClock,
    settings: Settings,
    rooms: FakeRoomGateway,
    meeting: dict[str, Any],
) -> None:
    at(clock, "16:00:00")
    go_live(client, settings, rooms, meeting)  # 1 organisatrice + 2 participants connectés
    client.app.state.settings = settings.model_copy(  # type: ignore[attr-defined]
        update={"max_participants_per_meeting": 2}
    )

    full = join(client, meeting["participant_url"], display_name="Nadia")
    assert full.status_code == 409
    assert error_code(full) == "meeting_full"

    organizer = join(client, meeting["organizer_urls"][0])
    assert organizer.status_code == 200  # un organisateur n'est jamais refusé

    client.app.state.settings = settings.model_copy(  # type: ignore[attr-defined]
        update={"max_participants_per_meeting": 0}
    )  # 0 = illimité
    assert join(client, meeting["participant_url"], display_name="Nadia").status_code == 200


def test_the_cut_off_does_not_wait_for_failing_webhooks(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    settings: Settings,
    rooms: FakeRoomGateway,
    sender: RecordingSender,
    meeting: dict[str, Any],
) -> None:
    at(clock, "16:00:00")
    go_live(client, settings, rooms, meeting)
    room = next(iter(rooms.participants))
    sender.responses = [webhooks.SendResult(None, "ReadTimeout")] * 50  # Eduvia ne répond pas

    at(clock, "18:00:00")
    state = client.app.state  # type: ignore[attr-defined]
    run_once(  # exactement comme la boucle de production : sans livraison des webhooks
        state.session_factory,
        state.settings,
        state.rooms,
        state.recorder,
        state.webhook_sender,
        state.clock,
        state.storage,
        with_tracks=False,
        with_deliveries=False,
    )

    assert rooms.called("delete_room") == [(room,)]
    assert status_of(client, auth, meeting) == "ended"
    assert sender.calls == []  # aucun appel réseau dans le tour qui coupe la salle

    with state.session_factory() as db:  # la boucle de livraison, à part, fait ses tentatives
        assert webhooks.deliver_due(db, settings, state.webhook_sender, now=clock.now()) > 0
    assert sender.calls  # et échoue sans rien bloquer


def test_the_join_routes_are_rate_limited_per_ip(
    client: TestClient, settings: Settings, meeting: dict[str, Any]
) -> None:
    from visio.api import ratelimit

    ratelimit._limiter = ratelimit.RateLimiter()  # compteur neuf pour ce test
    client.app.state.settings = settings.model_copy(  # type: ignore[attr-defined]
        update={"join_rate_limit_per_minute": 3}
    )
    body = {"token": "inventé"}
    codes = [client.post("/v1/join/preview", json=body).status_code for _ in range(5)]
    assert codes[:3] == [404, 404, 404]
    assert codes[3:] == [429, 429]
    limited = client.post("/v1/join", json=body)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert limited.headers["Retry-After"] == "60"

    ratelimit._limiter = ratelimit.RateLimiter()
