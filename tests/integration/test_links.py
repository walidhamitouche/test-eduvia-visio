"""Liens nominatifs et vérification des jetons (étape 5), de bout en bout avec la base."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.integration.conftest import SLOT_END, SLOT_START
from visio.config import Settings
from visio.db.models import LinkRow, MeetingRow
from visio.domain.clock import FixedClock
from visio.domain.errors import InvalidLinkError, LinkRevokedError
from visio.domain.roles import Role
from visio.domain.slot import parse_iso8601
from visio.services.links import decode_token, resolve_link, revoke_link

Create = Callable[..., dict[str, Any]]


def token_of(url: str) -> str:
    assert "/j/" in url
    return url.rsplit("/j/", 1)[1]


def post_link(client: TestClient, auth: dict[str, str], meeting_id: str, **body: Any) -> Any:
    return client.post(f"/v1/meetings/{meeting_id}/links", json=body, headers=auth)


# --- création -------------------------------------------------------------------------------------


def test_named_participant_link_is_created(
    client: TestClient, auth: dict[str, str], create: Create, db: Session, settings: Settings
) -> None:
    meeting = create()
    response = post_link(
        client,
        auth,
        meeting["id"],
        role="participant",
        display_name="Lina Benali",
        external_id="stu_981",
    )
    assert response.status_code == 201, response.text
    link = response.json()
    assert link["role"] == "participant"
    assert link["display_name"] == "Lina Benali"
    assert link["external_id"] == "stu_981"
    assert link["url"].startswith("http://visio.test/j/")
    assert link["url"] not in (meeting["participant_url"], *meeting["organizer_urls"])

    resolved = resolve_link(db, settings, token_of(link["url"]))
    assert resolved.meeting.id == meeting["id"]
    assert resolved.link.display_name == "Lina Benali"
    assert resolved.link.external_id == "stu_981"
    assert resolved.link.role is Role.PARTICIPANT


def test_token_contains_name_and_external_id(
    client: TestClient, auth: dict[str, str], create: Create, settings: Settings
) -> None:
    meeting = create()
    link = post_link(
        client, auth, meeting["id"], role="participant", display_name="Lina", external_id="stu_1"
    ).json()
    claims = decode_token(settings, token_of(link["url"]))
    assert claims["name"] == "Lina"
    assert claims["eid"] == "stu_1"
    assert claims["role"] == "participant"
    assert claims["mid"] == meeting["id"]
    assert claims["jti"] == link["id"]


def test_external_id_is_optional(
    client: TestClient, auth: dict[str, str], create: Create, db: Session, settings: Settings
) -> None:
    meeting = create()
    link = post_link(client, auth, meeting["id"], role="participant", display_name="Lina").json()
    assert link["external_id"] is None
    assert resolve_link(db, settings, token_of(link["url"])).link.external_id is None


def test_extra_organizer_link_is_created(
    client: TestClient, auth: dict[str, str], create: Create, db: Session, settings: Settings
) -> None:
    meeting = create()
    link = post_link(
        client,
        auth,
        meeting["id"],
        role="organizer",
        display_name="Paul Martin",
        external_id="staff_7",
    ).json()
    resolved = resolve_link(db, settings, token_of(link["url"]))
    assert resolved.link.role is Role.ORGANIZER
    assert resolved.link.display_name == "Paul Martin"


def test_two_organizers_get_two_distinct_working_links(
    create: Create, db: Session, settings: Settings
) -> None:
    created = create(
        organizers=[
            {"display_name": "Marie Durand", "external_id": "staff_42"},
            {"display_name": "Paul Martin"},
        ]
    )
    first, second = (resolve_link(db, settings, token_of(u)) for u in created["organizer_urls"])
    assert first.link.id != second.link.id
    assert (first.link.display_name, second.link.display_name) == ("Marie Durand", "Paul Martin")
    assert first.link.role is second.link.role is Role.ORGANIZER
    assert first.meeting.id == second.meeting.id == created["id"]


def test_links_response_is_in_paris_time(
    client: TestClient, auth: dict[str, str], create: Create
) -> None:
    meeting = create()
    link = post_link(client, auth, meeting["id"], role="participant", display_name="Lina").json()
    assert link["created_at"].endswith(("+01:00", "+02:00"))


# --- validation et erreurs ----------------------------------------------------------------------


def test_links_route_requires_the_api_key(client: TestClient, create: Create) -> None:
    meeting = create()
    body = {"role": "participant", "display_name": "Lina"}
    for headers in ({}, {"Authorization": "Bearer mauvaise-cle"}):
        response = client.post(f"/v1/meetings/{meeting['id']}/links", json=body, headers=headers)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


def test_unauthenticated_call_creates_no_link(
    client: TestClient, create: Create, db: Session
) -> None:
    meeting = create()
    before = db.query(LinkRow).count()
    client.post(
        f"/v1/meetings/{meeting['id']}/links", json={"role": "organizer", "display_name": "X"}
    )
    assert db.query(LinkRow).count() == before


def test_unknown_meeting_is_404(client: TestClient, auth: dict[str, str]) -> None:
    response = post_link(client, auth, "mtg_inconnue", role="participant", display_name="Lina")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "meeting_not_found"


@pytest.mark.parametrize(
    "body",
    [
        {"role": "participant"},  # nom obligatoire
        {"display_name": "Lina"},  # rôle obligatoire
        {"role": "admin", "display_name": "Lina"},  # rôle inconnu
        {"role": "participant", "display_name": ""},
        {"role": "participant", "display_name": "   "},
        {"role": "participant", "display_name": "x" * 201},
        {"role": "participant", "display_name": "Lina", "external_id": 42},  # type strict
        {"role": "participant", "display_name": "Lina", "extrnal_id": "x"},  # champ inconnu
        {"role": "participant", "display_name": None},
    ],
)
def test_invalid_bodies_are_rejected_without_creating_a_link(
    client: TestClient, auth: dict[str, str], create: Create, db: Session, body: dict[str, Any]
) -> None:
    meeting = create()
    before = db.query(LinkRow).count()
    response = post_link(client, auth, meeting["id"], **body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert db.query(LinkRow).count() == before


def test_cancelled_meeting_gets_no_link(
    client: TestClient, auth: dict[str, str], create: Create
) -> None:
    meeting = create()
    assert client.delete(f"/v1/meetings/{meeting['id']}", headers=auth).status_code == 200
    response = post_link(client, auth, meeting["id"], role="participant", display_name="Lina")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "meeting_cancelled"


def test_ended_meeting_gets_no_link(
    client: TestClient, auth: dict[str, str], create: Create, clock: FixedClock
) -> None:
    meeting = create()
    clock.set(parse_iso8601(SLOT_END))  # 18:00:00 pile : c'est fini
    response = post_link(client, auth, meeting["id"], role="participant", display_name="Lina")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "meeting_ended"


def test_link_can_be_created_during_the_meeting(
    client: TestClient, auth: dict[str, str], create: Create, clock: FixedClock
) -> None:
    meeting = create()
    clock.set(parse_iso8601("2027-01-02T17:59:59+01:00"))
    response = post_link(client, auth, meeting["id"], role="participant", display_name="Lina")
    assert response.status_code == 201


def test_at_most_ten_active_organizer_links(
    client: TestClient, auth: dict[str, str], create: Create, db: Session
) -> None:
    meeting = create()  # 1 organisateur à la création
    for i in range(9):
        response = post_link(client, auth, meeting["id"], role="organizer", display_name=f"O{i}")
        assert response.status_code == 201
    response = post_link(client, auth, meeting["id"], role="organizer", display_name="De trop")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "too_many_organizers"
    # Les liens participants ne sont pas limités par cette règle.
    assert (
        post_link(client, auth, meeting["id"], role="participant", display_name="Lina").status_code
        == 201
    )
    # Un lien organisateur révoqué libère une place.
    first = db.query(LinkRow).filter_by(meeting_id=meeting["id"], role=Role.ORGANIZER).first()
    assert first is not None
    revoke_link(db, first.id, now=parse_iso8601(SLOT_START))
    assert (
        post_link(
            client, auth, meeting["id"], role="organizer", display_name="Remplaçant"
        ).status_code
        == 201
    )


def test_get_meeting_never_returns_a_link(
    client: TestClient, auth: dict[str, str], create: Create
) -> None:
    meeting = create()
    link = post_link(client, auth, meeting["id"], role="participant", display_name="Lina").json()
    fetched = client.get(f"/v1/meetings/{meeting['id']}", headers=auth).text
    assert link["url"] not in fetched
    assert token_of(link["url"]) not in fetched


def test_links_route_is_in_the_openapi_spec(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/v1/meetings/{meeting_id}/links"]["post"]
    assert operation["tags"] == ["liens"]
    assert {"201", "401", "404", "409", "422"} <= set(operation["responses"])


# --- scénario 2 (partiel) : URL inventée ou modifiée n'ouvre rien --------------------------------


def test_invented_urls_open_nothing(create: Create, db: Session, settings: Settings) -> None:
    create()
    for token in ("abc", "a.b.c", "lnk_" + "0" * 32, "mtg_" + "0" * 32, ""):
        with pytest.raises(InvalidLinkError):
            resolve_link(db, settings, token)


def test_modified_url_opens_nothing(create: Create, db: Session, settings: Settings) -> None:
    created = create()
    token = token_of(created["participant_url"])
    resolve_link(db, settings, token)  # l'original fonctionne
    for tampered in (token[:-1] + ("A" if token[-1] != "A" else "B"), token + "x", token[1:]):
        with pytest.raises(InvalidLinkError):
            resolve_link(db, settings, tampered)


def test_every_refusal_gives_the_same_message(
    create: Create, db: Session, settings: Settings
) -> None:
    created = create()
    token = token_of(created["participant_url"])
    messages = set()
    for bad in ("garbage", token + "x", "a.b.c"):
        with pytest.raises(InvalidLinkError) as error:
            resolve_link(db, settings, bad)
        messages.add(error.value.message)
    assert messages == {"lien invalide"}


def test_validly_signed_token_for_an_unknown_link_is_refused(
    create: Create, db: Session, settings: Settings
) -> None:
    """Même avec le bon secret, un jeton dont le lien n'est pas en base n'ouvre rien."""
    created = create()
    secret = settings.link_signing_secret.get_secret_value()
    claims = {"jti": "lnk_" + "f" * 32, "mid": created["id"], "role": "organizer"}
    with pytest.raises(InvalidLinkError):
        resolve_link(db, settings, jwt.encode(claims, secret, algorithm="HS256"))


def test_token_of_meeting_a_is_refused_on_meeting_b(
    create: Create, db: Session, settings: Settings
) -> None:
    a = create(title="Réunion A")
    b = create(title="Réunion B")
    token_a = token_of(a["participant_url"])
    assert resolve_link(db, settings, token_a).meeting.id == a["id"]

    # Jeton signé correctement, lien de A, mais annonçant la réunion B.
    secret = settings.link_signing_secret.get_secret_value()
    claims = decode_token(settings, token_a)
    claims["mid"] = b["id"]
    with pytest.raises(InvalidLinkError):
        resolve_link(db, settings, jwt.encode(claims, secret, algorithm="HS256"))


def test_signed_token_with_a_different_role_than_the_database_is_refused(
    create: Create, db: Session, settings: Settings
) -> None:
    """La base fait foi : un jeton qui prétend organisateur sur un lien participant est refusé,
    même signé avec le bon secret."""
    created = create()
    secret = settings.link_signing_secret.get_secret_value()
    claims = decode_token(settings, token_of(created["participant_url"]))
    claims["role"] = "organizer"
    with pytest.raises(InvalidLinkError):
        resolve_link(db, settings, jwt.encode(claims, secret, algorithm="HS256"))


def test_signed_token_with_a_different_name_than_the_database_is_refused(
    client: TestClient, auth: dict[str, str], create: Create, db: Session, settings: Settings
) -> None:
    meeting = create()
    link = post_link(client, auth, meeting["id"], role="participant", display_name="Lina").json()
    secret = settings.link_signing_secret.get_secret_value()
    claims = decode_token(settings, token_of(link["url"]))
    claims["name"] = "Marie Durand"
    with pytest.raises(InvalidLinkError):
        resolve_link(db, settings, jwt.encode(claims, secret, algorithm="HS256"))


def test_token_changing_the_secret_invalidates_all_links(
    create: Create, db: Session, settings: Settings, set_env: Callable[[dict[str, str]], None]
) -> None:
    created = create()
    token = token_of(created["participant_url"])
    set_env({"LINK_SIGNING_SECRET": "nouveau-secret-" + "n" * 40})
    rotated = Settings()  # type: ignore[call-arg]
    with pytest.raises(InvalidLinkError):
        resolve_link(db, rotated, token)


# --- rôle vient du lien, révocation --------------------------------------------------------------


def test_role_comes_from_the_link(create: Create, db: Session, settings: Settings) -> None:
    created = create()
    participant = resolve_link(db, settings, token_of(created["participant_url"]))
    organizer = resolve_link(db, settings, token_of(created["organizer_urls"][0]))
    assert participant.link.role is Role.PARTICIPANT
    assert organizer.link.role is Role.ORGANIZER


def test_links_still_resolve_after_the_meeting_is_over(
    create: Create, db: Session, settings: Settings, clock: FixedClock
) -> None:
    """Le créneau se décide à l'étape 6 : le lien lui-même reste reconnu (« réunion terminée »)."""
    created = create()
    clock.set(parse_iso8601("2027-02-01T10:00:00+01:00"))
    resolved = resolve_link(db, settings, token_of(created["participant_url"]))
    assert resolved.meeting.id == created["id"]


