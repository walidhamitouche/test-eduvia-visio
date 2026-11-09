"""`POST /v1/join` (étape 6) : du lien au jeton LiveKit, en respectant le créneau.

Scénario 1 du besoin (16 h - 18 h) côté API, scénario 2 en HTTP (URL inventée ou modifiée), droits
et expiration du jeton LiveKit émis.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import valid_env
from tests.integration.conftest import SLOT_END, meeting_body
from visio.config import Settings
from visio.db.migrate import upgrade
from visio.domain.clock import FixedClock
from visio.domain.slot import parse_iso8601
from visio.main import create_app
from visio.services.links import revoke_link

Create = Callable[..., dict[str, Any]]

DAY = "2027-01-02"


@pytest.fixture
def create(client: TestClient, auth: dict[str, str]) -> Create:
    """Ici la salle est ouverte (`waiting_room: false`) : la salle d'attente a ses propres tests
    (`test_lobby.py`). Le reste est la réunion type de `tests/integration/conftest.py`."""

    def _create(**overrides: Any) -> dict[str, Any]:
        body = meeting_body(waiting_room=False, **overrides)
        response = client.post("/v1/meetings", json=body, headers=auth)
        assert response.status_code == 201, response.text
        created: dict[str, Any] = response.json()
        return created

    return _create


def at(clock: FixedClock, time: str) -> None:
    """Place l'horloge simulée à `time` (heure de Paris) le 2 janvier 2027."""
    clock.set(parse_iso8601(f"{DAY}T{time}+01:00"))


def token_of(url: str) -> str:
    assert "/j/" in url
    return url.rsplit("/j/", 1)[1]


def join(client: TestClient, token: str, **extra: Any) -> Any:
    client.cookies.clear()  # chaque appel = un navigateur à part (cookie non rejoué)
    return client.post("/v1/join", json={"token": token, **extra})


def decode_livekit(settings: Settings, token: str) -> dict[str, Any]:
    claims: dict[str, Any] = jwt.decode(
        token,
        settings.livekit_api_secret.get_secret_value(),
        algorithms=["HS256"],
        options={"verify_exp": False, "verify_nbf": False, "verify_iat": False},
    )
    return claims


def error_of(response: Any) -> dict[str, Any]:
    error: dict[str, Any] = response.json()["error"]
    return error


def named_link(
    client: TestClient, auth: dict[str, str], meeting_id: str, **body: Any
) -> dict[str, Any]:
    response = client.post(f"/v1/meetings/{meeting_id}/links", json=body, headers=auth)
    assert response.status_code == 201, response.text
    link: dict[str, Any] = response.json()
    return link


# --- scénario 1 : le créneau décide ------------------------------------------------------------


def test_scenario_1_slot_without_margin(
    client: TestClient, clock: FixedClock, create: Create
) -> None:
    token = token_of(create(early_join_minutes=0)["participant_url"])

    at(clock, "15:55:00")
    early = join(client, token, display_name="Lina")
    assert early.status_code == 409
    assert error_of(early)["code"] == "not_open_yet"
    assert error_of(early)["opens_at"] == "2027-01-02T16:00:00+01:00"
    assert "livekit_token" not in early.text

    at(clock, "15:59:59")
    assert join(client, token, display_name="Lina").status_code == 409

    at(clock, "16:00:00")
    assert join(client, token, display_name="Lina").status_code == 200

    at(clock, "17:59:59")
    assert join(client, token, display_name="Lina").status_code == 200

    at(clock, "18:00:00")
    over = join(client, token, display_name="Lina")
    assert over.status_code == 409
    assert error_of(over)["code"] == "meeting_ended"
    assert "livekit_token" not in over.text

    at(clock, "18:01:00")
    assert error_of(join(client, token, display_name="Lina"))["code"] == "meeting_ended"


def test_early_join_margin_opens_the_room_earlier(
    client: TestClient, clock: FixedClock, create: Create
) -> None:
    token = token_of(create(early_join_minutes=10)["participant_url"])
    at(clock, "15:49:59")
    refused = join(client, token, display_name="Lina")
    assert error_of(refused)["code"] == "not_open_yet"
    assert error_of(refused)["opens_at"] == "2027-01-02T15:50:00+01:00"
    at(clock, "15:50:00")
    assert join(client, token, display_name="Lina").status_code == 200


def test_closing_early_refuses_new_joins(
    client: TestClient, auth: dict[str, str], clock: FixedClock, create: Create
) -> None:
    meeting = create(early_join_minutes=0)
    token = token_of(meeting["participant_url"])
    at(clock, "16:30:00")
    assert join(client, token, display_name="Lina").status_code == 200
    assert client.post(f"/v1/meetings/{meeting['id']}/end", headers=auth).status_code == 200
    again = join(client, token, display_name="Lina")
    assert again.status_code == 409
    assert error_of(again)["code"] == "meeting_ended"


