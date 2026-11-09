"""Présence et chat (étape 11) : webhooks LiveKit signés, émargement, discussion conservée.

Scénario 7 côté API : le chat et la présence (entrées et sorties) sont exportables et cohérents
avec la séance ; scénario 5 : le nom d'un lien nominatif se retrouve dans la présence.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.integration.conftest import RecordingSender, meeting_body
from tests.integration.helpers import (
    at,
    claims_of,
    enter,
    error_code,
    join,
    livekit_webhook,
    post_room,
    seat,
    tick,
    token_of,
    utc_seconds,
)
from visio.config import Settings
from visio.db.models import ParticipantSessionRow
from visio.domain.clock import FixedClock
from visio.domain.meeting import MeetingState
from visio.services.rooms import FakeRoomGateway


@pytest.fixture
def meeting(client: TestClient, auth: dict[str, str], clock: FixedClock) -> dict[str, Any]:
    response = client.post(
        "/v1/meetings", json=meeting_body(waiting_room=False, early_join_minutes=0), headers=auth
    )
    assert response.status_code == 201
    at(clock, "16:00:00")
    created: dict[str, Any] = response.json()
    return created


def connect(
    client: TestClient,
    settings: Settings,
    url: str,
    when: str,
    sid: str,
    **extra: Any,
) -> dict[str, Any]:
    """Jeton émis (join) puis webhook `participant_joined` à l'heure `when` (Paris)."""
    body = join(client, url, **extra).json()
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


def disconnect(
    client: TestClient, settings: Settings, claims: dict[str, Any], when: str, sid: str
) -> None:
    response = livekit_webhook(
        client, settings, "participant_left", claims, sid=sid, created_at=utc_seconds(when)
    )
    assert response.status_code == 200, response.text


def events(sender: RecordingSender) -> list[dict[str, Any]]:
    return [json.loads(body) for _, _, body in sender.calls]


# --- le webhook de LiveKit est authentifié ------------------------------------------------------


def test_an_unsigned_webhook_is_refused(client: TestClient) -> None:
    response = client.post("/v1/livekit/webhook", json={"event": "participant_joined"})
    assert response.status_code == 401


def test_a_webhook_signed_with_another_secret_or_key_is_refused(
    client: TestClient, settings: Settings, meeting: dict[str, Any]
) -> None:
    claims = claims_of(settings, join(client, meeting["organizer_urls"][0]).json()["livekit_token"])
    wrong_secret = livekit_webhook(
        client, settings, "participant_joined", claims, secret="autre-secret-" + "x" * 40
    )
    wrong_key = livekit_webhook(client, settings, "participant_joined", claims, key="autre-cle")
    assert wrong_secret.status_code == 401
    assert wrong_key.status_code == 401


def test_a_webhook_whose_body_was_modified_is_refused(
    client: TestClient, settings: Settings, meeting: dict[str, Any]
) -> None:
    claims = claims_of(settings, join(client, meeting["organizer_urls"][0]).json()["livekit_token"])
    response = livekit_webhook(client, settings, "participant_joined", claims, tamper=True)
    assert response.status_code == 401


def test_events_for_unknown_rooms_or_other_participants_are_ignored(
    client: TestClient, settings: Settings, meeting: dict[str, Any], db: Session
) -> None:
    claims = claims_of(settings, join(client, meeting["organizer_urls"][0]).json()["livekit_token"])
    unknown = livekit_webhook(client, settings, "participant_joined", claims, room="rm_" + "0" * 32)
    assert unknown.json() == {"result": "unknown-room"}
    # un enregistreur (étape 14) n'est pas une personne : jamais déconnecté, jamais compté
    recorder = livekit_webhook(
        client, settings, "participant_joined", claims, identity="EG_recorder123"
    )
    assert recorder.json() == {"result": "not-a-person"}
    assert db.query(ParticipantSessionRow).count() == 0


# --- présence ----------------------------------------------------------------------------------


def test_first_entry_starts_the_meeting_and_announces_it(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    sender: RecordingSender,
    meeting: dict[str, Any],
) -> None:
    connect(client, settings, meeting["organizer_urls"][0], "16:00:10", "PA_1")
    connect(client, settings, meeting["participant_url"], "16:01:00", "PA_2", display_name="Lina")
    tick(client)

    state = client.get(f"/v1/meetings/{meeting['id']}", headers=auth).json()
    assert state["status"] == "live"
    assert state["started_at"] == "2027-01-02T16:00:10+01:00"
    assert [(p["display_name"], p["role"]) for p in state["participants"]] == [
        ("Marie Durand", "organizer"),
        ("Lina", "participant"),
    ]
    assert [e["type"] for e in events(sender)] == [
        "meeting.started",
        "participant.joined",
        "participant.joined",
    ]


