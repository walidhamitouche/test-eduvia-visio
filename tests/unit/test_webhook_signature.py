"""Signature des webhooks sortants (étape 12) : ce qu'Eduvia doit vérifier."""

from __future__ import annotations

import hashlib
import hmac

from visio.services.webhooks import sign, verify_signature

SECRET = "s" * 40
BODY = b'{"id":"evt_1","type":"meeting.started"}'
NOW = 1_800_000_000


def test_a_correct_signature_is_accepted() -> None:
    signature = sign(SECRET, NOW, BODY)
    assert verify_signature(SECRET, BODY, timestamp=str(NOW), signature=signature, now=NOW)


def test_signature_format_is_documented_and_reproducible() -> None:
    """Format publié dans docs/WEBHOOKS.md : `v1=` + HMAC-SHA256 de `<timestamp>.<corps>`."""
    expected = hmac.new(SECRET.encode(), f"{NOW}.".encode() + BODY, hashlib.sha256).hexdigest()
    assert sign(SECRET, NOW, BODY) == f"v1={expected}"


def test_a_modified_body_is_rejected() -> None:
    signature = sign(SECRET, NOW, BODY)
    tampered = BODY.replace(b"started", b"ended")
    assert not verify_signature(SECRET, tampered, timestamp=str(NOW), signature=signature, now=NOW)


def test_a_modified_timestamp_is_rejected() -> None:
    signature = sign(SECRET, NOW, BODY)
    assert not verify_signature(SECRET, BODY, timestamp=str(NOW + 1), signature=signature, now=NOW)


def test_another_secret_is_rejected() -> None:
    signature = sign("x" * 40, NOW, BODY)
    assert not verify_signature(SECRET, BODY, timestamp=str(NOW), signature=signature, now=NOW)


def test_a_timestamp_that_is_too_old_is_rejected_even_if_the_signature_is_right() -> None:
    signature = sign(SECRET, NOW, BODY)
    assert verify_signature(SECRET, BODY, timestamp=str(NOW), signature=signature, now=NOW + 299)
    assert not verify_signature(
        SECRET, BODY, timestamp=str(NOW), signature=signature, now=NOW + 301
    )


def test_a_timestamp_from_the_future_is_rejected() -> None:
    signature = sign(SECRET, NOW, BODY)
    assert not verify_signature(
        SECRET, BODY, timestamp=str(NOW), signature=signature, now=NOW - 301
    )


def test_garbage_is_rejected_without_crashing() -> None:
    assert not verify_signature(SECRET, BODY, timestamp="pas-un-nombre", signature="v1=00", now=NOW)
    assert not verify_signature(SECRET, BODY, timestamp=str(NOW), signature="", now=NOW)
