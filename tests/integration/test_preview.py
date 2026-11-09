"""`POST /v1/join/preview` (étape 7) : l'aperçu d'un lien pour les écrans d'attente et de
pré-jointure. Même règle du créneau que `join`, mais **aucun jeton LiveKit** n'est émis."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.integration.test_join import at, error_of, named_link, token_of
from visio.db.models import LinkRow
from visio.domain.clock import FixedClock
from visio.services.links import revoke_link

Create = Callable[..., dict[str, Any]]


def preview(client: TestClient, token: str) -> Any:
    return client.post("/v1/join/preview", json={"token": token})


def test_preview_follows_the_slot_rule(
    client: TestClient, clock: FixedClock, create: Create
) -> None:
    token = token_of(create(early_join_minutes=10)["participant_url"])

    at(clock, "15:49:59")
    early = preview(client, token)
    assert early.status_code == 200
    assert early.json()["status"] == "not_open_yet"
    assert early.json()["opens_at"] == "2027-01-02T15:50:00+01:00"
    assert early.json()["server_time"] == "2027-01-02T15:49:59+01:00"

    at(clock, "15:50:00")
    assert preview(client, token).json()["status"] == "open"
    at(clock, "17:59:59")
    assert preview(client, token).json()["status"] == "open"
    at(clock, "18:00:00")
    assert preview(client, token).json()["status"] == "ended"
    at(clock, "18:01:00")
    assert preview(client, token).json()["status"] == "ended"


def test_preview_gives_the_meeting_options_and_nothing_secret(
    client: TestClient, clock: FixedClock, create: Create
) -> None:
    created = create(waiting_room=True, recording=True, transcription=False)
    token = token_of(created["participant_url"])
    at(clock, "16:30:00")
    response = preview(client, token)
    body = response.json()
    assert body["meeting"] == {
        "title": "Cours BTS MCO – Groupe A",
        "starts_at": "2027-01-02T16:00:00+01:00",
        "ends_at": "2027-01-02T18:00:00+01:00",
        "waiting_room": True,
        "recording": True,
        "transcription": False,
    }
    # Ni jeton LiveKit, ni identifiant de réunion, ni metadata : un aperçu ne donne aucun accès.
    assert "livekit" not in response.text.lower()
    assert created["id"] not in response.text
    assert "metadata" not in response.text
    assert "eduvia_session_id" not in response.text


def test_generic_link_asks_for_a_name_and_named_link_locks_it(
    client: TestClient, auth: dict[str, str], clock: FixedClock, create: Create
) -> None:
    created = create()
    at(clock, "16:30:00")

    generic = preview(client, token_of(created["participant_url"])).json()["participant"]
    assert generic == {
        "role": "participant",
        "display_name": None,
        "external_id": None,
        "name_required": True,
    }

    link = named_link(
        client,
        auth,
        created["id"],
        role="participant",
        display_name="Lina Benali",
        external_id="stu_981",
    )
    named = preview(client, token_of(link["url"])).json()["participant"]
    assert named == {
        "role": "participant",
        "display_name": "Lina Benali",
        "external_id": "stu_981",
        "name_required": False,
    }

    organizer = preview(client, token_of(created["organizer_urls"][0])).json()["participant"]
    assert organizer["role"] == "organizer"
    assert organizer["display_name"] == "Marie Durand"
    assert organizer["name_required"] is False


def test_cancelled_meeting_is_reported_not_hidden(
    client: TestClient, auth: dict[str, str], create: Create
) -> None:
    created = create()
    client.delete(f"/v1/meetings/{created['id']}", headers=auth)
    assert preview(client, token_of(created["participant_url"])).json()["status"] == "cancelled"


def test_invented_or_modified_link_is_invalid_with_one_message(
    client: TestClient, create: Create
) -> None:
    token = token_of(create()["participant_url"])
    flipped = token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")
    messages = set()
    for bad in ("n-importe-quoi", flipped, token + "x", ""):
        response = preview(client, bad)
        assert response.status_code == 404, bad
        assert error_of(response)["code"] == "invalid_link"
        messages.add(error_of(response)["message"])
    assert len(messages) == 1


def test_revoked_link_is_refused(
    client: TestClient, db: Session, clock: FixedClock, create: Create
) -> None:
    created = create()
    link_id = db.query(LinkRow).filter(LinkRow.role == "organizer").one().id
    revoke_link(db, link_id, now=clock.now())
    response = preview(client, token_of(created["organizer_urls"][0]))
    assert response.status_code == 403
    assert error_of(response)["code"] == "participant_excluded"


def test_preview_does_not_start_the_meeting(
    client: TestClient, auth: dict[str, str], clock: FixedClock, create: Create
) -> None:
    created = create()
    at(clock, "16:30:00")
    preview(client, token_of(created["participant_url"]))
    state = client.get(f"/v1/meetings/{created['id']}", headers=auth).json()
    assert state["status"] == "scheduled"
    assert state["started_at"] is None
    assert state["participants"] == []


def test_preview_is_in_the_openapi_spec(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert "/v1/join/preview" in spec["paths"]