def named_participant(
    client: TestClient, auth: dict[str, str], meeting: dict[str, Any], name: str, external_id: str
) -> str:
    response = client.post(
        f"/v1/meetings/{meeting['id']}/links",
        json={"role": "participant", "display_name": name, "external_id": external_id},
        headers=auth,
    )
    assert response.status_code == 201
    return str(response.json()["url"])


def test_a_participant_who_enters_leaves_and_comes_back_has_two_intervals(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    settings: Settings,
    meeting: dict[str, Any],
) -> None:
    url = named_participant(client, auth, meeting, "Lina Benali", "stu_981")
    first = connect(client, settings, url, "16:00:00", "PA_a")
    disconnect(client, settings, first, "16:20:00", "PA_a")
    connect(client, settings, url, "16:40:00", "PA_b")
    disconnect(client, settings, first, "16:55:00", "PA_b")
    at(clock, "17:00:00")

    report = client.get(f"/v1/meetings/{meeting['id']}/attendance", headers=auth).json()

    [lina] = report["participants"]
    # scénario 5 : le nom et l'identifiant externe du lien nominatif sont dans la présence
    assert (lina["display_name"], lina["external_id"], lina["role"]) == (
        "Lina Benali",
        "stu_981",
        "participant",
    )
    spans = [(x["joined_at"][11:19], x["left_at"][11:19], x["seconds"]) for x in lina["sessions"]]
    assert spans == [("16:00:00", "16:20:00", 1200), ("16:40:00", "16:55:00", 900)]
    assert lina["total_seconds"] == 2100  # la somme des intervalles, pas dernière - première
    assert lina["last_left_at"] == "2027-01-02T16:55:00+01:00"


def test_two_overlapping_connections_of_the_same_person_count_once(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    settings: Settings,
    meeting: dict[str, Any],
) -> None:
    url = named_participant(client, auth, meeting, "Lina Benali", "stu_981")
    claims = connect(client, settings, url, "16:00:00", "PA_a")
    # deuxième onglet : la nouvelle connexion arrive avant la sortie de l'ancienne
    connect(client, settings, url, "16:10:00", "PA_b")
    disconnect(client, settings, claims, "16:10:01", "PA_a")  # l'ancienne est évincée
    disconnect(client, settings, claims, "16:30:00", "PA_b")
    at(clock, "17:00:00")

    [lina] = client.get(f"/v1/meetings/{meeting['id']}/attendance", headers=auth).json()[
        "participants"
    ]
    assert len(lina["sessions"]) == 2
    assert lina["total_seconds"] == 30 * 60  # 16:00 -> 16:30, sans compter le chevauchement


def test_someone_still_connected_has_no_exit_and_counts_until_now(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    settings: Settings,
    meeting: dict[str, Any],
) -> None:
    connect(client, settings, meeting["organizer_urls"][0], "16:00:00", "PA_1")
    at(clock, "16:45:00")
    [marie] = client.get(f"/v1/meetings/{meeting['id']}/attendance", headers=auth).json()[
        "participants"
    ]
    assert marie["last_left_at"] is None
    assert marie["sessions"][0]["left_at"] is None
    assert marie["total_seconds"] == 45 * 60


def test_a_replayed_webhook_does_not_create_a_second_session_nor_event(
    client: TestClient,
    settings: Settings,
    sender: RecordingSender,
    db: Session,
    meeting: dict[str, Any],
) -> None:
    claims = connect(client, settings, meeting["organizer_urls"][0], "16:00:00", "PA_1")
    again = livekit_webhook(
        client, settings, "participant_joined", claims, sid="PA_1",
        created_at=utc_seconds("16:00:00"), joined_at=utc_seconds("16:00:00"),
    )  # fmt: skip
    assert again.json() == {"result": "duplicate"}
    tick(client)
    assert db.query(ParticipantSessionRow).count() == 1
    assert [e["type"] for e in events(sender)].count("participant.joined") == 1


