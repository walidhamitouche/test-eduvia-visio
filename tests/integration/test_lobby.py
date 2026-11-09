"""Salle d'attente (étape 9) : scénario 4 du besoin, côté API.

Un participant n'entre qu'après admission ; sans salle d'attente il entre directement. Tant qu'il
attend, **aucun jeton** n'est émis : il ne peut ni voir ni entendre, ni écrire.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.integration.conftest import meeting_body
from tests.integration.helpers import at, error_code, join, post_room, token_of
from visio.db.models import AccessRow, AccessStatus
from visio.domain.clock import FixedClock
from visio.services.access import LOBBY_FRESH_FOR
from visio.services.rooms import FakeRoomGateway


def create(client: TestClient, auth: dict[str, str], **overrides: Any) -> dict[str, Any]:
    response = client.post("/v1/meetings", json=meeting_body(**overrides), headers=auth)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


@pytest.fixture
def lobby_meeting(client: TestClient, auth: dict[str, str], clock: FixedClock) -> dict[str, Any]:
    created = create(client, auth, waiting_room=True)
    at(clock, "16:30:00")
    return created


def ask(client: TestClient, meeting: dict[str, Any], name: str, **extra: Any) -> dict[str, Any]:
    response = join(client, meeting["participant_url"], display_name=name, **extra)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def waiting_names(client: TestClient, meeting: dict[str, Any]) -> list[str]:
    response = post_room(client, "lobby", meeting["organizer_urls"][0])
    assert response.status_code == 200, response.text
    return [entry["display_name"] for entry in response.json()["entries"]]


# --- scénario 4 --------------------------------------------------------------------------------


def test_a_waiting_participant_gets_no_token_at_all(
    client: TestClient, lobby_meeting: dict[str, Any]
) -> None:
    body = ask(client, lobby_meeting, "Lina")
    assert body["status"] == "waiting"
    assert body["livekit_token"] is None
    assert body["livekit_url"] is None
    assert body["expires_at"] is None
    assert body["client_id"]  # le navigateur le garde pour redemander avec la même identité
    assert "token" not in {k for k in body if k not in ("livekit_token",)}


def test_the_participant_enters_only_after_admission(
    client: TestClient, lobby_meeting: dict[str, Any]
) -> None:
    first = ask(client, lobby_meeting, "Lina")
    client_id = first["client_id"]
    assert waiting_names(client, lobby_meeting) == ["Lina"]
    entry_id = post_room(client, "lobby", lobby_meeting["organizer_urls"][0]).json()["entries"][0][
        "id"
    ]

    admitted = post_room(client, "lobby/admit", lobby_meeting["organizer_urls"][0], ids=[entry_id])
    assert admitted.json() == {"count": 1}

    body = ask(client, lobby_meeting, "Lina", client_id=client_id)
    assert body["status"] == "admitted"
    assert body["livekit_token"]
    assert waiting_names(client, lobby_meeting) == []  # plus dans la liste


def test_without_a_waiting_room_the_participant_enters_directly(
    client: TestClient, auth: dict[str, str], clock: FixedClock
) -> None:
    meeting = create(client, auth, waiting_room=False)
    at(clock, "16:30:00")
    body = ask(client, meeting, "Lina")
    assert body["status"] == "admitted"
    assert body["livekit_token"]


def test_organizers_always_enter_directly(
    client: TestClient, lobby_meeting: dict[str, Any]
) -> None:
    response = join(client, lobby_meeting["organizer_urls"][0])
    assert response.json()["status"] == "admitted"


def test_a_named_participant_also_waits(
    client: TestClient, auth: dict[str, str], lobby_meeting: dict[str, Any]
) -> None:
    link = client.post(
        f"/v1/meetings/{lobby_meeting['id']}/links",
        json={"role": "participant", "display_name": "Lina Benali", "external_id": "stu_981"},
        headers=auth,
    ).json()
    assert join(client, link["url"]).json()["status"] == "waiting"
    entries = post_room(client, "lobby", lobby_meeting["organizer_urls"][0]).json()["entries"]
    assert [(e["display_name"], e["external_id"]) for e in entries] == [("Lina Benali", "stu_981")]


def test_without_an_organizer_present_nobody_enters(
    client: TestClient, lobby_meeting: dict[str, Any]
) -> None:
    """Personne ne peut se faire admettre sans organisateur : seule une action d'organisateur
    (lien organisateur vérifié) fait passer une demande à « admise »."""
    for name in ("Lina", "Karim", "Sonia"):
        assert ask(client, lobby_meeting, name)["status"] == "waiting"
    # relire sa demande, longtemps, ne change rien
    for _ in range(5):
        assert ask(client, lobby_meeting, "Lina")["status"] == "waiting"


def test_a_participant_cannot_admit_himself_or_read_the_list(
    client: TestClient, lobby_meeting: dict[str, Any], db: Session
) -> None:
    ask(client, lobby_meeting, "Lina")
    entry_id = db.query(AccessRow).one().id
    for route, extra in (
        ("lobby", {}),
        ("lobby/admit", {"ids": [entry_id]}),
        ("lobby/deny", {"ids": [entry_id]}),
        ("lobby/admit-all", {}),
    ):
        response = post_room(client, route, lobby_meeting["participant_url"], **extra)
        assert response.status_code == 403, route
        assert error_code(response) == "organizer_required"
    db.expire_all()
    assert db.query(AccessRow).one().status is AccessStatus.WAITING


# --- tout admettre -----------------------------------------------------------------------------


def test_admit_all_lets_everyone_in_with_one_click(
    client: TestClient, lobby_meeting: dict[str, Any]
) -> None:
    ids = {}
    for name in ("Lina", "Karim", "Sonia", "Omar"):
        ids[name] = ask(client, lobby_meeting, name)["client_id"]

    response = post_room(client, "lobby/admit-all", lobby_meeting["organizer_urls"][0])

    assert response.json() == {"count": 4}
    for name, client_id in ids.items():
        body = ask(client, lobby_meeting, name, client_id=client_id)
        assert body["status"] == "admitted", name
        assert body["livekit_token"]


def test_a_person_who_closed_the_page_is_no_longer_listed_nor_admitted_by_admit_all(
    client: TestClient, clock: FixedClock, lobby_meeting: dict[str, Any]
) -> None:
    gone = ask(client, lobby_meeting, "Partie")["client_id"]
    clock.set(clock.now() + LOBBY_FRESH_FOR + timedelta(seconds=1))
    ask(client, lobby_meeting, "Présente")

    assert waiting_names(client, lobby_meeting) == ["Présente"]
    assert post_room(client, "lobby/admit-all", lobby_meeting["organizer_urls"][0]).json() == {
        "count": 1
    }
    # revenue plus tard, elle doit être de nouveau admise : l'organisateur ne l'a pas vue
    assert ask(client, lobby_meeting, "Partie", client_id=gone)["status"] == "waiting"


# --- refus -------------------------------------------------------------------------------------


def test_a_denied_participant_cannot_enter_nor_ask_again(
    client: TestClient, lobby_meeting: dict[str, Any]
) -> None:
    client_id = ask(client, lobby_meeting, "Lina")["client_id"]
    entry_id = post_room(client, "lobby", lobby_meeting["organizer_urls"][0]).json()["entries"][0][
        "id"
    ]
    assert post_room(
        client, "lobby/deny", lobby_meeting["organizer_urls"][0], ids=[entry_id]
    ).json() == {"count": 1}

    for _ in range(3):
        body = ask(client, lobby_meeting, "Lina", client_id=client_id)
        assert body["status"] == "denied"
        assert body["livekit_token"] is None
    # « admit-all » ne rouvre pas la porte à une personne refusée
    assert post_room(client, "lobby/admit-all", lobby_meeting["organizer_urls"][0]).json() == {
        "count": 0
    }
    assert ask(client, lobby_meeting, "Lina", client_id=client_id)["status"] == "denied"


def test_a_new_decision_by_an_organizer_can_admit_a_denied_person(
    client: TestClient, lobby_meeting: dict[str, Any]
) -> None:
    client_id = ask(client, lobby_meeting, "Lina")["client_id"]
    organizer = lobby_meeting["organizer_urls"][0]
    entry = post_room(client, "lobby", organizer).json()["entries"][0]
    post_room(client, "lobby/deny", organizer, ids=[entry["id"]])
    listed = post_room(client, "lobby", organizer).json()["entries"]
    assert [(e["display_name"], e["status"]) for e in listed] == [("Lina", "denied")]

    post_room(client, "lobby/admit", organizer, ids=[entry["id"]])

    assert ask(client, lobby_meeting, "Lina", client_id=client_id)["status"] == "admitted"


def test_decisions_on_unknown_requests_are_clean_404s(
    client: TestClient, lobby_meeting: dict[str, Any]
) -> None:
    response = post_room(
        client, "lobby/admit", lobby_meeting["organizer_urls"][0], ids=["acc_inconnu"]
    )
    assert response.status_code == 404
    assert error_code(response) == "participant_not_found"


def test_a_request_of_another_meeting_cannot_be_decided(
    client: TestClient, auth: dict[str, str], lobby_meeting: dict[str, Any], db: Session
) -> None:
    ask(client, lobby_meeting, "Lina")
    foreign = create(client, auth, waiting_room=True)
    response = post_room(
        client, "lobby/admit", foreign["organizer_urls"][0], ids=[db.query(AccessRow).one().id]
    )
    assert response.status_code in (404, 409)  # l'autre réunion n'est pas encore ouverte (16h30)
    db.expire_all()
    assert db.query(AccessRow).one().status is AccessStatus.WAITING


def test_an_excluded_person_cannot_be_readmitted(
    client: TestClient, lobby_meeting: dict[str, Any], db: Session
) -> None:
    client_id = ask(client, lobby_meeting, "Lina")["client_id"]
    entry = db.query(AccessRow).one()
    entry.status = AccessStatus.EXCLUDED
    db.commit()
    response = post_room(client, "lobby/admit", lobby_meeting["organizer_urls"][0], ids=[entry.id])
    assert response.status_code == 404
    again = join(client, lobby_meeting["participant_url"], display_name="Lina", client_id=client_id)
    assert again.status_code == 403
    assert error_code(again) == "participant_excluded"


def test_waiting_participants_get_no_media_server_call(
    client: TestClient, rooms: FakeRoomGateway, lobby_meeting: dict[str, Any]
) -> None:
    ask(client, lobby_meeting, "Lina")
    assert rooms.calls == [] and rooms.participants == {}


def test_the_waiting_person_cannot_write_in_the_chat(
    client: TestClient, lobby_meeting: dict[str, Any]
) -> None:
    client_id = ask(client, lobby_meeting, "Lina")["client_id"]
    response = client.post(
        "/v1/room/chat",
        json={
            "token": token_of(lobby_meeting["participant_url"]),
            "client_id": client_id,
            "text": "coucou",
        },
    )
    assert response.status_code == 403
    assert error_code(response) == "not_admitted"
