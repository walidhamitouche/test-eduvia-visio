"""API réunions (étape 4) : création, lecture, modification, annulation, clôture."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openapi_spec_validator import validate
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.conftest import SECRET_API_KEY
from tests.integration.conftest import SLOT_END, SLOT_START, meeting_body
from visio.db import LinkRow, MeetingRow, ParticipantSessionRow
from visio.domain.clock import FixedClock
from visio.domain.meeting import MeetingState
from visio.domain.roles import Role
from visio.domain.slot import can_join, parse_iso8601

Create = Callable[..., dict[str, Any]]


def error_code(response: Any) -> str:
    body = response.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == {"code", "message"}, body
    code: str = body["error"]["code"]
    return code


def at(clock: FixedClock, iso: str) -> None:
    clock.set(parse_iso8601(iso))


def set_state(db: Session, meeting_id: str, state: MeetingState) -> None:
    meeting = db.get(MeetingRow, meeting_id)
    assert meeting is not None
    meeting.state = state
    db.commit()


# --- authentification ---------------------------------------------------------------------


def test_every_meeting_route_requires_the_api_key(client: TestClient) -> None:
    calls = [
        ("POST", "/v1/meetings"),
        ("GET", "/v1/meetings/mtg_x"),
        ("PATCH", "/v1/meetings/mtg_x"),
        ("POST", "/v1/meetings/mtg_x/end"),
        ("DELETE", "/v1/meetings/mtg_x"),
    ]
    for method, path in calls:
        response = client.request(method, path, json={})
        assert response.status_code == 401, (method, path)
        assert error_code(response) == "unauthorized"
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header",
    [
        "Bearer mauvaise-cle",
        "Bearer ",
        f"Basic {SECRET_API_KEY}",
        SECRET_API_KEY,
        f"Bearer {SECRET_API_KEY}x",
        f"Bearer {SECRET_API_KEY[:-1]}",
    ],
)
def test_post_with_wrong_key_is_401_and_creates_nothing(
    client: TestClient, db: Session, header: str
) -> None:
    response = client.post("/v1/meetings", json=meeting_body(), headers={"Authorization": header})
    assert response.status_code == 401
    assert error_code(response) == "unauthorized"
    assert SECRET_API_KEY not in response.text
    assert db.scalar(select(func.count()).select_from(MeetingRow)) == 0


def test_authentication_comes_before_validation(client: TestClient) -> None:
    response = client.post("/v1/meetings", json={"n'importe": "quoi"})
    assert response.status_code == 401


def test_scheme_is_case_insensitive(client: TestClient) -> None:
    response = client.get(
        "/v1/meetings/mtg_x", headers={"Authorization": f"bearer {SECRET_API_KEY}"}
    )
    assert response.status_code == 404  # authentifié : on arrive jusqu'à « introuvable »


def test_health_stays_public(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


def test_production_refuses_plain_http(
    set_env: Callable[[dict[str, str]], None], tmp_path: Any, auth: dict[str, str]
) -> None:
    from tests.conftest import valid_env
    from visio.config import Settings
    from visio.db.migrate import upgrade
    from visio.main import create_app

    db_url = f"sqlite:///{tmp_path / 'prod.db'}"
    set_env({**valid_env("production"), "DATABASE_URL": db_url})
    upgrade(db_url)
    app = create_app(settings=Settings())  # type: ignore[call-arg]
    with TestClient(app) as http_client:
        refused = http_client.get("/v1/meetings/mtg_x", headers=auth)
        assert refused.status_code == 403
        assert error_code(refused) == "https_required"
        # Derrière Coolify, le proxy indique le protocole d'origine.
        proxied = http_client.get(
            "/v1/meetings/mtg_x", headers={**auth, "X-Forwarded-Proto": "https"}
        )
        assert proxied.status_code == 404
    with TestClient(app, base_url="https://testserver") as https_client:
        assert https_client.get("/v1/meetings/mtg_x", headers=auth).status_code == 404


# --- création ----------------------------------------------------------------------------


def test_create_returns_201_with_the_documented_fields(
    client: TestClient, auth: dict[str, str]
) -> None:
    response = client.post("/v1/meetings", json=meeting_body(), headers=auth)
    assert response.status_code == 201
    created = response.json()
    assert created["id"].startswith("mtg_")
    assert response.headers["location"] == f"/v1/meetings/{created['id']}"
    assert created["status"] == "scheduled"
    assert created["title"] == "Cours BTS MCO – Groupe A"
    assert created["starts_at"] == SLOT_START
    assert created["ends_at"] == SLOT_END
    assert created["early_join_minutes"] == 10
    assert created["waiting_room"] is True
    assert created["recording"] is True
    assert created["transcription"] is True
    assert created["metadata"] == {"eduvia_session_id": 1234}
    assert created["participants"] == []
    assert created["started_at"] is None
    assert created["ended_at"] is None
    assert created["participant_url"].startswith("http://visio.test/j/")
    assert len(created["organizer_urls"]) == 1
    assert created["organizers"] == [
        {
            "display_name": "Marie Durand",
            "external_id": "staff_42",
            "url": created["organizer_urls"][0],
        }
    ]


def test_options_default_to_off(client: TestClient, auth: dict[str, str]) -> None:
    body = meeting_body()
    for name in ("early_join_minutes", "waiting_room", "recording", "transcription", "metadata"):
        del body[name]
    response = client.post("/v1/meetings", json=body, headers=auth)
    assert response.status_code == 201
    created = response.json()
    assert created["early_join_minutes"] == 0
    assert (created["waiting_room"], created["recording"], created["transcription"]) == (
        False,
        False,
        False,
    )
    assert created["metadata"] == {}


def test_dates_are_stored_as_instants_and_shown_in_paris_time(
    client: TestClient, auth: dict[str, str], create: Create
) -> None:
    created = create(starts_at="2027-01-02T15:00:00Z", ends_at="2027-01-02T17:00:00+00:00")
    assert created["starts_at"] == "2027-01-02T16:00:00+01:00"
    assert created["ends_at"] == "2027-01-02T18:00:00+01:00"
    summer = create(starts_at="2027-07-01T14:00:00+02:00", ends_at="2027-07-01T15:00:00+02:00")
    assert summer["starts_at"] == "2027-07-01T14:00:00+02:00"  # heure d'été de Paris


def test_links_are_created_with_the_meeting(
    client: TestClient, db: Session, create: Create
) -> None:
    created = create(
        organizers=[
            {"display_name": "Marie Durand", "external_id": "staff_42"},
            {"display_name": "Paul Martin"},
        ]
    )
    links = db.scalars(select(LinkRow).where(LinkRow.meeting_id == created["id"])).all()
    roles = sorted(link.role for link in links)
    assert roles == [Role.ORGANIZER, Role.ORGANIZER, Role.PARTICIPANT]
    generic = next(link for link in links if link.role is Role.PARTICIPANT)
    assert generic.display_name is None
    assert generic.external_id is None
    organizers = {
        link.display_name: link.external_id for link in links if link.role is Role.ORGANIZER
    }
    assert organizers == {"Marie Durand": "staff_42", "Paul Martin": None}


def test_two_organizers_get_two_distinct_secret_links(create: Create) -> None:
    created = create(organizers=[{"display_name": "Marie Durand"}, {"display_name": "Paul Martin"}])
    urls = [*created["organizer_urls"], created["participant_url"]]
    assert len(set(urls)) == 3
    assert [o["display_name"] for o in created["organizers"]] == ["Marie Durand", "Paul Martin"]
    assert [o["url"] for o in created["organizers"]] == created["organizer_urls"]


def test_urls_never_contain_the_meeting_id(create: Create) -> None:
    created = create()
    for url in (created["participant_url"], *created["organizer_urls"]):
        assert created["id"] not in url
        assert created["id"].removeprefix("mtg_") not in url


@pytest.mark.parametrize(
    "overrides",
    [
        {"ends_at": SLOT_START},  # fin = début
        {"ends_at": "2027-01-02T15:00:00+01:00"},  # fin avant le début
        {"starts_at": "2027-01-02T16:00:00"},  # pas de décalage
        {"ends_at": "2027-01-02T18:00:00"},
        {"starts_at": "demain 16h"},  # pas du ISO 8601
        {"ends_at": "2027-01-03T04:00:01+01:00"},  # 12 h et 1 s : trop long
        {"early_join_minutes": 61},
        {"early_join_minutes": -1},
        {"starts_at": "2026-11-01T16:00:00+01:00", "ends_at": "2026-11-01T18:00:00+01:00"},  # passé
    ],
)
def test_incoherent_slot_is_422_invalid_slot_and_creates_nothing(
    client: TestClient, db: Session, auth: dict[str, str], overrides: dict[str, Any]
) -> None:
    response = client.post("/v1/meetings", json=meeting_body(**overrides), headers=auth)
    assert response.status_code == 422
    assert error_code(response) == "invalid_slot"
    assert db.scalar(select(func.count()).select_from(MeetingRow)) == 0
    assert db.scalar(select(func.count()).select_from(LinkRow)) == 0


def test_slot_of_exactly_twelve_hours_is_accepted(create: Create) -> None:
    created = create(starts_at="2027-01-02T08:00:00+01:00", ends_at="2027-01-02T20:00:00+01:00")
    assert created["status"] == "scheduled"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda b: b.pop("title"),
        lambda b: b.pop("starts_at"),
        lambda b: b.pop("organizers"),
        lambda b: b.update(organizers=[]),
        lambda b: b.update(organizers=[{"external_id": "staff_42"}]),
        lambda b: b.update(organizers=[{"display_name": "  "}]),
        lambda b: b.update(title=""),
        lambda b: b.update(title="x" * 201),
        lambda b: b.update(recordng=True),  # faute de frappe : refusée, pas ignorée
        lambda b: b.update(early_join_minutes="10"),  # types stricts
        lambda b: b.update(early_join_minutes=True),
        lambda b: b.update(early_join_minutes=10.5),
        lambda b: b.update(recording="true"),
        lambda b: b.update(recording=1),
        lambda b: b.update(metadata=None),
        lambda b: b.update(metadata=[1, 2]),
        lambda b: b.update(starts_at=20270102),
        lambda b: b.update(organizers=[{"display_name": "A", "external_id": 42}]),
        lambda b: b.update(organizers=[{"display_name": "A"}] * 11),
    ],
)
def test_malformed_body_is_422_validation_error(
    client: TestClient, db: Session, auth: dict[str, str], mutation: Callable[[dict[str, Any]], Any]
) -> None:
    body = meeting_body()
    mutation(body)
    response = client.post("/v1/meetings", json=body, headers=auth)
    assert response.status_code == 422
    assert error_code(response) == "validation_error"
    assert db.scalar(select(func.count()).select_from(MeetingRow)) == 0


def test_invalid_json_is_a_clean_422(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/v1/meetings",
        content=b"{pas du json",
        headers={**auth, "Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert error_code(response) == "validation_error"


def test_validation_message_names_the_field(client: TestClient, auth: dict[str, str]) -> None:
    body = meeting_body()
    del body["title"]
    message = client.post("/v1/meetings", json=body, headers=auth).json()["error"]["message"]
    assert "title" in message


# --- metadata ----------------------------------------------------------------------------

METADATA_SAMPLES: list[dict[str, Any]] = [
    {"eduvia_session_id": 1234},
    {},
    {"a": {"b": [1, 2.5, None, True, "é à ü – 日本"], "c": {}}, "z": 0, "big": 2**53},
    {"zebra": 1, "alpha": 2, "mid": 3},
    {"texte": 'ligne 1\nligne 2 "cité" \\ /'},
]


@pytest.mark.parametrize("metadata", METADATA_SAMPLES)
def test_metadata_is_returned_unchanged_by_post_and_get(
    client: TestClient, auth: dict[str, str], create: Create, metadata: dict[str, Any]
) -> None:
    created = create(metadata=metadata)
    assert created["metadata"] == metadata
    assert list(created["metadata"]) == list(metadata)  # même ordre des clés
    fetched = client.get(f"/v1/meetings/{created['id']}", headers=auth).json()
    assert fetched["metadata"] == metadata
    assert json.dumps(fetched["metadata"]) == json.dumps(metadata)


def test_metadata_rejects_nan_and_oversize(client: TestClient, auth: dict[str, str]) -> None:
    raw = json.dumps(meeting_body(metadata={"x": 1})).replace('{"x": 1}', '{"x": NaN}')
    nan = client.post(
        "/v1/meetings", content=raw, headers={**auth, "Content-Type": "application/json"}
    )
    assert nan.status_code == 422
    assert error_code(nan) == "validation_error"
    big = client.post("/v1/meetings", json=meeting_body(metadata={"x": "a" * 20_000}), headers=auth)
    assert big.status_code == 422
    assert error_code(big) == "validation_error"


# --- lecture -----------------------------------------------------------------------------


def test_get_returns_state_options_and_no_link(
    client: TestClient, auth: dict[str, str], create: Create
) -> None:
    created = create()
    response = client.get(f"/v1/meetings/{created['id']}", headers=auth)
    assert response.status_code == 200
    fetched = response.json()
    assert fetched["status"] == "scheduled"
    assert fetched["waiting_room"] is True
    assert "participant_url" not in fetched
    assert "organizer_urls" not in fetched
    assert "organizers" not in fetched
    expected = {k: v for k, v in created.items() if k in fetched}
    assert fetched == expected


def test_get_unknown_meeting_is_404_meeting_not_found(
    client: TestClient, auth: dict[str, str]
) -> None:
    response = client.get("/v1/meetings/mtg_inconnu", headers=auth)
    assert response.status_code == 404
    assert error_code(response) == "meeting_not_found"


def test_unknown_route_is_a_json_404_not_an_empty_room(client: TestClient) -> None:
    response = client.get("/une-url-inventee")
    assert response.status_code == 404
    assert error_code(response) == "not_found"


def test_wrong_method_is_a_json_405(client: TestClient, auth: dict[str, str]) -> None:
    response = client.put("/v1/meetings", headers=auth)
    assert response.status_code == 405
    assert error_code(response) == "method_not_allowed"


def test_get_lists_connected_participants_only(
    client: TestClient, db: Session, auth: dict[str, str], create: Create
) -> None:
    created = create()
    now = parse_iso8601("2027-01-02T16:05:00+01:00")
    db.add_all(
        [
            ParticipantSessionRow(
                meeting_id=created["id"],
                participant_identity="p1",
                display_name="Alice",
                external_id="stu_1",
                role=Role.PARTICIPANT,
                joined_at=now,
            ),
            ParticipantSessionRow(
                meeting_id=created["id"],
                participant_identity="p2",
                display_name="Bob (parti)",
                role=Role.PARTICIPANT,
                joined_at=now,
                left_at=now + timedelta(minutes=5),
            ),
        ]
    )
    db.commit()
    participants = client.get(f"/v1/meetings/{created['id']}", headers=auth).json()["participants"]
    assert participants == [
        {
            "display_name": "Alice",
            "external_id": "stu_1",
            "role": "participant",
            "joined_at": "2027-01-02T16:05:00+01:00",
        }
    ]


def test_status_becomes_ended_once_the_slot_is_over(
    client: TestClient, auth: dict[str, str], create: Create, clock: FixedClock
) -> None:
    created = create()
    url = f"/v1/meetings/{created['id']}"
    at(clock, "2027-01-02T17:59:59+01:00")
    assert client.get(url, headers=auth).json()["status"] == "scheduled"
    at(clock, "2027-01-02T18:00:00+01:00")
    assert client.get(url, headers=auth).json()["status"] == "ended"


# --- modification ------------------------------------------------------------------------


def test_patch_changes_only_the_fields_sent(
    client: TestClient, auth: dict[str, str], create: Create
) -> None:
    created = create()
    url = f"/v1/meetings/{created['id']}"
    response = client.patch(url, json={"title": "Nouveau titre", "recording": False}, headers=auth)
    assert response.status_code == 200
    patched = response.json()
    assert patched["title"] == "Nouveau titre"
    assert patched["recording"] is False
    for unchanged in ("starts_at", "ends_at", "waiting_room", "transcription", "metadata"):
        assert patched[unchanged] == created[unchanged]
    assert client.get(url, headers=auth).json() == patched


def test_patch_can_move_the_slot(client: TestClient, auth: dict[str, str], create: Create) -> None:
    created = create()
    response = client.patch(
        f"/v1/meetings/{created['id']}",
        json={
            "starts_at": "2027-01-03T09:00:00+01:00",
            "ends_at": "2027-01-03T10:30:00+01:00",
            "early_join_minutes": 0,
        },
        headers=auth,
    )
    assert response.status_code == 200
    assert response.json()["starts_at"] == "2027-01-03T09:00:00+01:00"
    assert response.json()["early_join_minutes"] == 0


def test_patch_metadata_replaces_the_whole_object(
    client: TestClient, auth: dict[str, str], create: Create
) -> None:
    created = create(metadata={"a": 1, "b": 2})
    response = client.patch(
        f"/v1/meetings/{created['id']}", json={"metadata": {"c": 3}}, headers=auth
    )
    assert response.json()["metadata"] == {"c": 3}


def test_patch_validates_the_resulting_slot(
    client: TestClient, auth: dict[str, str], create: Create
) -> None:
    created = create()
    url = f"/v1/meetings/{created['id']}"
    # ends_at seul, mais avant le starts_at déjà enregistré
    response = client.patch(url, json={"ends_at": "2027-01-02T15:00:00+01:00"}, headers=auth)
    assert response.status_code == 422
    assert error_code(response) == "invalid_slot"
    response = client.patch(url, json={"early_join_minutes": 90}, headers=auth)
    assert error_code(response) == "invalid_slot"
    response = client.patch(url, json={"starts_at": "2027-01-02T16:00:00"}, headers=auth)
    assert error_code(response) == "invalid_slot"
    # rien n'a bougé
    assert client.get(url, headers=auth).json()["ends_at"] == created["ends_at"]


@pytest.mark.parametrize(
    "body",
    [
        {"title": None},
        {"starts_at": None},
        {"metadata": None},
        {"organizers": [{"display_name": "X"}]},  # pas modifiable ici
        {"status": "ended"},
        {"id": "mtg_autre"},
        {"waiting_room": "oui"},
        {"title": ""},
    ],
)
def test_patch_rejects_unknown_null_or_badly_typed_fields(
    client: TestClient, auth: dict[str, str], create: Create, body: dict[str, Any]
) -> None:
    created = create()
    response = client.patch(f"/v1/meetings/{created['id']}", json=body, headers=auth)
    assert response.status_code == 422
    assert error_code(response) == "validation_error"


def test_patch_with_nothing_to_change_is_a_harmless_200(
    client: TestClient, auth: dict[str, str], create: Create
) -> None:
    created = create()
    response = client.patch(f"/v1/meetings/{created['id']}", json={}, headers=auth)
    assert response.status_code == 200
    assert response.json()["title"] == created["title"]


def test_patch_after_start_is_409_meeting_already_started(
    client: TestClient, auth: dict[str, str], create: Create, clock: FixedClock
) -> None:
    created = create()
    url = f"/v1/meetings/{created['id']}"
    at(clock, "2027-01-02T16:00:00+01:00")  # starts_at atteint, personne connectée
    response = client.patch(url, json={"title": "trop tard"}, headers=auth)
    assert response.status_code == 409
    assert error_code(response) == "meeting_already_started"
    assert client.get(url, headers=auth).json()["title"] == created["title"]


def test_patch_when_someone_already_connected_is_409(
    client: TestClient, db: Session, auth: dict[str, str], create: Create
) -> None:
    created = create()
    set_state(db, created["id"], MeetingState.LIVE)  # connexion pendant la marge d'ouverture
    response = client.patch(
        f"/v1/meetings/{created['id']}", json={"title": "trop tard"}, headers=auth
    )
    assert response.status_code == 409
    assert error_code(response) == "meeting_already_started"


def test_patch_one_second_before_start_still_works(
    client: TestClient, auth: dict[str, str], create: Create, clock: FixedClock
) -> None:
    created = create()
    at(clock, "2027-01-02T15:59:59+01:00")
    response = client.patch(f"/v1/meetings/{created['id']}", json={"title": "ok"}, headers=auth)
    assert response.status_code == 200


def test_patch_on_cancelled_and_unknown_meetings(
    client: TestClient, auth: dict[str, str], create: Create
) -> None:
    created = create()
    url = f"/v1/meetings/{created['id']}"
    client.delete(url, headers=auth)
    response = client.patch(url, json={"title": "x"}, headers=auth)
    assert response.status_code == 409
    assert error_code(response) == "meeting_cancelled"
    response = client.patch("/v1/meetings/mtg_inconnu", json={"title": "x"}, headers=auth)
    assert response.status_code == 404
    assert error_code(response) == "meeting_not_found"


# --- annulation --------------------------------------------------------------------------


def test_delete_of_a_meeting_not_started_cancels_it(
    client: TestClient, auth: dict[str, str], create: Create
) -> None:
    created = create()
    url = f"/v1/meetings/{created['id']}"
    response = client.delete(url, headers=auth)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert client.get(url, headers=auth).json()["status"] == "cancelled"


def test_delete_can_be_retried(client: TestClient, auth: dict[str, str], create: Create) -> None:
    created = create()
    url = f"/v1/meetings/{created['id']}"
    assert client.delete(url, headers=auth).status_code == 200
    again = client.delete(url, headers=auth)
    assert again.status_code == 200
    assert again.json()["status"] == "cancelled"


def test_delete_after_start_is_409(
    client: TestClient, auth: dict[str, str], create: Create, clock: FixedClock
) -> None:
    created = create()
    url = f"/v1/meetings/{created['id']}"
    at(clock, "2027-01-02T16:00:00+01:00")
    response = client.delete(url, headers=auth)
    assert response.status_code == 409
    assert error_code(response) == "meeting_already_started"
    assert client.get(url, headers=auth).json()["status"] == "scheduled"


def test_delete_of_a_live_meeting_is_409(
    client: TestClient, db: Session, auth: dict[str, str], create: Create
) -> None:
    created = create()
    set_state(db, created["id"], MeetingState.LIVE)
    response = client.delete(f"/v1/meetings/{created['id']}", headers=auth)
    assert response.status_code == 409
    assert error_code(response) == "meeting_already_started"


def test_delete_of_an_ended_meeting_is_409(
    client: TestClient, auth: dict[str, str], create: Create, clock: FixedClock
) -> None:
    created = create()
    at(clock, "2027-01-02T19:00:00+01:00")
    response = client.delete(f"/v1/meetings/{created['id']}", headers=auth)
    assert response.status_code == 409
    assert error_code(response) == "meeting_already_started"


def test_delete_unknown_meeting_is_404(client: TestClient, auth: dict[str, str]) -> None:
    response = client.delete("/v1/meetings/mtg_inconnu", headers=auth)
    assert response.status_code == 404
    assert error_code(response) == "meeting_not_found"


def test_a_cancelled_meeting_can_no_longer_be_joined(
    client: TestClient, db: Session, auth: dict[str, str], create: Create, clock: FixedClock
) -> None:
    created = create()
    client.delete(f"/v1/meetings/{created['id']}", headers=auth)
    at(clock, "2027-01-02T17:00:00+01:00")  # en plein créneau
    row = db.get(MeetingRow, created["id"])
    assert row is not None
    assert not can_join(row.to_domain(), clock.now())


# --- clôture -----------------------------------------------------------------------------


def test_end_closes_a_live_meeting_immediately(
    client: TestClient, db: Session, auth: dict[str, str], create: Create, clock: FixedClock
) -> None:
    created = create()
    set_state(db, created["id"], MeetingState.LIVE)
    at(clock, "2027-01-02T17:00:00+01:00")
    response = client.post(f"/v1/meetings/{created['id']}/end", headers=auth)
    assert response.status_code == 200
    ended = response.json()
    assert ended["status"] == "ended"
    assert ended["ended_at"] == "2027-01-02T17:00:00+01:00"
    # « Réunion terminée par l'organisateur à 17:00 : refusée à 17:01 même dans le créneau »
    db.expire_all()
    row = db.get(MeetingRow, created["id"])
    assert row is not None
    at(clock, "2027-01-02T17:01:00+01:00")
    assert not can_join(row.to_domain(), clock.now())


def test_end_works_on_an_open_meeting_nobody_joined(
    client: TestClient, auth: dict[str, str], create: Create, clock: FixedClock
) -> None:
    # Décision 10 : passé `starts_at`, DELETE est refusé mais /end reste possible.
    created = create()
    at(clock, "2027-01-02T16:30:00+01:00")
    response = client.post(f"/v1/meetings/{created['id']}/end", headers=auth)
    assert response.status_code == 200
    assert response.json()["status"] == "ended"


def test_end_works_during_the_early_join_margin(
    client: TestClient, auth: dict[str, str], create: Create, clock: FixedClock
) -> None:
    created = create()  # marge de 10 min : ouverte dès 15:50
    at(clock, "2027-01-02T15:50:00+01:00")
    response = client.post(f"/v1/meetings/{created['id']}/end", headers=auth)
    assert response.status_code == 200
    assert response.json()["status"] == "ended"


def test_end_before_opening_is_409_meeting_not_started(
    client: TestClient, auth: dict[str, str], create: Create, clock: FixedClock
) -> None:
    created = create()
    at(clock, "2027-01-02T15:49:59+01:00")
    response = client.post(f"/v1/meetings/{created['id']}/end", headers=auth)
    assert response.status_code == 409
    assert error_code(response) == "meeting_not_started"
    assert client.get(f"/v1/meetings/{created['id']}", headers=auth).json()["status"] == "scheduled"


def test_end_can_be_retried_and_keeps_the_first_closing_time(
    client: TestClient, auth: dict[str, str], create: Create, clock: FixedClock
) -> None:
    created = create()
    url = f"/v1/meetings/{created['id']}/end"
    at(clock, "2027-01-02T17:00:00+01:00")
    first = client.post(url, headers=auth).json()
    at(clock, "2027-01-02T17:10:00+01:00")
    second = client.post(url, headers=auth)
    assert second.status_code == 200
    assert second.json()["ended_at"] == first["ended_at"] == "2027-01-02T17:00:00+01:00"


def test_end_after_the_slot_changes_nothing(
    client: TestClient, auth: dict[str, str], create: Create, clock: FixedClock
) -> None:
    created = create()
    at(clock, "2027-01-02T18:30:00+01:00")
    response = client.post(f"/v1/meetings/{created['id']}/end", headers=auth)
    assert response.status_code == 200
    assert response.json()["status"] == "ended"
    assert response.json()["ended_at"] is None  # fin normale, pas de clôture anticipée


def test_end_of_cancelled_or_unknown_meeting(
    client: TestClient, auth: dict[str, str], create: Create
) -> None:
    created = create()
    client.delete(f"/v1/meetings/{created['id']}", headers=auth)
    response = client.post(f"/v1/meetings/{created['id']}/end", headers=auth)
    assert response.status_code == 409
    assert error_code(response) == "meeting_cancelled"
    response = client.post("/v1/meetings/mtg_inconnu/end", headers=auth)
    assert response.status_code == 404
    assert error_code(response) == "meeting_not_found"


# --- OpenAPI -----------------------------------------------------------------------------


def test_openapi_spec_is_valid_and_documents_the_api(app: FastAPI, client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    validate(spec)  # validateur officiel OpenAPI 3.1
    paths = spec["paths"]
    assert set(paths["/v1/meetings"]) == {"post"}
    assert set(paths["/v1/meetings/{meeting_id}"]) == {"get", "patch", "delete"}
    assert set(paths["/v1/meetings/{meeting_id}/end"]) == {"post"}
    assert "/health" in paths
    assert spec["components"]["securitySchemes"]["ApiKey"]["scheme"] == "bearer"
    post = paths["/v1/meetings"]["post"]
    assert post["security"] == [{"ApiKey": []}]
    assert {"201", "401", "422"} <= set(post["responses"])
    assert {"409", "404"} <= set(paths["/v1/meetings/{meeting_id}"]["patch"]["responses"])
    assert "/health" in paths and "security" not in paths["/health"]["get"]


# --- horloge et base ---------------------------------------------------------------------


def test_the_application_uses_the_injected_clock(
    client: TestClient, auth: dict[str, str], create: Create, clock: FixedClock
) -> None:
    """Création d'une réunion dont la fin est « passée » : dépend de l'horloge, pas du système."""
    at(clock, "2027-01-02T19:00:00+01:00")
    response = client.post("/v1/meetings", json=meeting_body(), headers=auth)
    assert response.status_code == 422
    assert error_code(response) == "invalid_slot"
    at(clock, "2027-01-02T17:00:00+01:00")  # créneau en cours : création acceptée
    assert client.post("/v1/meetings", json=meeting_body(), headers=auth).status_code == 201


def test_every_v1_operation_is_documented_and_webhooks_are_in_the_spec(client: TestClient) -> None:
    """OpenAPI complète (étape 21b) : résumé et description partout, webhooks décrits."""
    spec = client.get("/openapi.json").json()
    missing = [
        f"{method.upper()} {path}"
        for path, item in spec["paths"].items()
        if path.startswith("/v1")
        for method, operation in item.items()
        if not operation.get("summary") or not operation.get("description")
    ]
    assert missing == []
    assert len(spec["webhooks"]) == 8