def test_a_left_event_without_an_open_session_is_harmless(
    client: TestClient, settings: Settings, meeting: dict[str, Any]
) -> None:
    claims = claims_of(settings, join(client, meeting["organizer_urls"][0]).json()["livekit_token"])
    response = livekit_webhook(client, settings, "participant_left", claims, sid="PA_x")
    assert response.json() == {"result": "no-open-session"}


def test_an_excluded_person_who_reuses_a_still_valid_token_is_thrown_out_at_once(
    client: TestClient,
    settings: Settings,
    rooms: FakeRoomGateway,
    db: Session,
    meeting: dict[str, Any],
    auth: dict[str, str],
) -> None:
    """Le jeton de salle vit 10 minutes : exclue, la personne pourrait se reconnecter avec lui
    sans repasser par `/v1/join`. Le webhook de connexion la déconnecte immédiatement."""
    url = named_participant(client, auth, meeting, "Lina Benali", "stu_981")
    enter(client, settings, rooms, meeting["organizer_urls"][0])
    old_token_claims = claims_of(settings, join(client, url).json()["livekit_token"])
    lina = seat(rooms, old_token_claims)
    post_room(client, "remove", meeting["organizer_urls"][0], identity=lina.identity)
    rooms.calls.clear()

    response = livekit_webhook(
        client, settings, "participant_joined", old_token_claims, sid="PA_back",
        created_at=utc_seconds("16:05:00"), joined_at=utc_seconds("16:05:00"),
    )  # fmt: skip

    assert response.json() == {"result": "refused"}
    assert [c[1][1] for c in rooms.calls if c[0] == "remove"] == [lina.identity]
    assert db.query(ParticipantSessionRow).count() == 0  # jamais comptée présente


def test_a_person_who_was_never_admitted_is_thrown_out_too(
    client: TestClient, auth: dict[str, str], settings: Settings, rooms: FakeRoomGateway,
    clock: FixedClock,
) -> None:  # fmt: skip
    waiting = client.post("/v1/meetings", json=meeting_body(waiting_room=True), headers=auth).json()
    at(clock, "16:30:00")
    admitted = join(client, waiting["organizer_urls"][0]).json()
    claims = claims_of(settings, admitted["livekit_token"])
    assert join(client, waiting["participant_url"], display_name="Intrus").json()["status"] == (
        "waiting"
    )
    # un jeton de participant fabriqué à la main ne passerait pas la signature de LiveKit ;
    # ici on simule le pire : le serveur de médias annonce quand même une entrée non admise.
    forged = dict(claims)
    forged["sub"] = "pg_" + "a" * 32
    forged["metadata"] = json.dumps({"role": "participant", "external_id": None})
    response = livekit_webhook(client, settings, "participant_joined", forged, sid="PA_z")
    assert response.json() == {"result": "refused"}
    assert any(c[0] == "remove" for c in rooms.calls)


def test_the_meeting_state_is_only_live_after_a_real_connection(
    client: TestClient, settings: Settings, db: Session, meeting: dict[str, Any]
) -> None:
    join(client, meeting["organizer_urls"][0])  # jeton émis, personne n'est connecté
    assert db.query(ParticipantSessionRow).count() == 0
    from visio.db.models import MeetingRow

    db.expire_all()
    assert db.query(MeetingRow).one().state is MeetingState.SCHEDULED


# --- chat --------------------------------------------------------------------------------------


def send(client: TestClient, url: str, text: str, **extra: Any) -> Any:
    return client.post("/v1/room/chat", json={"token": token_of(url), "text": text, **extra})


def test_a_message_is_stored_with_the_verified_author_and_broadcast(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    rooms: FakeRoomGateway,
    meeting: dict[str, Any],
) -> None:
    url = named_participant(client, auth, meeting, "Lina Benali", "stu_981")
    enter(client, settings, rooms, url)
    room = next(iter(rooms.participants))

    response = send(client, url, "Bonjour à tous")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == "Bonjour à tous"
    assert body["author"] == {
        "display_name": "Lina Benali",
        "external_id": "stu_981",
        "role": "participant",
    }
    [(call_room, topic, payload, identities)] = rooms.called("send_data")
    assert (call_room, topic, identities) == (room, "chat", None)  # à toute la salle
    assert payload["from"] == "Lina Benali"
    assert payload["text"] == "Bonjour à tous"
    assert payload["id"] == body["id"]


