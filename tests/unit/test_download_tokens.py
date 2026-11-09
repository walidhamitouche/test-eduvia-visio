"""Jetons des URL de téléchargement (étape 15) : signature, expiration à 15 min, falsification.

Logique pure, horloge passée en paramètre : aucune base, aucun S3.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from tests.conftest import valid_env
from visio.config import Settings
from visio.db.models import LinkRow, new_id
from visio.domain.errors import (
    DownloadLinkExpiredError,
    InvalidDownloadLinkError,
    InvalidLinkError,
)
from visio.domain.roles import Role
from visio.services import download_tokens
from visio.services.download_tokens import DOWNLOAD_URL_TTL, download_url, issue, verify
from visio.services.links import MAX_TOKEN_LENGTH, decode_token, issue_token

NOW = datetime(2027, 1, 2, 18, 30, 0, tzinfo=UTC)
ARTIFACT = "art_" + "a" * 32


def make_settings(set_env, **extra: str) -> Settings:  # type: ignore[no-untyped-def]
    set_env({**valid_env(), **extra})
    return Settings()  # type: ignore[call-arg]


def b64(data: object) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def forged(settings: Settings, claims: dict[str, object], key: bytes | str | None = None) -> str:
    """Un jeton signé comme le nôtre (clé dérivée) avec des claims au choix."""
    signing_key = key if key is not None else download_tokens._signing_key(settings)
    return jwt.encode(claims, signing_key, algorithm="HS256")


# --- durée de vie : 15 minutes au plus ----------------------------------------------------------


def test_ttl_is_the_15_minutes_of_the_requirement() -> None:
    assert timedelta(minutes=15) == DOWNLOAD_URL_TTL


def test_a_fresh_token_gives_back_its_artifact(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    grant = issue(settings, ARTIFACT, now=NOW)
    assert verify(settings, grant.token, now=NOW) == ARTIFACT
    assert grant.expires_at == NOW + DOWNLOAD_URL_TTL


@pytest.mark.parametrize(
    ("elapsed", "valid"),
    [
        (timedelta(0), True),
        (timedelta(minutes=14, seconds=59), True),
        (timedelta(minutes=14, seconds=59, milliseconds=999), True),
        (timedelta(minutes=15), False),  # pile à 15 min : périmé
        (timedelta(minutes=15, seconds=1), False),
        (timedelta(hours=3), False),
        (timedelta(days=30), False),
    ],
)
def test_expiry_boundary_is_exact(set_env, elapsed: timedelta, valid: bool) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    grant = issue(settings, ARTIFACT, now=NOW)
    if valid:
        assert verify(settings, grant.token, now=NOW + elapsed) == ARTIFACT
    else:
        with pytest.raises(DownloadLinkExpiredError):
            verify(settings, grant.token, now=NOW + elapsed)


def test_lifetime_is_never_longer_than_15_minutes_even_with_fractional_seconds(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    now = NOW + timedelta(microseconds=900_000)
    grant = issue(settings, ARTIFACT, now=now)
    assert grant.expires_at <= now + DOWNLOAD_URL_TTL
    with pytest.raises(DownloadLinkExpiredError):
        verify(settings, grant.token, now=now + DOWNLOAD_URL_TTL)


def test_the_expiry_does_not_depend_on_the_system_clock(set_env) -> None:  # type: ignore[no-untyped-def]
    """Un jeton émis dans le passé lointain est périmé, un jeton du futur est valable : seule
    l'horloge passée en paramètre compte (c'est ce qui rend les tests possibles)."""
    settings = make_settings(set_env)
    future = datetime(2090, 1, 1, tzinfo=UTC)
    grant = issue(settings, ARTIFACT, now=future)
    assert verify(settings, grant.token, now=future + timedelta(minutes=1)) == ARTIFACT


# --- URL -----------------------------------------------------------------------------------------


def test_the_url_is_on_the_public_domain_and_never_on_the_s3(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(
        set_env, PUBLIC_BASE_URL="https://visio.eduvia.app/", S3_ENDPOINT="https://s3.eduvia.test"
    )
    grant = issue(settings, ARTIFACT, now=NOW)
    url = download_url(settings, grant)
    assert url == f"https://visio.eduvia.app/v1/downloads/{grant.token}"
    assert "s3.eduvia.test" not in url
    assert settings.s3_bucket not in url
    assert ARTIFACT not in url.split("/v1/downloads/")[0]


def test_each_grant_is_a_distinct_token_after_time_passes(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    first = issue(settings, ARTIFACT, now=NOW)
    second = issue(settings, ARTIFACT, now=NOW + timedelta(seconds=1))
    assert first.token != second.token
    assert verify(settings, second.token, now=NOW + timedelta(seconds=2)) == ARTIFACT


# --- falsification ---------------------------------------------------------------------------


def test_changing_any_character_invalidates_the_token(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    token = issue(settings, ARTIFACT, now=NOW).token
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    for index, original in enumerate(token):
        if original == ".":
            continue
        replacement = next(c for c in alphabet if c != original)
        tampered = token[:index] + replacement + token[index + 1 :]
        with pytest.raises(InvalidDownloadLinkError):
            verify(settings, tampered, now=NOW)


def test_swapping_the_artifact_id_breaks_the_signature(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    header, payload, signature = issue(settings, ARTIFACT, now=NOW).token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["aid"] = "art_" + "b" * 32
    with pytest.raises(InvalidDownloadLinkError):
        verify(settings, f"{header}.{b64(claims)}.{signature}", now=NOW)


def test_extending_the_expiry_breaks_the_signature(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    header, payload, signature = issue(settings, ARTIFACT, now=NOW).token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["exp"] += 86_400
    with pytest.raises(InvalidDownloadLinkError):
        verify(settings, f"{header}.{b64(claims)}.{signature}", now=NOW + timedelta(hours=1))


@pytest.mark.parametrize(
    "token",
    ["", "abc", "a.b.c", "....", "x" * 50, "é.é.é", "..", "a" * (MAX_TOKEN_LENGTH + 1)],
)
def test_garbage_is_invalid(set_env, token: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(InvalidDownloadLinkError):
        verify(make_settings(set_env), token, now=NOW)


def test_another_secret_is_refused(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    other = make_settings(set_env, LINK_SIGNING_SECRET="un-tout-autre-secret-" + "z" * 40)
    token = issue(other, ARTIFACT, now=NOW).token
    with pytest.raises(InvalidDownloadLinkError):
        verify(settings, token, now=NOW)


def test_the_raw_link_secret_is_not_the_signing_key(set_env) -> None:  # type: ignore[no-untyped-def]
    """Un jeton signé avec le secret des liens tel quel (sans la dérivation) est refusé."""
    settings = make_settings(set_env)
    raw = settings.link_signing_secret.get_secret_value()
    claims = {"aid": ARTIFACT, "use": "artifact-download", "exp": int(NOW.timestamp()) + 600}
    with pytest.raises(InvalidDownloadLinkError):
        verify(settings, forged(settings, claims, key=raw), now=NOW)


def test_only_hs256_is_accepted(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    claims = {"aid": ARTIFACT, "use": "artifact-download", "exp": int(NOW.timestamp()) + 600}
    none_token = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}."
    with pytest.raises(InvalidDownloadLinkError):
        verify(settings, none_token, now=NOW)
    key = download_tokens._signing_key(settings)
    with pytest.raises(InvalidDownloadLinkError):
        verify(settings, jwt.encode(claims, key * 2, algorithm="HS512"), now=NOW)


@pytest.mark.parametrize(
    "claims",
    [
        {"use": "artifact-download", "exp": 9_999_999_999},  # sans artefact
        {"aid": ARTIFACT, "exp": 9_999_999_999},  # sans usage
        {"aid": ARTIFACT, "use": "artifact-download"},  # sans expiration
        {"aid": ARTIFACT, "use": "join", "exp": 9_999_999_999},  # autre usage
        {"aid": 123, "use": "artifact-download", "exp": 9_999_999_999},  # artefact mal typé
        {"aid": ARTIFACT, "use": "artifact-download", "exp": "9999999999"},  # exp texte
        {"aid": ARTIFACT, "use": "artifact-download", "exp": True},  # exp booléen
        {"aid": ARTIFACT, "use": "artifact-download", "exp": 9_999_999_999.5},  # exp décimal
    ],
)
def test_a_validly_signed_token_with_wrong_claims_is_invalid(set_env, claims) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    with pytest.raises(InvalidDownloadLinkError):
        verify(settings, forged(settings, claims), now=NOW)


def test_a_token_that_is_not_canonical_is_refused(set_env) -> None:  # type: ignore[no-untyped-def]
    """Un caractère de remplissage superflu ne doit pas donner un second jeton valable."""
    settings = make_settings(set_env)
    token = issue(settings, ARTIFACT, now=NOW).token
    header, payload, signature = token.split(".")
    assert verify(settings, token, now=NOW) == ARTIFACT
    with pytest.raises(InvalidDownloadLinkError):
        verify(settings, f"{header}.{payload}=.{signature}", now=NOW)


def test_an_expired_token_only_reveals_expiry_if_the_signature_is_good(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    other = make_settings(set_env, LINK_SIGNING_SECRET="un-tout-autre-secret-" + "z" * 40)
    old = issue(other, ARTIFACT, now=NOW).token
    with pytest.raises(InvalidDownloadLinkError):  # pas « expiré » : on ne dit rien d'un faux
        verify(settings, old, now=NOW + timedelta(days=1))


# --- séparation avec les liens de visio ------------------------------------------------------


def test_a_visio_link_token_never_opens_a_download(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    link = LinkRow(id=new_id("lnk"), meeting_id=new_id("mtg"), role=Role.ORGANIZER, created_at=NOW)
    with pytest.raises(InvalidDownloadLinkError):
        verify(settings, issue_token(settings, link), now=NOW)


def test_a_download_token_never_opens_a_meeting(set_env) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(set_env)
    token = issue(settings, ARTIFACT, now=NOW).token
    with pytest.raises(InvalidLinkError):
        decode_token(settings, token)
