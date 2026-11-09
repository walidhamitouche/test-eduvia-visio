"""Jetons LiveKit (étape 6) : droits par rôle, expiration, noms opaques. Logique pure."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from visio.domain.roles import Role
from visio.services.livekit_tokens import (
    LIVEKIT_TOKEN_TTL,
    client_ws_url,
    generic_identity,
    issue_livekit_token,
    livekit_expiry,
    named_identity,
    room_name,
    video_grant,
)

SECRET = "test-livekit-secret-" + "d" * 40
LINK_SECRET = "test-link-secret-" + "b" * 40
NOW = datetime(2027, 1, 2, 15, 0, tzinfo=UTC)
END = datetime(2027, 1, 2, 17, 0, tzinfo=UTC)


def decode(token: str) -> dict[str, Any]:
    # L'heure simulée est en 2027 : on ne vérifie pas les dates ici (voir les tests d'expiration).
    claims: dict[str, Any] = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        options={"verify_exp": False, "verify_nbf": False, "verify_iat": False},
    )
    return claims


def make_token(role: Role = Role.PARTICIPANT, **overrides: Any) -> str:
    values: dict[str, Any] = {
        "api_key": "devkey",
        "api_secret": SECRET,
        "room": "rm_test",
        "identity": "pn_test",
        "name": "Lina Benali",
        "role": role,
        "external_id": "stu_981",
        "issued_at": NOW,
        "expires_at": NOW + LIVEKIT_TOKEN_TTL,
    }
    values.update(overrides)
    return issue_livekit_token(**values)


# --- droits -----------------------------------------------------------------------------------


def test_participant_can_publish_and_subscribe_but_is_not_admin() -> None:
    grant = decode(make_token(Role.PARTICIPANT))["video"]
    assert grant["roomJoin"] is True
    assert grant["room"] == "rm_test"
    assert grant["canPublish"] is True
    assert grant["canSubscribe"] is True
    assert "roomAdmin" not in grant
    assert "roomCreate" not in grant


@pytest.mark.parametrize("role", list(Role))
def test_nobody_can_publish_data_so_nobody_can_forge_a_chat_message(role: Role) -> None:
    """Le chat passe par le backend (`/v1/room/chat`) : il connaît l'auteur réel."""
    assert decode(make_token(role))["video"]["canPublishData"] is False


def test_organizer_is_not_room_admin_either() -> None:
    """Étape 8 : la modération passe par le backend, qui vérifie le rôle en base et tient la liste
    d'exclusion. Un `roomAdmin` permettrait à un navigateur d'exclure sans trace, ou d'évincer un
    autre organisateur, en parlant directement au serveur de médias."""
    grant = decode(make_token(Role.ORGANIZER))["video"]
    assert grant["roomJoin"] is True
    for forbidden in ("roomAdmin", "roomCreate", "roomList", "roomRecord"):
        assert forbidden not in grant


def test_both_roles_get_exactly_the_same_grant() -> None:
    assert video_grant(Role.ORGANIZER, "rm_x") == video_grant(Role.PARTICIPANT, "rm_x")


def test_both_roles_can_share_their_screen() -> None:
    for role in Role:
        sources = video_grant(role, "rm_x")["canPublishSources"]
        assert {"microphone", "camera", "screen_share"} <= set(sources)


def test_token_is_limited_to_one_room() -> None:
    grant = decode(make_token(room="rm_one"))["video"]
    assert grant["room"] == "rm_one"
    assert "roomList" not in grant
    assert "roomRecord" not in grant


# --- contenu et signature ----------------------------------------------------------------------


def test_token_carries_identity_name_and_metadata() -> None:
    claims = decode(make_token(name="Zoé Élodie", external_id="stu_981"))
    assert claims["iss"] == "devkey"
    assert claims["sub"] == "pn_test"
    assert claims["name"] == "Zoé Élodie"
    assert json.loads(claims["metadata"]) == {"role": "participant", "external_id": "stu_981"}


