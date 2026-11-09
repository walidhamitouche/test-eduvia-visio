"""Rôles et modération (étape 8) : droits de l'organisateur appliqués côté serveur.

Scénario 3 du besoin : l'organisateur coupe un micro et exclut un participant ; ce participant
ne peut pas revenir. Ici contre un faux serveur de médias ; le vrai est dans tests/e2e.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.integration.conftest import meeting_body
from tests.integration.helpers import (
    at,
    enter,
    error_code,
    join,
    post_room,
    token_of,
)
from visio.config import Settings
from visio.db.models import AccessRow, AccessStatus, LinkRow
from visio.domain.clock import FixedClock
from visio.services.rooms import FakeRoomGateway

Create = Callable[..., dict[str, Any]]


@pytest.fixture
def meeting(client: TestClient, auth: dict[str, str], clock: FixedClock) -> dict[str, Any]:
    """Réunion ouverte, salle ouverte, deux organisateurs (formateur et co-animateur)."""
    body = meeting_body(
        waiting_room=False,
        organizers=[
            {"display_name": "Marie Durand", "external_id": "staff_42"},
            {"display_name": "Paul Martin", "external_id": "staff_43"},
        ],
    )
    response = client.post("/v1/meetings", json=body, headers=auth)
    assert response.status_code == 201, response.text
    at(clock, "16:30:00")
    created: dict[str, Any] = response.json()
    return created


# --- seul un organisateur modère ---------------------------------------------------------------


def test_a_participant_calling_a_moderation_route_gets_403(
    client: TestClient, settings: Settings, rooms: FakeRoomGateway, meeting: dict[str, Any]
) -> None:
    enter(client, settings, rooms, meeting["organizer_urls"][0])
    lina = enter(client, settings, rooms, meeting["participant_url"], display_name="Lina")
    other = enter(client, settings, rooms, meeting["participant_url"], display_name="Karim")

    mute = post_room(
        client, "mute", meeting["participant_url"], identity=other.identity, source="microphone"
    )
    remove = post_room(client, "remove", meeting["participant_url"], identity=other.identity)
    end = post_room(client, "end", meeting["participant_url"])
    for response in (mute, remove, end):
        assert response.status_code == 403
        assert error_code(response) == "organizer_required"
    assert rooms.calls == []  # aucun ordre n'est parti vers le serveur de médias
    assert lina.identity != other.identity


def test_a_participant_who_forges_an_organizer_token_is_refused(
    client: TestClient, settings: Settings, rooms: FakeRoomGateway, meeting: dict[str, Any]
) -> None:
    import jwt

    target = enter(client, settings, rooms, meeting["participant_url"], display_name="Lina")
    real = token_of(meeting["participant_url"])
    claims = jwt.decode(real, options={"verify_signature": False})
    forged = jwt.encode({**claims, "role": "organizer"}, "mauvais-secret" * 4, algorithm="HS256")
    for token in (forged, real[:-3] + "AAA"):
        response = client.post(
            "/v1/room/remove", json={"token": token, "identity": target.identity}
        )
        assert response.status_code == 404
        assert error_code(response) == "invalid_link"
    assert rooms.calls == []


def test_an_invented_token_is_refused_before_anything_else(client: TestClient) -> None:
    response = client.post(
        "/v1/room/mute", json={"token": "inventé", "identity": "x", "source": "camera"}
    )
    assert response.status_code == 404
    assert error_code(response) == "invalid_link"


# --- couper un micro ---------------------------------------------------------------------------


def test_organizer_mutes_a_microphone(
    client: TestClient, settings: Settings, rooms: FakeRoomGateway, meeting: dict[str, Any]
) -> None:
    enter(client, settings, rooms, meeting["organizer_urls"][0])
    lina = enter(client, settings, rooms, meeting["participant_url"], display_name="Lina")

    response = post_room(
        client, "mute", meeting["organizer_urls"][0], identity=lina.identity, source="microphone"
    )

    assert response.status_code == 200
    assert response.json() == {"muted": 1}
    room = next(iter(rooms.participants))
    [(call_room, identity, track_sid, muted)] = rooms.called("mute")
    assert (call_room, identity, muted) == (room, lina.identity, True)
    assert track_sid.endswith("microphone")  # le micro, pas la caméra


def test_organizer_mutes_camera_and_screen_share_separately(
    client: TestClient, settings: Settings, rooms: FakeRoomGateway, meeting: dict[str, Any]
) -> None:
    from tests.integration.helpers import claims_of, seat

    enter(client, settings, rooms, meeting["organizer_urls"][0])
    body = join(client, meeting["participant_url"], display_name="Lina").json()
    lina = seat(
        rooms,
        claims_of(settings, body["livekit_token"]),
        tracks=("microphone", "camera", "screen_share", "screen_share_audio"),
    )
    organizer = meeting["organizer_urls"][0]

    assert post_room(client, "mute", organizer, identity=lina.identity, source="camera").json() == {
        "muted": 1
    }
    # le partage coupe aussi le son du partage
    assert post_room(
        client, "mute", organizer, identity=lina.identity, source="screen_share"
    ).json() == {"muted": 2}
    sources = [call[2].rsplit("_", 1)[-1] for call in rooms.called("mute")]
    assert sources[0] == "camera"


def test_muting_a_person_who_publishes_nothing_is_a_no_op(
    client: TestClient, settings: Settings, rooms: FakeRoomGateway, meeting: dict[str, Any]
) -> None:
    from tests.integration.helpers import claims_of, seat

    enter(client, settings, rooms, meeting["organizer_urls"][0])
    body = join(client, meeting["participant_url"], display_name="Lina").json()
    lina = seat(rooms, claims_of(settings, body["livekit_token"]), tracks=())
    response = post_room(
        client, "mute", meeting["organizer_urls"][0], identity=lina.identity, source="microphone"
    )
    assert response.json() == {"muted": 0}
    assert rooms.called("mute") == []


def test_unknown_person_is_a_clean_404(
    client: TestClient, settings: Settings, rooms: FakeRoomGateway, meeting: dict[str, Any]
) -> None:
    enter(client, settings, rooms, meeting["organizer_urls"][0])
    response = post_room(
        client, "mute", meeting["organizer_urls"][0], identity="pg_" + "0" * 32, source="camera"
    )
    assert response.status_code == 404
    assert error_code(response) == "participant_not_found"


def test_an_organizer_cannot_moderate_another_organizer_nor_himself(
    client: TestClient, settings: Settings, rooms: FakeRoomGateway, meeting: dict[str, Any]
) -> None:
    marie = enter(client, settings, rooms, meeting["organizer_urls"][0])
    paul = enter(client, settings, rooms, meeting["organizer_urls"][1])
    for target in (paul, marie):
        for route, extra in (("mute", {"source": "microphone"}), ("remove", {})):
            response = post_room(
                client, route, meeting["organizer_urls"][0], identity=target.identity, **extra
            )
            assert response.status_code == 403
            assert error_code(response) == "target_is_organizer"
    assert rooms.calls == []


def test_moderation_is_refused_outside_the_slot(
    client: TestClient, clock: FixedClock, rooms: FakeRoomGateway, meeting: dict[str, Any]
) -> None:
    at(clock, "18:00:00")
    response = post_room(
        client, "mute", meeting["organizer_urls"][0], identity="pg_x", source="camera"
    )
    assert response.status_code == 409
    assert error_code(response) == "meeting_ended"
    assert rooms.calls == []


def test_media_server_down_is_a_502_not_a_crash(
    client: TestClient, settings: Settings, rooms: FakeRoomGateway, meeting: dict[str, Any]
) -> None:
    enter(client, settings, rooms, meeting["organizer_urls"][0])
    lina = enter(client, settings, rooms, meeting["participant_url"], display_name="Lina")
    rooms.failing = True
    response = post_room(
        client, "mute", meeting["organizer_urls"][0], identity=lina.identity, source="camera"
    )
    assert response.status_code == 502
    assert error_code(response) == "media_server_unavailable"


# --- exclusion définitive ----------------------------------------------------------------------


def test_excluding_a_named_participant_revokes_the_link_for_good(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    rooms: FakeRoomGateway,
    db: Session,
    meeting: dict[str, Any],
) -> None:
    link = client.post(
        f"/v1/meetings/{meeting['id']}/links",
        json={"role": "participant", "display_name": "Lina Benali", "external_id": "stu_981"},
        headers=auth,
    ).json()
    enter(client, settings, rooms, meeting["organizer_urls"][0])
    lina = enter(client, settings, rooms, link["url"])

    response = post_room(client, "remove", meeting["organizer_urls"][0], identity=lina.identity)

    assert response.status_code == 200
    assert rooms.called("remove") == [(next(iter(rooms.participants)), lina.identity)]
    # scénario 3 : même lien, même jour : refusé
    again = join(client, link["url"])
    assert again.status_code == 403
    assert error_code(again) == "participant_excluded"
    # le lien est révoqué (liste de révocation) et la décision est tracée
    db.expire_all()
    revoked = db.get(LinkRow, link["id"])
    assert revoked is not None and revoked.revoked_at is not None
    entry = db.query(AccessRow).filter_by(participant_identity=lina.identity).one()
    assert entry.status is AccessStatus.EXCLUDED
    assert entry.decided_by is not None


def test_excluding_on_the_generic_link_only_excludes_that_browser(
    client: TestClient, settings: Settings, rooms: FakeRoomGateway, meeting: dict[str, Any]
) -> None:
    enter(client, settings, rooms, meeting["organizer_urls"][0])
    first = client.post(
        "/v1/join",
        json={"token": token_of(meeting["participant_url"]), "display_name": "Lina"},
    ).json()
    from tests.integration.helpers import claims_of, seat

    lina = seat(rooms, claims_of(settings, first["livekit_token"]))
    karim = enter(client, settings, rooms, meeting["participant_url"], display_name="Karim")

    post_room(client, "remove", meeting["organizer_urls"][0], identity=lina.identity)

    # Lina (même navigateur : même client_id) ne peut plus revenir…
    again = join(
        client, meeting["participant_url"], display_name="Lina", client_id=first["client_id"]
    )
    assert again.status_code == 403
    assert error_code(again) == "participant_excluded"
    # … mais le lien générique n'est pas révoqué : Karim et les autres entrent toujours.
    assert join(client, meeting["participant_url"], display_name="Karim").status_code == 200
    assert karim.identity != lina.identity
    other = join(client, meeting["participant_url"], display_name="Sonia")
    assert other.status_code == 200


def test_an_excluded_person_cannot_return_on_the_generic_link_by_clearing_the_browser(
    client: TestClient, settings: Settings, rooms: FakeRoomGateway, meeting: dict[str, Any]
) -> None:
    from tests.integration.helpers import claims_of, seat

    enter(client, settings, rooms, meeting["organizer_urls"][0])
    first = client.post(
        "/v1/join",
        json={"token": token_of(meeting["participant_url"]), "display_name": "Léa Durand"},
    )
    assert first.status_code == 200
    assert "visio_cid" in first.cookies  # identité posée par le serveur
    lea = seat(rooms, claims_of(settings, first.json()["livekit_token"]))
    post_room(client, "remove", meeting["organizer_urls"][0], identity=lea.identity)

    # 1. stockage local vidé, cookie conservé : même identité, refusée
    again = client.post(
        "/v1/join",
        json={"token": token_of(meeting["participant_url"]), "display_name": "Léa Durand"},
    )
    assert error_code(again) == "participant_excluded"

    # 2. fenêtre privée (ni cookie ni client_id), même nom à l'accent et à la casse près : refusée
    fresh = TestClient(client.app)
    for name in ("Léa Durand", "lea  DURAND"):
        r = fresh.post(
            "/v1/join", json={"token": token_of(meeting["participant_url"]), "display_name": name}
        )
        assert r.status_code == 403 and error_code(r) == "participant_excluded"

    # 3. les autres personnes entrent toujours
    ok = fresh.post(
        "/v1/join",
        json={"token": token_of(meeting["participant_url"]), "display_name": "Karim"},
    )
    assert ok.status_code == 200


def test_an_excluded_name_stays_excluded_even_from_a_browser_already_known(
    client: TestClient, settings: Settings, rooms: FakeRoomGateway, meeting: dict[str, Any]
) -> None:
    from tests.integration.helpers import claims_of, seat

    enter(client, settings, rooms, meeting["organizer_urls"][0])
    token = token_of(meeting["participant_url"])
    lina_browser = TestClient(client.app)
    first = lina_browser.post("/v1/join", json={"token": token, "display_name": "Lina"})
    lina = seat(rooms, claims_of(settings, first.json()["livekit_token"]))
    post_room(client, "remove", meeting["organizer_urls"][0], identity=lina.identity)

    # Un autre navigateur entre sous un autre nom, puis revient avec le nom exclu : refusé.
    other = TestClient(client.app)
    assert other.post("/v1/join", json={"token": token, "display_name": "Walid"}).status_code == 200
    again = other.post("/v1/join", json={"token": token, "display_name": "Lina"})
    assert again.status_code == 403 and error_code(again) == "participant_excluded"


def test_the_exclusion_is_recorded_before_the_disconnection(
    client: TestClient, settings: Settings, rooms: FakeRoomGateway, meeting: dict[str, Any]
) -> None:
    """Si le serveur de médias est injoignable, l'ordre échoue (502) mais la personne ne pourra
    plus revenir : l'exclusion ne dépend pas de la réussite de la déconnexion."""
    enter(client, settings, rooms, meeting["organizer_urls"][0])
    first = join(client, meeting["participant_url"], display_name="Lina").json()
    from tests.integration.helpers import claims_of, seat

    lina = seat(rooms, claims_of(settings, first["livekit_token"]))
    # la liste fonctionne, l'ordre de déconnexion échoue
    original = rooms.remove_participant

    def failing_remove(room: str, identity: str) -> None:
        rooms.failing = True
        original(room, identity)

    rooms.remove_participant = failing_remove  # type: ignore[method-assign]
    response = post_room(client, "remove", meeting["organizer_urls"][0], identity=lina.identity)
    assert response.status_code == 502
    again = join(
        client, meeting["participant_url"], display_name="Lina", client_id=first["client_id"]
    )
    assert again.status_code == 403


