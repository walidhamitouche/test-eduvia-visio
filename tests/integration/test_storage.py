"""Étape 13b : service de stockage S3 et outil `s3check`, contre un S3 simulé (moto), sans Docker.

Le vrai MinIO se vérifie à la main : `uv run task s3-init` puis `uv run task s3-check`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from moto.server import ThreadedMotoServer

from visio.services.storage import ObjectNotFoundError, Storage, StorageError
from visio.tools.s3check import run_check

PORT = 5599


@pytest.fixture(scope="session")
def s3_endpoint() -> Iterator[str]:
    """Un vrai serveur S3 HTTP local (moto) : le client est testé comme face à MinIO."""
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT, verbose=False)
    server.start()
    try:
        yield f"http://127.0.0.1:{PORT}"
    finally:
        server.stop()


def make_storage(endpoint: str) -> Storage:
    return Storage(
        endpoint=endpoint,
        bucket=f"visio-{uuid.uuid4().hex[:12]}",
        access_key="s3-access",
        secret_key="test-s3-secret-" + "e" * 40,
        region="eu-west-3",
    )


@pytest.fixture
def storage(s3_endpoint: str) -> Storage:
    store = make_storage(s3_endpoint)
    store.ensure_bucket()
    return store


def test_write_read_delete_roundtrip(storage: Storage) -> None:
    key = "meetings/mtg_1/recordings/abc.mp4"
    storage.put(key, b"\x00\x01binaire", content_type="video/mp4")
    assert storage.exists(key)
    assert storage.get(key) == b"\x00\x01binaire"

    storage.delete(key)
    assert not storage.exists(key)
    with pytest.raises(ObjectNotFoundError):
        storage.get(key)


def test_size_reads_the_object_size_or_none(storage: Storage) -> None:
    storage.put("meetings/m/recordings/size.mp4", b"x" * 1234)
    assert storage.size("meetings/m/recordings/size.mp4") == 1234
    assert storage.size("meetings/m/recordings/absent.mp4") is None


def test_delete_is_repeatable(storage: Storage) -> None:
    storage.delete("meetings/mtg_1/absent.mp4")  # ne lève rien : à l'appelant de savoir
    storage.put("a.txt", b"x")
    storage.delete("a.txt")
    storage.delete("a.txt")


def test_missing_object(storage: Storage) -> None:
    assert not storage.exists("nope.txt")
    with pytest.raises(ObjectNotFoundError):
        storage.get("nope.txt")


def test_ensure_bucket_is_idempotent(s3_endpoint: str) -> None:
    store = make_storage(s3_endpoint)
    assert not store.bucket_exists()
    assert store.ensure_bucket() is True
    assert store.bucket_exists()
    assert store.ensure_bucket() is False


def test_missing_bucket_is_reported_clearly(s3_endpoint: str) -> None:
    store = make_storage(s3_endpoint)
    with pytest.raises(StorageError):
        store.put("a.txt", b"x")


def test_from_settings_uses_the_configuration(s3_endpoint: str) -> None:
    from tests.conftest import valid_env
    from visio.config import Settings

    env = {**valid_env(), "S3_ENDPOINT": s3_endpoint}
    settings = Settings(**{key.lower(): value for key, value in env.items()})  # type: ignore[arg-type]
    store = Storage.from_settings(settings)
    assert store.endpoint == s3_endpoint
    assert store.bucket == "visio-test"


@pytest.mark.parametrize("key", ["", "/absolu.txt", "../dehors.txt", "a/../../b.txt"])
def test_unsafe_keys_are_refused(storage: Storage, key: str) -> None:
    with pytest.raises(ValueError, match="clé"):
        storage.put(key, b"x")
    with pytest.raises(ValueError, match="clé"):
        storage.get(key)
    with pytest.raises(ValueError, match="clé"):
        storage.delete(key)


def test_unreachable_s3_gives_a_storage_error() -> None:
    store = Storage(
        endpoint="http://127.0.0.1:1",
        bucket="b",
        access_key="a",
        secret_key="s",
        region="eu-west-3",
    )
    with pytest.raises(StorageError, match="injoignable"):
        store.put("a.txt", b"x")
    with pytest.raises(StorageError, match="injoignable"):
        store.bucket_exists()


def test_errors_never_contain_the_secret_key() -> None:
    store = Storage(
        endpoint="http://127.0.0.1:1",
        bucket="b",
        access_key="ACCESS-VISIBLE-NOWHERE",
        secret_key="SECRET-VISIBLE-NOWHERE",
        region="eu-west-3",
    )
    with pytest.raises(StorageError) as caught:
        store.put("a.txt", b"x")
    assert "SECRET-VISIBLE-NOWHERE" not in str(caught.value)
    assert "ACCESS-VISIBLE-NOWHERE" not in str(caught.value)


# --- outil s3check ---------------------------------------------------------------------------


def test_check_passes_on_a_private_bucket(storage: Storage) -> None:
    assert run_check(storage, fetch=lambda url: 403) == []


def test_check_cleans_up_its_test_object(storage: Storage) -> None:
    run_check(storage, fetch=lambda url: 403)
    listing = storage._client.list_objects_v2(Bucket=storage.bucket)  # noqa: SLF001
    assert listing.get("KeyCount", 0) == 0


def test_check_flags_a_public_bucket(storage: Storage) -> None:
    problems = run_check(storage, fetch=lambda url: 200)
    assert any("PUBLIC" in problem for problem in problems)


def test_check_flags_an_unexpected_anonymous_answer(storage: Storage) -> None:
    problems = run_check(storage, fetch=lambda url: 500)
    assert any("500" in problem for problem in problems)


def test_check_reports_a_missing_bucket(s3_endpoint: str) -> None:
    problems = run_check(make_storage(s3_endpoint), fetch=lambda url: 403)
    assert problems


def test_init_waits_for_a_slow_s3() -> None:
    from visio.services.storage import StorageError
    from visio.tools.s3check import _with_retries

    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise StorageError("pas encore prêt")
        return "ok"

    assert _with_retries(flaky, attempts=5, pause=0) == "ok"
    assert calls["n"] == 3


def test_init_gives_up_after_the_last_attempt() -> None:
    from visio.tools.s3check import _with_retries

    def broken() -> None:
        raise StorageError("toujours en panne")

    with pytest.raises(StorageError, match="toujours en panne"):
        _with_retries(broken, attempts=3, pause=0)


def test_list_objects_filters_by_prefix_and_gives_sizes(storage: Storage) -> None:
    storage.put("meetings/mtg_1/recordings/a.mp4", b"12345")
    storage.put("meetings/mtg_2/recordings/b.mp4", b"1234567")
    storage.put("healthcheck/x.mp4", b"1")
    found = dict(storage.list_objects("meetings/"))
    assert found == {
        "meetings/mtg_1/recordings/a.mp4": 5,
        "meetings/mtg_2/recordings/b.mp4": 7,
    }
    assert storage.list_objects("absent/") == []


# --- sonde de santé (étape 17b) ----------------------------------------------------------------


def test_ping_passes_when_the_bucket_exists(storage: Storage) -> None:
    storage.ping()


def test_ping_fails_clearly_when_the_bucket_is_missing(s3_endpoint: str) -> None:
    with pytest.raises(StorageError, match="bucket introuvable"):
        make_storage(s3_endpoint).ping()


def test_ping_fails_when_the_s3_is_unreachable() -> None:
    store = Storage(
        endpoint="http://127.0.0.1:1",
        bucket="b",
        access_key="a",
        secret_key="s",
        region="eu-west-3",
    )
    with pytest.raises(StorageError, match="injoignable"):
        store.ping()


def test_ping_writes_nothing(storage: Storage) -> None:
    storage.ping()
    assert storage.list_objects() == []
