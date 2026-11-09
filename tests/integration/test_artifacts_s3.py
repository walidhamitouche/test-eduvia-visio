"""Étape 15 contre un vrai serveur S3 HTTP local (moto) : lecture par plage, purge, bucket
versionné, scénario 8 de bout en bout et outil `artifacts-check`. Sans Docker."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from moto.server import ThreadedMotoServer
from sqlalchemy.orm import Session

from tests.integration.conftest import meeting_body
from visio.config import Settings
from visio.db.models import ArtifactKind, ArtifactRow, ArtifactStatus
from visio.domain.clock import FixedClock
from visio.main import create_app
from visio.services.storage import ObjectNotFoundError, Storage, StorageError
from visio.tools.artifactscheck import FAIL, OK, main, run_check


def _as_client(client: TestClient) -> Any:
    """`run_check` attend un `httpx.Client` ; selon les paquets installés (httpx2 arrive avec
    l'extra `transcription`), Starlette bâtit `TestClient` sur `httpx` ou sur `httpx2`."""
    return client


PORT = 5597
MP4 = b"\x00\x00\x00\x20ftypisom" + bytes(range(256)) * 400  # 102 412 octets


@pytest.fixture(scope="module")
def s3_endpoint() -> Iterator[str]:
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT, verbose=False)
    server.start()
    try:
        yield f"http://127.0.0.1:{PORT}"
    finally:
        server.stop()


@pytest.fixture
def storage(s3_endpoint: str) -> Storage:  # remplace le faux S3 du conftest
    store = Storage(
        endpoint=s3_endpoint,
        bucket=f"visio-{uuid.uuid4().hex[:12]}",
        access_key="s3-access",
        secret_key="test-s3-secret-" + "e" * 40,
        region="eu-west-3",
    )
    store.ensure_bucket()
    return store


def key_for(meeting_id: str = "mtg_1") -> str:
    return f"meetings/{meeting_id}/recordings/{uuid.uuid4()}.mp4"


# --- Storage : lecture par plage ---------------------------------------------------------------


def test_open_range_reads_exactly_the_requested_bytes(storage: Storage) -> None:
    key = key_for()
    storage.put(key, MP4, content_type="video/mp4")
    for start, end in [
        (0, 0),
        (0, 99),
        (4, 7),
        (100_000, len(MP4) - 1),
        (len(MP4) - 1, len(MP4) - 1),
    ]:
        stream = storage.open_range(key, start, end)
        assert stream.length == end - start + 1
        assert b"".join(stream.chunks()) == MP4[start : end + 1]


def test_open_range_streams_in_chunks(storage: Storage) -> None:
    key = key_for()
    storage.put(key, MP4)
    stream = storage.open_range(key, 0, len(MP4) - 1)
    sizes = [len(c) for c in stream.chunks(chunk_size=40_000)]
    assert sizes == [40_000, 40_000, len(MP4) - 80_000]


def test_open_range_errors(storage: Storage) -> None:
    with pytest.raises(ObjectNotFoundError):
        storage.open_range(key_for(), 0, 9)
    storage.put("a.bin", b"abc")
    with pytest.raises(ValueError, match="plage"):
        storage.open_range("a.bin", 5, 2)
    with pytest.raises(StorageError):  # début au-delà de la fin : refus du S3 (InvalidRange)
        storage.open_range("a.bin", 50, 60)
    with pytest.raises(ValueError):
        storage.open_range("../secret", 0, 1)


def test_an_unreachable_s3_is_a_storage_error_not_a_crash() -> None:
    dead = Storage(
        endpoint="http://127.0.0.1:1",
        bucket="x",
        access_key="a",
        secret_key="b" * 40,
        region="eu-west-3",
    )
    with pytest.raises(StorageError, match="injoignable"):
        dead.open_range("k", 0, 1)
    with pytest.raises(StorageError, match="injoignable"):
        dead.purge("meetings/m/recordings/")


# --- Storage : purge ---------------------------------------------------------------------------