def test_nobody_can_choose_the_author_of_a_message(
    client: TestClient, settings: Settings, rooms: FakeRoomGateway, meeting: dict[str, Any]
) -> None:
    enter(client, settings, rooms, meeting["participant_url"], display_name="Lina")
    client_id = join(client, meeting["participant_url"], display_name="Lina").json()["client_id"]
    for extra in ({"from": "Marie Durand"}, {"author": "Marie Durand"}, {"role": "organizer"}):
        response = send(client, meeting["participant_url"], "texte", client_id=client_id, **extra)
        assert response.status_code == 422  # champ inconnu refusé


def test_the_chat_export_has_author_time_and_message_in_order(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    settings: Settings,
    rooms: FakeRoomGateway,
    meeting: dict[str, Any],
) -> None:
    lina_url = named_participant(client, auth, meeting, "Lina Benali", "stu_981")
    enter(client, settings, rooms, meeting["organizer_urls"][0])
    enter(client, settings, rooms, lina_url)
    script = [
        ("16:05:00", meeting["organizer_urls"][0], "Bonjour, on commence"),
        ("16:05:30", lina_url, "Bonjour Marie"),
        ("16:06:00", meeting["organizer_urls"][0], "Ouvrez le support page 3"),
    ]
    for when, url, text in script:
        at(clock, when)
        assert send(client, url, text).status_code == 200

    export = client.get(f"/v1/meetings/{meeting['id']}/chat", headers=auth).json()

    messages = export["messages"]
    assert [m["text"] for m in messages] == [t for _, _, t in script]
    assert [m["sent_at"][11:19] for m in messages] == [w for w, _, _ in script]
    assert [(m["author"]["display_name"], m["author"]["role"]) for m in messages] == [
        ("Marie Durand", "organizer"),
        ("Lina Benali", "participant"),
        ("Marie Durand", "organizer"),
    ]
    assert messages[1]["author"]["external_id"] == "stu_981"


def test_a_stranger_or_an_excluded_person_cannot_write(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    rooms: FakeRoomGateway,
    meeting: dict[str, Any],
) -> None:
    # jamais entré : pas de ligne d'accès -> refusé
    nobody = send(client, meeting["participant_url"], "salut", client_id="a" * 32)
    assert nobody.status_code == 403
    assert error_code(nobody) == "not_admitted"
    # exclue : le lien est révoqué, donc refusé
    url = named_participant(client, auth, meeting, "Lina Benali", "stu_981")
    enter(client, settings, rooms, meeting["organizer_urls"][0])
    lina = enter(client, settings, rooms, url)
    post_room(client, "remove", meeting["organizer_urls"][0], identity=lina.identity)
    banned = send(client, url, "je reviens")
    assert banned.status_code == 403
    assert error_code(banned) == "participant_excluded"


def test_chat_is_refused_outside_the_slot_and_with_bad_text(
    client: TestClient,
    clock: FixedClock,
    settings: Settings,
    rooms: FakeRoomGateway,
    meeting: dict[str, Any],
) -> None:
    url = meeting["organizer_urls"][0]
    enter(client, settings, rooms, url)
    assert send(client, url, "   ").status_code == 422
    assert send(client, url, "x" * 2001).status_code == 422
    assert send(client, url, "x" * 2000).status_code == 200
    at(clock, "18:00:00")
    late = send(client, url, "trop tard")
    assert late.status_code == 409
    assert error_code(late) == "meeting_ended"


def test_a_message_is_kept_even_if_the_live_broadcast_fails(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    rooms: FakeRoomGateway,
    meeting: dict[str, Any],
) -> None:
    url = meeting["organizer_urls"][0]
    enter(client, settings, rooms, url)
    rooms.failing = True
    assert send(client, url, "important").status_code == 200
    export = client.get(f"/v1/meetings/{meeting['id']}/chat", headers=auth).json()
    assert [m["text"] for m in export["messages"]] == ["important"]


def test_exports_need_the_api_key_and_a_known_meeting(
    client: TestClient, auth: dict[str, str], meeting: dict[str, Any]
) -> None:
    for route in ("attendance", "chat"):
        assert client.get(f"/v1/meetings/{meeting['id']}/{route}").status_code == 401
        missing = client.get(f"/v1/meetings/mtg_inconnu/{route}", headers=auth)
        assert missing.status_code == 404
        assert error_code(missing) == "meeting_not_found"