def test_cancelled_meeting_refuses_joins(
    client: TestClient, auth: dict[str, str], clock: FixedClock, create: Create
) -> None:
    meeting = create(early_join_minutes=0)
    token = token_of(meeting["participant_url"])
    assert client.delete(f"/v1/meetings/{meeting['id']}", headers=auth).status_code == 200
    at(clock, "16:30:00")
    refused = join(client, token, display_name="Lina")
    assert refused.status_code == 409
    assert error_of(refused)["code"] == "meeting_cancelled"


# --- scénario 2 : une URL inventée ou modifiée n'ouvre rien ------------------------------------


def test_invalid_links_open_nothing_and_no_livekit_token_is_issued(
    client: TestClient,
    clock: FixedClock,
    create: Create,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = create(early_join_minutes=0)
    token = token_of(created["participant_url"])
    at(clock, "16:30:00")

    def must_not_be_called(**_: Any) -> str:
        raise AssertionError("aucun jeton LiveKit ne doit être émis pour un lien invalide")

    monkeypatch.setattr("visio.services.join.issue_livekit_token", must_not_be_called)
    flipped = "A" if token[-1] != "A" else "B"
    bad_tokens = ("abc", "a.b.c", "lnk_" + "0" * 32, token[:-1] + flipped, token + "x", token[1:])
    for bad in bad_tokens:
        response = join(client, bad, display_name="Lina")
        assert response.status_code == 404, bad
        assert error_of(response) == {"code": "invalid_link", "message": "lien invalide"}
        assert "livekit" not in response.text


def test_empty_and_huge_tokens_are_invalid_links(client: TestClient) -> None:
    for bad in ("", "x" * 100_000):
        response = join(client, bad)
        assert response.status_code == 404
        assert error_of(response)["code"] == "invalid_link"


def test_revoked_link_is_refused_but_others_still_join(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    create: Create,
    db: Session,
) -> None:
    meeting = create(early_join_minutes=0)
    lina = named_link(client, auth, meeting["id"], role="participant", display_name="Lina")
    omar = named_link(client, auth, meeting["id"], role="participant", display_name="Omar")
    at(clock, "16:30:00")
    revoke_link(db, lina["id"], now=clock.now())
    refused = join(client, token_of(lina["url"]))
    assert refused.status_code == 403
    assert error_of(refused)["code"] == "participant_excluded"
    assert "livekit_token" not in refused.text
    assert join(client, token_of(omar["url"])).status_code == 200


def test_revoked_link_stays_refused_after_the_meeting_is_over_too(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    create: Create,
    db: Session,
) -> None:
    meeting = create(early_join_minutes=0)
    lina = named_link(client, auth, meeting["id"], role="participant", display_name="Lina")
    revoke_link(db, lina["id"], now=parse_iso8601(f"{DAY}T16:10:00+01:00"))
    at(clock, "18:30:00")
    assert error_of(join(client, token_of(lina["url"])))["code"] == "participant_excluded"


# --- jeton LiveKit : droits, expiration, salle ---------------------------------------------------


def test_participant_token_has_participant_rights_only(
    client: TestClient, clock: FixedClock, create: Create, settings: Settings
) -> None:
    token = token_of(create(early_join_minutes=0)["participant_url"])
    at(clock, "16:30:00")
    response = join(client, token, display_name="Lina Benali")
    assert response.status_code == 200, response.text
    claims = decode_livekit(settings, response.json()["livekit_token"])
    assert claims["iss"] == settings.livekit_api_key
    assert claims["name"] == "Lina Benali"
    grant = claims["video"]
    assert grant["roomJoin"] is True
    assert grant["canPublish"] is True
    assert grant["canSubscribe"] is True
    assert "roomAdmin" not in grant
    assert "roomCreate" not in grant
    assert json.loads(claims["metadata"]) == {"role": "participant", "external_id": None}


def test_organizer_token_is_not_room_admin(
    client: TestClient, clock: FixedClock, create: Create, settings: Settings
) -> None:
    created = create(early_join_minutes=0)
    at(clock, "16:30:00")
    response = join(client, token_of(created["organizer_urls"][0]))
    assert response.status_code == 200, response.text
    body = response.json()
    claims = decode_livekit(settings, body["livekit_token"])
    assert "roomAdmin" not in claims["video"]  # la modération passe par /v1/room/*
    assert claims["name"] == "Marie Durand"
    assert json.loads(claims["metadata"]) == {"role": "organizer", "external_id": "staff_42"}
    assert body["participant"] == {
        "display_name": "Marie Durand",
        "external_id": "staff_42",
        "role": "organizer",
    }


def test_livekit_token_is_short_lived(
    client: TestClient, clock: FixedClock, create: Create, settings: Settings
) -> None:
    token = token_of(create(early_join_minutes=0)["participant_url"])
    at(clock, "16:30:00")
    body = join(client, token, display_name="Lina").json()
    claims = decode_livekit(settings, body["livekit_token"])
    assert claims["exp"] == int(clock.now().timestamp()) + 600
    assert claims["nbf"] == int(clock.now().timestamp())
    assert parse_iso8601(body["expires_at"]) == parse_iso8601(f"{DAY}T16:40:00+01:00")


def test_livekit_token_never_outlives_the_meeting(
    client: TestClient, clock: FixedClock, create: Create, settings: Settings
) -> None:
    token = token_of(create(early_join_minutes=0)["participant_url"])
    at(clock, "17:55:00")
    body = join(client, token, display_name="Lina").json()
    claims = decode_livekit(settings, body["livekit_token"])
    assert claims["exp"] == int(parse_iso8601(SLOT_END).timestamp())
    assert body["seconds_until_end"] == 300


def test_everyone_of_a_meeting_gets_the_same_opaque_room_and_other_meetings_do_not(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    create: Create,
    settings: Settings,
) -> None:
    first = create(early_join_minutes=0)
    second = create(early_join_minutes=0)
    at(clock, "16:30:00")

    def room_of(token: str) -> str:
        body = join(client, token, display_name="X").json()
        room: str = decode_livekit(settings, body["livekit_token"])["video"]["room"]
        return room

    participant_room = room_of(token_of(first["participant_url"]))
    organizer_room = room_of(token_of(first["organizer_urls"][0]))
    other_room = room_of(token_of(second["participant_url"]))
    assert participant_room == organizer_room
    assert participant_room != other_room
    assert first["id"] not in participant_room
    assert first["id"].removeprefix("mtg_") not in participant_room


def test_response_does_not_reveal_the_meeting_id_or_the_link_id(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    create: Create,
    settings: Settings,
) -> None:
    created = create(early_join_minutes=0)
    lina = named_link(client, auth, created["id"], role="participant", display_name="Lina")
    at(clock, "16:30:00")
    response = join(client, token_of(lina["url"]))
    assert response.status_code == 200
    claims = decode_livekit(settings, response.json()["livekit_token"])
    assert created["id"] not in response.text
    assert lina["id"] not in response.text
    assert lina["id"] not in claims["sub"]


def test_response_describes_the_meeting_for_the_interface(
    client: TestClient, clock: FixedClock, create: Create
) -> None:
    token = token_of(create(early_join_minutes=0)["participant_url"])
    at(clock, "16:30:00")
    body = join(client, token, display_name="Lina").json()
    assert body["livekit_url"] == "ws://localhost:7880"
    assert body["meeting"] == {
        "title": "Cours BTS MCO – Groupe A",
        "starts_at": "2027-01-02T16:00:00+01:00",
        "ends_at": "2027-01-02T18:00:00+01:00",
        "waiting_room": False,
        "recording": True,  # l'interface affiche l'indicateur d'enregistrement
        "transcription": True,
    }
    assert body["seconds_until_end"] == 90 * 60
    assert "metadata" not in body["meeting"]


# --- nom et identité -----------------------------------------------------------------------------


def test_named_link_keeps_its_name_and_a_stable_identity(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    create: Create,
    settings: Settings,
) -> None:
    meeting = create(early_join_minutes=0)
    lina = named_link(
        client, auth, meeting["id"], role="participant", display_name="Lina", external_id="stu_981"
    )
    omar = named_link(client, auth, meeting["id"], role="participant", display_name="Omar")
    at(clock, "16:30:00")

    first = join(client, token_of(lina["url"]), display_name="Un autre nom").json()
    again = join(client, token_of(lina["url"])).json()
    other = join(client, token_of(omar["url"])).json()

    assert first["participant"]["display_name"] == "Lina"  # le nom du lien ne se modifie pas
    assert first["client_id"] is None
    claims = decode_livekit(settings, first["livekit_token"])
    assert claims["name"] == "Lina"
    assert json.loads(claims["metadata"])["external_id"] == "stu_981"
    identity = claims["sub"]
    assert decode_livekit(settings, again["livekit_token"])["sub"] == identity
    assert decode_livekit(settings, other["livekit_token"])["sub"] != identity


def test_generic_link_requires_a_name(
    client: TestClient, clock: FixedClock, create: Create
) -> None:
    token = token_of(create(early_join_minutes=0)["participant_url"])
    at(clock, "16:30:00")
    missing = join(client, token)
    assert missing.status_code == 422
    assert error_of(missing)["code"] == "display_name_required"
    assert "livekit_token" not in missing.text
    blank = join(client, token, display_name="   ")
    assert blank.status_code == 422
    assert error_of(blank)["code"] == "validation_error"


def test_name_is_checked_after_the_slot(
    client: TestClient, clock: FixedClock, create: Create
) -> None:
    """Avant l'ouverture, la page d'attente n'a pas besoin du nom."""
    token = token_of(create(early_join_minutes=0)["participant_url"])
    at(clock, "15:00:00")
    assert error_of(join(client, token))["code"] == "not_open_yet"


def test_generic_link_gives_each_browser_its_own_identity(
    client: TestClient, clock: FixedClock, create: Create, settings: Settings
) -> None:
    token = token_of(create(early_join_minutes=0)["participant_url"])
    at(clock, "16:30:00")

    first = join(client, token, display_name="Lina").json()
    second = join(client, token, display_name="Omar").json()
    assert first["client_id"] != second["client_id"]  # chaque navigateur reçoit le sien
    assert len(first["client_id"]) == 32

    returning = join(client, token, display_name="Lina", client_id=first["client_id"]).json()
    assert returning["client_id"] == first["client_id"]

    def identity(body: dict[str, Any]) -> str:
        sub: str = decode_livekit(settings, body["livekit_token"])["sub"]
        return sub

    assert identity(returning) == identity(first)
    assert identity(first) != identity(second)
    assert first["client_id"] not in identity(first)


def test_generic_link_name_is_the_one_typed(
    client: TestClient, clock: FixedClock, create: Create, settings: Settings
) -> None:
    token = token_of(create(early_join_minutes=0)["participant_url"])
    at(clock, "16:30:00")
    body = join(client, token, display_name="  Zoé  ").json()
    assert body["participant"] == {
        "display_name": "Zoé",
        "external_id": None,
        "role": "participant",
    }
    assert decode_livekit(settings, body["livekit_token"])["name"] == "Zoé"


# --- formes d'entrée -----------------------------------------------------------------------------


def test_invalid_bodies_are_validation_errors(client: TestClient) -> None:
    for payload in (
        {},
        {"token": 123},
        {"token": "x", "unknown": 1},
        {"token": "x", "client_id": "trop-court"},
        {"token": "x", "client_id": "a" * 16 + " espace"},
        {"token": "x", "display_name": "n" * 201},
    ):
        response = client.post("/v1/join", json=payload)
        assert response.status_code == 422, payload
        assert error_of(response)["code"] == "validation_error"


def test_join_needs_no_api_key_but_creates_nothing(
    client: TestClient, auth: dict[str, str], clock: FixedClock, create: Create
) -> None:
    created = create(early_join_minutes=0)
    at(clock, "16:30:00")
    joined = join(client, token_of(created["participant_url"]), display_name="Lina")
    assert joined.status_code == 200
    listing = client.get(f"/v1/meetings/{created['id']}", headers=auth).json()
    assert listing["id"] == created["id"]
    # Le jeton d'un lien ne donne aucun accès à l'API de gestion.
    refused = client.get(
        f"/v1/meetings/{created['id']}",
        headers={"Authorization": f"Bearer {token_of(created['organizer_urls'][0])}"},
    )
    assert refused.status_code == 401


def test_join_route_is_in_the_openapi_spec_and_the_dev_page_is_not(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/v1/join"]["post"]
    assert {"200", "403", "404", "409", "422"} <= set(operation["responses"])
    assert not any(path.startswith("/dev") for path in spec["paths"])


# --- HTTPS en production, page d'essai réservée au développement -----------------------------


def test_production_refuses_plain_http_on_join(
    set_env: Callable[[dict[str, str]], None], tmp_path: Any
) -> None:
    db_url = f"sqlite:///{tmp_path / 'prod.db'}"
    set_env({**valid_env("production"), "DATABASE_URL": db_url})
    upgrade(db_url)
    app = create_app(settings=Settings())  # type: ignore[call-arg]
    with TestClient(app) as http_client:
        refused = http_client.post("/v1/join", json={"token": "x"})
        assert refused.status_code == 403
        assert error_of(refused)["code"] == "https_required"
        proxied = http_client.post(
            "/v1/join", json={"token": "x"}, headers={"X-Forwarded-Proto": "https"}
        )
        assert proxied.status_code == 404  # lien invalide : on est passé jusqu'à la vérification


def test_dev_page_exists_in_development_only(
    client: TestClient, set_env: Callable[[dict[str, str]], None], tmp_path: Any
) -> None:
    page = client.get("/dev/livekit-test")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "/v1/join" in page.text

    db_url = f"sqlite:///{tmp_path / 'prod.db'}"
    set_env({**valid_env("production"), "DATABASE_URL": db_url})
    upgrade(db_url)
    production = create_app(settings=Settings())  # type: ignore[call-arg]
    with TestClient(production) as prod_client:
        hidden = prod_client.get("/dev/livekit-test", headers={"X-Forwarded-Proto": "https"})
        assert hidden.status_code == 404
        assert error_of(hidden)["code"] == "not_found"