def test_purge_removes_the_prefix_and_nothing_else(storage: Storage) -> None:
    stem = f"meetings/mtg_1/recordings/{uuid.uuid4()}"
    other = key_for()
    for name in (f"{stem}.mp4", f"{stem}.mp4.json", f"{stem}/seg_00000.ts", f"{stem}/seg.m3u8"):
        storage.put(name, b"x")
    storage.put(other, b"keep")
    assert storage.purge(stem) == 4
    assert storage.list_objects("meetings/") == [(other, 4)]
    assert storage.purge(stem) == 0  # rejouable


def test_purge_refuses_dangerous_prefixes(storage: Storage) -> None:
    for bad in ("", "/meetings/", "meetings/../x"):
        with pytest.raises(ValueError):
            storage.purge(bad)


def test_purge_removes_every_version_of_a_versioned_bucket(storage: Storage) -> None:
    """Sans cela, « supprimer » ne ferait que poser un marqueur : les versions resteraient."""
    client: Any = storage._client
    client.put_bucket_versioning(
        Bucket=storage.bucket, VersioningConfiguration={"Status": "Enabled"}
    )
    key = key_for()
    storage.put(key, b"version 1")
    storage.put(key, b"version 2 plus longue")
    assert len(client.list_object_versions(Bucket=storage.bucket)["Versions"]) == 2

    assert storage.purge(key) == 2
    assert storage.size(key) is None
    listing = client.list_object_versions(Bucket=storage.bucket)
    assert not listing.get("Versions") and not listing.get("DeleteMarkers")


def test_a_plain_delete_on_a_versioned_bucket_would_have_left_versions(storage: Storage) -> None:
    """Preuve que le cas existe : le `delete` simple laisse les versions récupérables."""
    client: Any = storage._client
    client.put_bucket_versioning(
        Bucket=storage.bucket, VersioningConfiguration={"Status": "Enabled"}
    )
    key = key_for()
    storage.put(key, b"secret")
    storage.delete(key)
    assert storage.size(key) is None  # l'objet semble supprimé...
    assert client.list_object_versions(Bucket=storage.bucket)["Versions"]  # ... mais non


def test_unreadable_versioning_falls_back_to_a_plain_purge(
    storage: Storage, caplog: pytest.LogCaptureFixture
) -> None:
    from botocore.exceptions import ClientError

    key = key_for()
    storage.put(key, b"x")
    client: Any = storage._client

    def denied(**_: Any) -> Any:
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetBucketVersioning")

    client.get_bucket_versioning = denied
    assert storage.purge(key) == 1
    assert storage.size(key) is None
    assert "AccessDenied" in caplog.text


# --- scénario 8 de bout en bout, contre un vrai S3 -----------------------------------------------


def make_ready_recording(
    client: TestClient, auth: dict[str, str], db: Session, storage: Storage
) -> tuple[str, ArtifactRow]:
    created = client.post("/v1/meetings", json=meeting_body(), headers=auth).json()
    key = key_for(created["id"])
    storage.put(key, MP4, content_type="video/mp4")
    row = ArtifactRow(
        meeting_id=created["id"],
        kind=ArtifactKind.RECORDING,
        status=ArtifactStatus.READY,
        storage_key=key,
        size_bytes=len(MP4),
    )
    db.add(row)
    db.commit()
    return str(created["id"]), row


def test_download_ranges_and_deletion_against_a_real_s3(
    client: TestClient, auth: dict[str, str], db: Session, storage: Storage, clock: FixedClock
) -> None:
    meeting_id, row = make_ready_recording(client, auth, db, storage)
    row_id, key = row.id, str(row.storage_key)
    (item,) = client.get(f"/v1/meetings/{meeting_id}/recordings", headers=auth).json()["recordings"]
    path = item["download_url"].removeprefix("http://visio.test")

    whole = client.get(path)
    assert whole.status_code == 200 and whole.content == MP4
    part = client.get(path, headers={"Range": "bytes=-100"})
    assert part.status_code == 206 and part.content == MP4[-100:]
    assert client.get(path, headers={"Range": f"bytes={len(MP4)}-"}).status_code == 416

    # (L'accès anonyme direct au S3 est vérifié sur le vrai S3 par `artifacts-check` : moto
    # ne contrôle pas les accès.)
    assert client.delete(f"/v1/recordings/{row_id}", headers=auth).status_code == 204
    assert storage.size(key) is None  # vérifié directement sur le S3
    assert storage.list_objects(f"meetings/{meeting_id}/") == []
    gone = client.get(path)  # URL émise avant la suppression, encore dans ses 15 minutes
    assert gone.status_code == 410
    assert gone.json()["error"]["code"] == "artifact_deleted"
    assert client.delete(f"/v1/recordings/{row_id}", headers=auth).status_code == 404