def test_two_organizers_both_moderate(
    client: TestClient, settings: Settings, rooms: FakeRoomGateway, meeting: dict[str, Any]
) -> None:
    """Formateur et co-animateur : chaque lien organisateur ouvre les mêmes droits."""
    enter(client, settings, rooms, meeting["organizer_urls"][0])
    enter(client, settings, rooms, meeting["organizer_urls"][1])
    lina = enter(client, settings, rooms, meeting["participant_url"], display_name="Lina")
    karim = enter(client, settings, rooms, meeting["participant_url"], display_name="Karim")

    first = post_room(
        client, "mute", meeting["organizer_urls"][0], identity=lina.identity, source="microphone"
    )
    second = post_room(client, "remove", meeting["organizer_urls"][1], identity=karim.identity)
    assert first.status_code == 200
    assert second.status_code == 200
    assert {call[1] for call in rooms.called("mute")} == {lina.identity}
    assert {call[1] for call in rooms.called("remove")} == {karim.identity}


def test_an_organizer_ends_the_meeting_for_everyone(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    rooms: FakeRoomGateway,
    meeting: dict[str, Any],
) -> None:
    enter(client, settings, rooms, meeting["organizer_urls"][0])
    enter(client, settings, rooms, meeting["participant_url"], display_name="Lina")
    room = next(iter(rooms.participants))

    response = post_room(client, "end", meeting["organizer_urls"][0])

    assert response.status_code == 200
    assert rooms.called("delete_room") == [(room,)]
    state = client.get(f"/v1/meetings/{meeting['id']}", headers=auth).json()
    assert state["status"] == "ended"
    assert join(client, meeting["participant_url"], display_name="Lina").status_code == 409