def test_metadata_has_a_null_external_id_without_one() -> None:
    claims = decode(make_token(external_id=None))
    assert json.loads(claims["metadata"]) == {"role": "participant", "external_id": None}


def test_token_is_signed_with_the_livekit_secret_only() -> None:
    token = make_token()
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, "un-autre-secret-" + "q" * 40, algorithms=["HS256"])


# --- expiration -------------------------------------------------------------------------------


def test_expiry_is_short_when_the_meeting_is_far_from_over() -> None:
    assert livekit_expiry(NOW, END) == NOW + LIVEKIT_TOKEN_TTL
    assert timedelta(minutes=15) >= LIVEKIT_TOKEN_TTL


def test_expiry_never_goes_past_the_end_of_the_meeting() -> None:
    near_end = END - timedelta(minutes=3)
    assert livekit_expiry(near_end, END) == END


def test_token_exp_matches_the_requested_expiry() -> None:
    expiry = NOW + timedelta(minutes=7)
    claims = decode(make_token(expires_at=expiry))
    assert claims["exp"] == int(expiry.timestamp())
    assert claims["nbf"] == claims["iat"] == int(NOW.timestamp())


def test_a_token_past_its_expiry_is_refused_by_a_standard_verifier() -> None:
    past = datetime(2020, 1, 1, 9, 0, tzinfo=UTC)
    token = make_token(issued_at=past, expires_at=past + LIVEKIT_TOKEN_TTL)
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(token, SECRET, algorithms=["HS256"])


# --- noms opaques -----------------------------------------------------------------------------


def test_room_name_is_stable_opaque_and_per_meeting() -> None:
    meeting_id = "mtg_0576f9bef54033e7ea377447aa397a8b"
    room = room_name(LINK_SECRET, meeting_id)
    assert room == room_name(LINK_SECRET, meeting_id)
    assert room.startswith("rm_")
    assert len(room) == 3 + 32
    assert meeting_id not in room
    assert meeting_id.removeprefix("mtg_") not in room
    assert room != room_name(LINK_SECRET, "mtg_autre")
    assert room != room_name("un-autre-secret-" + "z" * 40, meeting_id)


def test_named_identity_is_stable_and_hides_the_link_id() -> None:
    link_id = "lnk_" + "a" * 32
    identity = named_identity(LINK_SECRET, link_id)
    assert identity == named_identity(LINK_SECRET, link_id)
    assert link_id not in identity
    assert "a" * 32 not in identity
    assert identity != named_identity(LINK_SECRET, "lnk_" + "b" * 32)


def test_generic_identity_depends_on_the_browser_and_hides_the_client_id() -> None:
    link_id = "lnk_" + "a" * 32
    mine = "client-" + "1" * 16
    theirs = "client-" + "2" * 16
    identity = generic_identity(LINK_SECRET, link_id, mine)
    assert identity == generic_identity(LINK_SECRET, link_id, mine)
    assert identity != generic_identity(LINK_SECRET, link_id, theirs)
    assert mine not in identity
    assert identity != named_identity(LINK_SECRET, link_id)


def test_room_and_identities_do_not_collide_across_uses() -> None:
    same = "x" * 20
    assert room_name(LINK_SECRET, same).removeprefix("rm_") != named_identity(
        LINK_SECRET, same
    ).removeprefix("pn_")


# --- URL pour le navigateur ---------------------------------------------------------------------


def test_client_url_uses_websocket_schemes() -> None:
    assert client_ws_url("http://localhost:7880") == "ws://localhost:7880"
    assert client_ws_url("https://visio.eduvia.app") == "wss://visio.eduvia.app"
    assert client_ws_url("ws://localhost:7880") == "ws://localhost:7880"
    assert client_ws_url("wss://visio.eduvia.app") == "wss://visio.eduvia.app"