def test_a_real_s3_down_during_download_is_a_502(
    client: TestClient, auth: dict[str, str], db: Session, storage: Storage
) -> None:
    meeting_id, _ = make_ready_recording(client, auth, db, storage)
    (item,) = client.get(f"/v1/meetings/{meeting_id}/recordings", headers=auth).json()["recordings"]
    path = item["download_url"].removeprefix("http://visio.test")
    real = client.app.state.storage  # type: ignore[attr-defined]
    client.app.state.storage = Storage(  # type: ignore[attr-defined]
        endpoint="http://127.0.0.1:1",
        bucket=real.bucket,
        access_key="a",
        secret_key="b" * 40,
        region="eu-west-3",
    )
    try:
        response = client.get(path)
    finally:
        client.app.state.storage = real  # type: ignore[attr-defined]
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "storage_unavailable"


# --- l'outil artifacts-check ---------------------------------------------------------------------


@pytest.fixture
def api(
    settings: Settings, clock: FixedClock, storage: Storage
) -> Iterator[tuple[TestClient, Settings]]:
    clock.set(datetime(2027, 1, 2, 9, 0, tzinfo=UTC))
    app = create_app(settings=settings, clock=clock, storage=storage, run_workers=False)
    with TestClient(app, base_url="http://visio.test") as http:
        yield http, settings


def test_artifacts_check_passes_without_deleting(
    api: tuple[TestClient, Settings], db: Session, storage: Storage, auth: dict[str, str]
) -> None:
    http, settings = api
    meeting_id, row = make_ready_recording(http, auth, db, storage)
    lines = run_check(
        _as_client(http), storage, settings, meeting_id, delete=False, raw_status=lambda _: 403
    )
    assert not [text for level, text in lines if level == FAIL], lines
    assert any("Range bytes=0-99" in text for _, text in lines)
    assert storage.size(str(row.storage_key)) == len(MP4)  # lecture seule : rien n'est supprimé


def test_artifacts_check_with_delete_proves_scenario_8(
    api: tuple[TestClient, Settings], db: Session, storage: Storage, auth: dict[str, str]
) -> None:
    http, settings = api
    meeting_id, row = make_ready_recording(http, auth, db, storage)
    key = str(row.storage_key)
    lines = run_check(
        _as_client(http), storage, settings, meeting_id, delete=True, raw_status=lambda _: 403
    )
    assert not [text for level, text in lines if level == FAIL], lines
    assert (OK, "DELETE : 204") in lines
    assert storage.size(key) is None


def test_artifacts_check_flags_a_public_bucket(
    api: tuple[TestClient, Settings], db: Session, storage: Storage, auth: dict[str, str]
) -> None:
    http, settings = api
    meeting_id, _ = make_ready_recording(http, auth, db, storage)
    lines = run_check(
        _as_client(http), storage, settings, meeting_id, delete=False, raw_status=lambda _: 200
    )
    assert any(level == FAIL and "public" in text for level, text in lines)


def test_artifacts_check_reports_a_meeting_without_recording(
    api: tuple[TestClient, Settings], storage: Storage, auth: dict[str, str]
) -> None:
    http, settings = api
    created = http.post("/v1/meetings", json=meeting_body(), headers=auth).json()
    lines = run_check(_as_client(http), storage, settings, created["id"], delete=False)
    assert lines[-1][0] == FAIL and "aucun enregistrement prêt" in lines[-1][1]
    unknown = run_check(_as_client(http), storage, settings, "mtg_inconnue", delete=False)
    assert unknown[-1][0] == FAIL


def test_artifacts_check_cli_needs_a_valid_configuration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    messages: list[str] = []
    assert main([], out=messages.append) == 1  # aucune configuration dans l'environnement du test
    assert messages