def test_link_survives_a_slot_change(
    client: TestClient, auth: dict[str, str], create: Create, db: Session, settings: Settings
) -> None:
    """Pas d'expiration dans le jeton : déplacer le créneau ne casse pas les liens déjà émis."""
    created = create()
    token = token_of(created["participant_url"])
    response = client.patch(
        f"/v1/meetings/{created['id']}",
        json={"starts_at": "2027-03-01T09:00:00+01:00", "ends_at": "2027-03-01T10:00:00+01:00"},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    resolved = resolve_link(db, settings, token)
    db.refresh(resolved.meeting)
    assert resolved.meeting.starts_at.month == 3


def test_revoked_link_is_refused_but_others_still_work(
    client: TestClient, auth: dict[str, str], create: Create, db: Session, settings: Settings
) -> None:
    meeting = create()
    lina = post_link(client, auth, meeting["id"], role="participant", display_name="Lina").json()
    omar = post_link(client, auth, meeting["id"], role="participant", display_name="Omar").json()
    revoke_link(db, lina["id"], now=parse_iso8601(SLOT_START))

    with pytest.raises(LinkRevokedError):
        resolve_link(db, settings, token_of(lina["url"]))
    assert resolve_link(db, settings, token_of(omar["url"])).link.display_name == "Omar"
    assert resolve_link(db, settings, token_of(meeting["participant_url"])).link.role is (
        Role.PARTICIPANT
    )


def test_revoking_is_replayable_and_keeps_the_first_date(
    client: TestClient, auth: dict[str, str], create: Create, db: Session
) -> None:
    meeting = create()
    lina = post_link(client, auth, meeting["id"], role="participant", display_name="Lina").json()
    first = revoke_link(db, lina["id"], now=parse_iso8601("2027-01-02T16:30:00+01:00"))
    again = revoke_link(db, lina["id"], now=parse_iso8601("2027-01-02T17:00:00+01:00"))
    assert first.revoked_at == again.revoked_at == parse_iso8601("2027-01-02T16:30:00+01:00")


def test_revoking_an_unknown_link_fails(db: Session) -> None:
    with pytest.raises(InvalidLinkError):
        revoke_link(db, "lnk_inconnu", now=parse_iso8601(SLOT_START))


def test_cancelled_meeting_keeps_its_links_resolvable(
    client: TestClient, auth: dict[str, str], create: Create, db: Session, settings: Settings
) -> None:
    created = create()
    client.delete(f"/v1/meetings/{created['id']}", headers=auth)
    resolved = resolve_link(db, settings, token_of(created["participant_url"]))
    assert isinstance(resolved.meeting, MeetingRow)  # c'est l'étape 6 qui répondra « annulée »
