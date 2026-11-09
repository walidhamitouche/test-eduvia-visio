"""Jetons de lien (étape 5) : signature, falsification, entropie. Logique pure, sans base."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import jwt
import pytest

from tests.conftest import valid_env
from visio.config import Settings
from visio.db.models import LinkRow, new_id
from visio.domain.errors import InvalidLinkError
from visio.domain.roles import Role
from visio.services.links import (
    MAX_TOKEN_LENGTH,
    decode_token,
    issue_link_url,
    issue_token,
)

CREATED = datetime(2027, 1, 1, 9, 0, tzinfo=UTC)


def make_settings(set_env, **extra: str) -> Settings:  # type: ignore[no-untyped-def]
    set_env({**valid_env(), **extra})
    return Settings()  # type: ignore[call-arg]


def make_link(**overrides: object) -> LinkRow:
    values: dict[str, object] = {
        "id": new_id("lnk"),
        "meeting_id": new_id("mtg"),
        "role": Role.PARTICIPANT,
        "created_at": CREATED,
    }
    values.update(overrides)
    return LinkRow(**values)


def b64(data: dict[str, object]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_payload(token: str) -> dict[str, object]:
    segment = token.split(".")[1]
    payload: dict[str, object] = json.loads(
        base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    )
    return payload


# --- URL ----------------------------------------------------------------------------------------


def test_url_uses_the_public_domain_and_only_contains_the_token(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env, PUBLIC_BASE_URL="https://visio.eduvia.app/")
    link = make_link()
    url = issue_link_url(settings, link)
    assert url == f"https://visio.eduvia.app/j/{issue_token(settings, link)}"
    assert link.id not in url.split("/j/")[0]
    assert "?" not in url
    assert "#" not in url


def test_token_is_deterministic_and_depends_on_secret_and_link(set_env) -> None:  # type: ignore[no-untyped-def]
    one = make_settings(set_env)
    a, b = make_link(), make_link()
    assert issue_token(one, a) == issue_token(one, a)
    assert issue_token(one, a) != issue_token(one, b)
    other = make_settings(set_env, LINK_SIGNING_SECRET="autre-secret-" + "z" * 40)
    assert issue_token(one, a) != issue_token(other, a)


def test_token_carries_the_claims_of_the_link(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    link = make_link(role=Role.ORGANIZER, display_name="Marie Durand", external_id="staff_42")
    claims = decode_token(settings, issue_token(settings, link))
    assert claims["jti"] == link.id
    assert claims["mid"] == link.meeting_id
    assert claims["role"] == "organizer"
    assert claims["name"] == "Marie Durand"
    assert claims["eid"] == "staff_42"
    assert "exp" not in claims  # le créneau se décide côté serveur (DECISIONS.md point 22)


def test_generic_link_has_no_name_claims(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    claims = decode_token(settings, issue_token(settings, make_link()))
    assert "name" not in claims
    assert "eid" not in claims


def test_token_stays_short_with_the_longest_names(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    for name in ("é" * 200, "😀" * 200, "x" * 200):
        link = make_link(display_name=name, external_id=name)
        token = issue_token(settings, link)
        assert len(token) < MAX_TOKEN_LENGTH
        assert name != "é" * 200 or len(token) < 1500  # accents : l'URL reste courte
        assert decode_token(settings, token)["name"] == name


# --- falsification -------------------------------------------------------------------------------


def test_valid_token_is_accepted(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    link = make_link()
    assert decode_token(settings, issue_token(settings, link))["jti"] == link.id


def test_changing_any_single_character_is_rejected(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    token = issue_token(settings, make_link(display_name="Lina Benali", external_id="stu_1"))
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    for position, char in enumerate(token):
        if char == ".":
            continue
        replacement = next(c for c in alphabet if c != char)
        tampered = token[:position] + replacement + token[position + 1 :]
        with pytest.raises(InvalidLinkError):
            decode_token(settings, tampered)


def test_role_changed_to_organizer_is_rejected_by_the_signature(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    token = issue_token(settings, make_link(role=Role.PARTICIPANT))
    header, _, signature = token.split(".")
    payload = decode_payload(token)
    payload["role"] = "organizer"
    forged = f"{header}.{b64(payload)}.{signature}"
    assert forged != token
    with pytest.raises(InvalidLinkError):
        decode_token(settings, forged)


def test_token_signed_with_another_secret_is_rejected(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    claims = {"jti": new_id("lnk"), "mid": new_id("mtg"), "role": "organizer"}
    forged = jwt.encode(claims, "un-autre-secret-" + "q" * 40, algorithm="HS256")
    with pytest.raises(InvalidLinkError):
        decode_token(settings, forged)


def test_alg_none_is_rejected(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    header = b64({"alg": "none", "typ": "JWT"})
    payload = b64({"jti": new_id("lnk"), "mid": new_id("mtg"), "role": "organizer"})
    for forged in (f"{header}.{payload}.", f"{header}.{payload}"):
        with pytest.raises(InvalidLinkError):
            decode_token(settings, forged)


def test_other_hmac_algorithms_are_rejected_even_with_the_right_secret(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    secret = settings.link_signing_secret.get_secret_value()
    claims = {"jti": new_id("lnk"), "mid": new_id("mtg"), "role": "organizer"}
    with pytest.raises(InvalidLinkError):
        decode_token(settings, jwt.encode(claims, secret, algorithm="HS512"))


def test_token_missing_a_required_claim_is_rejected(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    secret = settings.link_signing_secret.get_secret_value()
    full = {"jti": new_id("lnk"), "mid": new_id("mtg"), "role": "participant"}
    for missing in full:
        claims = {k: v for k, v in full.items() if k != missing}
        with pytest.raises(InvalidLinkError):
            decode_token(settings, jwt.encode(claims, secret, algorithm="HS256"))


def test_claim_of_the_wrong_type_is_rejected(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    secret = settings.link_signing_secret.get_secret_value()
    claims = {"jti": 12, "mid": new_id("mtg"), "role": "participant"}
    with pytest.raises(InvalidLinkError):
        decode_token(settings, jwt.encode(claims, secret, algorithm="HS256"))


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        " ",
        "abc",
        "a.b.c",
        "..",
        "....",
        "eyJhbGciOiJIUzI1NiJ9.e30.",
        "é.é.é",
        "x" * 5000,
        "a." * 3000,
        "\x00",
    ],
)
def test_garbage_is_rejected_with_the_same_error(set_env, garbage: str) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    with pytest.raises(InvalidLinkError) as error:
        decode_token(settings, garbage)
    assert error.value.message == "lien invalide"


def test_expired_looking_claims_do_not_matter(set_env) -> None:  # type: ignore[no-untyped-def]
    """Après la réunion, le lien reste reconnu (pour afficher « réunion terminée »)."""
    settings = make_settings(set_env)
    secret = settings.link_signing_secret.get_secret_value()
    claims = {"jti": new_id("lnk"), "mid": new_id("mtg"), "role": "participant", "exp": 1}
    assert decode_token(settings, jwt.encode(claims, secret, algorithm="HS256"))["exp"] == 1


# --- entropie ------------------------------------------------------------------------------------


def test_link_ids_are_unique_and_have_128_random_bits() -> None:
    generated = {new_id("lnk") for _ in range(100_000)}
    assert len(generated) == 100_000
    assert all(len(i.removeprefix("lnk_")) == 32 for i in generated)  # 32 hex = 128 bits
    # UUID4 fixe 6 bits (version et variante) : ici aucun chiffre n'est constant.
    assert len({i[len("lnk_") + 12] for i in generated}) > 8  # chiffre de « version » UUID4
    assert len({i[len("lnk_") + 16] for i in generated}) > 8  # chiffre de « variante » UUID4


def test_100_000_tokens_are_unique_and_unrelated(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    meeting_id = new_id("mtg")
    links = [
        make_link(meeting_id=meeting_id, role=Role.ORGANIZER if i % 2 else Role.PARTICIPANT)
        for i in range(100_000)
    ]
    tokens = [issue_token(settings, link) for link in links]
    assert len(set(tokens)) == 100_000
    assert len({link.id for link in links}) == 100_000
    # Les signatures (donc ce qui est invérifiable sans le secret) ne se répètent jamais.
    assert len({t.rsplit(".", 1)[1] for t in tokens}) == 100_000


def test_organizer_token_cannot_be_derived_from_a_participant_token(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    meeting_id = new_id("mtg")
    participant = make_link(meeting_id=meeting_id, role=Role.PARTICIPANT)
    organizer = make_link(meeting_id=meeting_id, role=Role.ORGANIZER)
    p_token = issue_token(settings, participant)
    o_token = issue_token(settings, organizer)
    # Même réunion, mais rien en commun dans l'identifiant ni dans la signature.
    assert participant.id != organizer.id
    assert p_token.rsplit(".", 1)[1] != o_token.rsplit(".", 1)[1]
    assert p_token.split(".")[0] == o_token.split(".")[0]  # seul l'en-tête (public) est commun
