"""Étape 15 : API des artefacts (liste, URL temporaires, téléchargement par proxy, suppression).

Contre un S3 en mémoire (`FakeStorage`), sans Docker. Le vrai S3 HTTP (moto) est couvert par
`test_artifacts_s3.py`, le vrai S3 de développement par `uv run task artifacts-check`.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openapi_spec_validator import validate
from pydantic import SecretStr
from sqlalchemy.orm import Session

from tests.conftest import valid_env
from tests.integration.helpers import error_code
from visio.config import Settings
from visio.db.migrate import upgrade
from visio.db.models import ArtifactKind, ArtifactRow, ArtifactStatus
from visio.domain.clock import FixedClock
from visio.main import create_app
from visio.services import artifacts as artifacts_service
from visio.services import download_tokens
from visio.services.download_tokens import DOWNLOAD_URL_TTL
from visio.services.storage import CHUNK_SIZE, FakeStorage, ObjectStore

Create = Callable[..., dict[str, Any]]

CONTENT = bytes(range(256)) * 40  # 10 240 octets reconnaissables
BASE = "http://visio.test/v1/downloads/"


def path_of(url: str) -> str:
    assert url.startswith(BASE), url
    return url.removeprefix("http://visio.test")


def add_artifact(
    db: Session,
    storage: FakeStorage,
    meeting_id: str,
    *,
    kind: ArtifactKind = ArtifactKind.RECORDING,
    status: ArtifactStatus = ArtifactStatus.READY,
    content: bytes | None = CONTENT,
    error: str | None = None,
    extension: str = "mp4",
    created_at: datetime | None = None,
) -> ArtifactRow:
    """Un artefact comme les étapes 14 et 16 les laissent : ligne en base + objet sur le S3."""
    folder = "recordings" if kind is ArtifactKind.RECORDING else "transcripts"
    key = f"meetings/{meeting_id}/{folder}/{uuid.uuid4()}.{extension}"
    row = ArtifactRow(
        meeting_id=meeting_id,
        kind=kind,
        status=status,
        storage_key=key,
        size_bytes=len(content) if content is not None and status is ArtifactStatus.READY else None,
        error=error,
    )
    if created_at is not None:
        row.created_at = created_at
    db.add(row)
    db.commit()
    if content is not None:
        storage.objects[key] = content
    return row


@pytest.fixture
def meeting_id(create: Create) -> str:
    return str(create()["id"])


@pytest.fixture
def recording(db: Session, storage: FakeStorage, meeting_id: str) -> ArtifactRow:
    return add_artifact(db, storage, meeting_id)


def listing(client: TestClient, auth: dict[str, str], meeting_id: str) -> list[dict[str, Any]]:
    response = client.get(f"/v1/meetings/{meeting_id}/recordings", headers=auth)
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["recordings"]
    return items


def url_of(client: TestClient, auth: dict[str, str], meeting_id: str) -> str:
    (item,) = listing(client, auth, meeting_id)
    assert item["download_url"], item
    return str(item["download_url"])


# --- liste -----------------------------------------------------------------------------------


def test_listing_needs_the_api_key(client: TestClient, meeting_id: str) -> None:
    assert client.get(f"/v1/meetings/{meeting_id}/recordings").status_code == 401
    bad = {"Authorization": "Bearer pas-la-bonne-cle"}
    assert client.get(f"/v1/meetings/{meeting_id}/recordings", headers=bad).status_code == 401


def test_listing_an_unknown_meeting_is_a_clean_404(
    client: TestClient, auth: dict[str, str]
) -> None:
    response = client.get("/v1/meetings/mtg_inconnue/recordings", headers=auth)
    assert response.status_code == 404
    assert error_code(response) == "meeting_not_found"


def test_a_meeting_without_recording_has_an_empty_list(
    client: TestClient, auth: dict[str, str], meeting_id: str
) -> None:
    response = client.get(f"/v1/meetings/{meeting_id}/recordings", headers=auth)
    assert response.status_code == 200
    assert response.json() == {"meeting_id": meeting_id, "recordings": []}


def test_a_ready_recording_comes_with_a_temporary_url_on_the_public_domain(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    recording: ArtifactRow,
    meeting_id: str,
) -> None:
    (item,) = listing(client, auth, meeting_id)
    assert item["id"] == recording.id
    assert item["meeting_id"] == meeting_id
    assert item["status"] == "ready"
    assert item["size_bytes"] == len(CONTENT)
    assert item["error"] is None
    assert item["download_url"].startswith(BASE)
    # 15 minutes exactement après l'émission, en heure de Paris (le 2 janvier : +01:00).
    expected = (clock.now() + DOWNLOAD_URL_TTL).astimezone(UTC)
    assert item["download_url_expires_at"].endswith("+01:00")
    assert datetime.fromisoformat(item["download_url_expires_at"]) == expected
    assert item["created_at"].endswith("+01:00")


def test_the_listing_never_exposes_the_s3(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    recording: ArtifactRow,
    meeting_id: str,
) -> None:
    """Le bucket est privé : ni clé d'objet, ni endpoint, ni bucket dans ce que l'on renvoie."""
    text = client.get(f"/v1/meetings/{meeting_id}/recordings", headers=auth).text
    assert recording.storage_key is not None
    for secret in (
        recording.storage_key,
        "meetings/",
        settings.s3_endpoint,
        settings.s3_bucket,
        "egress",
    ):
        assert secret not in text


def test_pending_and_failed_recordings_have_no_url(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    storage: FakeStorage,
    meeting_id: str,
) -> None:
    now = datetime(2027, 1, 2, 9, 0, tzinfo=UTC)
    add_artifact(
        db, storage, meeting_id, status=ArtifactStatus.PENDING, content=None, created_at=now
    )
    add_artifact(
        db,
        storage,
        meeting_id,
        status=ArtifactStatus.FAILED,
        content=None,
        error="aucune piste audio ou vidéo n'a été publiée : rien à enregistrer",
        created_at=now + timedelta(minutes=1),
    )
    pending, failed = listing(client, auth, meeting_id)
    assert pending["status"] == "pending"
    assert failed["status"] == "failed"
    assert failed["error"].startswith("aucune piste")
    for item in (pending, failed):
        assert item["download_url"] is None
        assert item["download_url_expires_at"] is None
        assert item["size_bytes"] is None


def test_the_listing_is_ordered_oldest_first_and_ignores_transcripts(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    storage: FakeStorage,
    meeting_id: str,
) -> None:
    base = datetime(2027, 1, 2, 9, 0, tzinfo=UTC)
    second = add_artifact(db, storage, meeting_id, created_at=base + timedelta(hours=1))
    first = add_artifact(db, storage, meeting_id, created_at=base)
    add_artifact(
        db,
        storage,
        meeting_id,
        kind=ArtifactKind.TRANSCRIPT,
        extension="json",
        content=b"{}",
        created_at=base + timedelta(hours=2),
    )
    assert [item["id"] for item in listing(client, auth, meeting_id)] == [first.id, second.id]


def test_a_meeting_never_lists_the_recordings_of_another(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    storage: FakeStorage,
    create: Create,
) -> None:
    mine, other = str(create()["id"]), str(create()["id"])
    add_artifact(db, storage, other)
    assert listing(client, auth, mine) == []


def test_every_listing_gives_a_fresh_url_and_old_ones_live_until_their_expiry(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    recording: ArtifactRow,
    meeting_id: str,
) -> None:
    first = url_of(client, auth, meeting_id)
    clock.advance(timedelta(minutes=10))
    second = url_of(client, auth, meeting_id)
    assert first != second
    clock.advance(timedelta(minutes=6))  # 16 min après la première, 6 après la seconde
    assert client.get(path_of(first)).status_code == 410
    assert client.get(path_of(second)).status_code == 200


def test_get_one_recording_gives_the_same_description_and_a_fresh_url(
    client: TestClient, auth: dict[str, str], recording: ArtifactRow, meeting_id: str
) -> None:
    response = client.get(f"/v1/recordings/{recording.id}", headers=auth)
    assert response.status_code == 200
    body = response.json()
    (listed,) = listing(client, auth, meeting_id)
    assert {k: v for k, v in body.items() if not k.startswith("download")} == {
        k: v for k, v in listed.items() if not k.startswith("download")
    }
    assert client.get(path_of(body["download_url"])).content == CONTENT


def test_get_one_recording_errors(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    storage: FakeStorage,
    meeting_id: str,
) -> None:
    assert client.get("/v1/recordings/art_inconnu").status_code == 401
    response = client.get("/v1/recordings/art_inconnu", headers=auth)
    assert response.status_code == 404
    assert error_code(response) == "artifact_not_found"
    transcript = add_artifact(
        db, storage, meeting_id, kind=ArtifactKind.TRANSCRIPT, extension="json", content=b"{}"
    )
    # Un identifiant de transcription ne désigne pas un enregistrement.
    assert client.get(f"/v1/recordings/{transcript.id}", headers=auth).status_code == 404


# --- téléchargement --------------------------------------------------------------------------


def test_download_serves_the_whole_file_without_any_api_key(
    client: TestClient, auth: dict[str, str], recording: ArtifactRow, meeting_id: str
) -> None:
    url = url_of(client, auth, meeting_id)
    response = client.get(path_of(url))  # aucune clé d'API : le jeton de l'URL fait foi
    assert response.status_code == 200
    assert response.content == CONTENT
    headers = response.headers
    assert headers["content-type"] == "video/mp4"
    assert headers["content-length"] == str(len(CONTENT))
    assert (
        headers["content-disposition"]
        == f'attachment; filename="enregistrement-{recording.id}.mp4"'
    )
    assert headers["accept-ranges"] == "bytes"
    assert headers["etag"] == f'"{recording.id}"'
    assert headers["cache-control"] == "private, no-store"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["x-content-type-options"] == "nosniff"


def test_a_big_file_is_streamed_in_chunks_and_arrives_intact(
    client: TestClient, auth: dict[str, str], db: Session, storage: FakeStorage, meeting_id: str
) -> None:
    big = (bytes(range(251)) * 20_000)[: 3 * CHUNK_SIZE + 12_345]
    add_artifact(db, storage, meeting_id, content=big)
    response = client.get(path_of(url_of(client, auth, meeting_id)))
    assert response.status_code == 200
    assert response.content == big
    assert response.headers["content-length"] == str(len(big))


def test_stored_range_reads_by_chunks_and_closes_itself() -> None:
    store = FakeStorage(objects={"k": b"x" * (2 * CHUNK_SIZE + 5)})
    stream = store.open_range("k", 0, 2 * CHUNK_SIZE + 4)
    sizes = [len(chunk) for chunk in stream.chunks()]
    assert sizes == [CHUNK_SIZE, CHUNK_SIZE, 5]
    assert stream.length == 2 * CHUNK_SIZE + 5


def test_download_is_valid_until_the_15th_minute_exactly(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    recording: ArtifactRow,
    meeting_id: str,
) -> None:
    path = path_of(url_of(client, auth, meeting_id))
    clock.advance(timedelta(minutes=14, seconds=59))
    assert client.get(path).status_code == 200
    clock.advance(timedelta(seconds=1))  # 15 min pile
    expired = client.get(path)
    assert expired.status_code == 410
    assert error_code(expired) == "download_link_expired"
    clock.advance(timedelta(days=1))
    assert client.get(path).status_code == 410


def test_an_invented_or_modified_url_opens_nothing(
    client: TestClient, auth: dict[str, str], recording: ArtifactRow, meeting_id: str
) -> None:
    token = path_of(url_of(client, auth, meeting_id)).rsplit("/", 1)[1]
    flipped = token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")
    for bad in ("abc", "a.b.c", flipped, token + "x", token[:-1], "x" * 600):
        response = client.get(f"/v1/downloads/{bad}")
        assert response.status_code == 404, bad
        assert error_code(response) == "invalid_download_link"
        # Le message est unique : il ne dit jamais pourquoi l'URL est refusée.
        assert response.json()["error"]["message"] == "URL de téléchargement invalide"


def test_a_visio_link_is_not_a_download_url(
    client: TestClient, create: Create, db: Session, storage: FakeStorage
) -> None:
    created = create()
    add_artifact(db, storage, str(created["id"]))
    for link in (created["participant_url"], created["organizer_urls"][0]):
        token = link.rsplit("/j/", 1)[1]
        response = client.get(f"/v1/downloads/{token}")
        assert response.status_code == 404
        assert error_code(response) == "invalid_download_link"


def test_a_token_signed_with_another_secret_is_refused(
    client: TestClient, settings: Settings, recording: ArtifactRow
) -> None:
    other = settings.model_copy(
        update={"link_signing_secret": SecretStr("un-autre-secret-" + "q" * 40)}
    )
    grant = download_tokens.issue(other, recording.id, now=datetime(2027, 1, 2, 9, 0, tzinfo=UTC))
    assert client.get(f"/v1/downloads/{grant.token}").status_code == 404


def test_the_url_is_only_a_get_or_head_route(
    client: TestClient, auth: dict[str, str], recording: ArtifactRow, meeting_id: str
) -> None:
    path = path_of(url_of(client, auth, meeting_id))
    assert client.post(path).status_code == 405
    assert client.delete(path).status_code == 405
    assert client.put(path).status_code == 405


def test_a_zero_byte_file_is_served_as_an_empty_200(
    client: TestClient, auth: dict[str, str], db: Session, storage: FakeStorage, meeting_id: str
) -> None:
    add_artifact(db, storage, meeting_id, content=b"")
    response = client.get(path_of(url_of(client, auth, meeting_id)))
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == "0"


def test_a_transcript_artifact_uses_the_same_mechanism(
    db: Session, storage: FakeStorage, settings: Settings, meeting_id: str
) -> None:
    """Étape 16 : la transcription réutilise liste, URL et téléchargement (même service)."""
    row = add_artifact(
        db,
        storage,
        meeting_id,
        kind=ArtifactKind.TRANSCRIPT,
        extension="json",
        content=b'{"segments": []}',
    )
    now = datetime(2027, 1, 2, 9, 0, tzinfo=UTC)
    grant = artifacts_service.issue_download(settings, row, now=now)
    assert grant is not None
    plan = artifacts_service.prepare_download(db, storage, settings, grant.token, now=now)
    assert plan.content_type == "application/json"
    assert plan.filename == f"transcription-{row.id}.json"
    assert plan.size == len(b'{"segments": []}')
    assert [
        a.id for a in artifacts_service.list_artifacts(db, meeting_id, ArtifactKind.TRANSCRIPT)
    ] == [row.id]
    artifacts_service.delete_artifact(db, storage, row.id, ArtifactKind.TRANSCRIPT)
    assert row.storage_key not in storage.objects


@pytest.mark.parametrize(
    ("extension", "content_type"),
    [
        ("mp4", "video/mp4"),
        ("json", "application/json"),
        ("txt", "text/plain; charset=utf-8"),
        ("srt", "application/x-subrip"),
        ("vtt", "text/vtt"),
        ("bin", "application/octet-stream"),
    ],
)
def test_the_content_type_follows_the_file_extension(
    db: Session,
    storage: FakeStorage,
    settings: Settings,
    meeting_id: str,
    extension: str,
    content_type: str,
) -> None:
    row = add_artifact(db, storage, meeting_id, extension=extension, content=b"abc")
    now = datetime(2027, 1, 2, 9, 0, tzinfo=UTC)
    grant = artifacts_service.issue_download(settings, row, now=now)
    assert grant is not None
    plan = artifacts_service.prepare_download(db, storage, settings, grant.token, now=now)
    assert plan.content_type == content_type


# --- reprise (Range) et HEAD -------------------------------------------------------------------


def fetch(client: TestClient, path: str, **headers: str) -> Any:
    return client.get(path, headers={k.replace("_", "-"): v for k, v in headers.items()})


@pytest.fixture
def path(client: TestClient, auth: dict[str, str], recording: ArtifactRow, meeting_id: str) -> str:
    return path_of(url_of(client, auth, meeting_id))


@pytest.mark.parametrize(
    ("header", "start", "end"),
    [
        ("bytes=0-99", 0, 99),
        ("bytes=100-199", 100, 199),
        ("bytes=10000-", 10000, len(CONTENT) - 1),
        ("bytes=-50", len(CONTENT) - 50, len(CONTENT) - 1),
        ("bytes=10200-999999", 10200, len(CONTENT) - 1),  # fin trop grande : ramenée à la fin
        ("bytes=0-0", 0, 0),
    ],
)
def test_a_range_is_served_as_a_partial_content(
    client: TestClient, path: str, header: str, start: int, end: int
) -> None:
    response = fetch(client, path, Range=header)
    assert response.status_code == 206
    assert response.content == CONTENT[start : end + 1]
    assert response.headers["content-range"] == f"bytes {start}-{end}/{len(CONTENT)}"
    assert response.headers["content-length"] == str(end - start + 1)
    assert response.headers["accept-ranges"] == "bytes"


def test_a_download_can_be_resumed_and_reassembled(client: TestClient, path: str) -> None:
    first = fetch(client, path, Range="bytes=0-4999")
    rest = fetch(client, path, Range="bytes=5000-")
    assert first.content + rest.content == CONTENT


@pytest.mark.parametrize("header", [f"bytes={len(CONTENT)}-", "bytes=99999-", "bytes=-0"])
def test_a_range_outside_the_file_is_a_416_with_the_real_size(
    client: TestClient, path: str, header: str
) -> None:
    response = fetch(client, path, Range=header)
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(CONTENT)}"
    assert error_code(response) == "range_not_satisfiable"
    assert response.json()["error"]["size"] == len(CONTENT)


@pytest.mark.parametrize(
    "header",
    ["bytes=0-10,20-30", "bytes=10-5", "items=0-5", "bytes=abc", "bytes=", "n'importe quoi"],
)
def test_an_unreadable_range_is_ignored_and_the_whole_file_is_served(
    client: TestClient, path: str, header: str
) -> None:
    response = fetch(client, path, Range=header)
    assert response.status_code == 200
    assert response.content == CONTENT
    assert "content-range" not in response.headers


def test_if_range_with_the_current_etag_keeps_the_range(
    client: TestClient, path: str, recording: ArtifactRow
) -> None:
    response = fetch(client, path, Range="bytes=0-9", If_Range=f'"{recording.id}"')
    assert response.status_code == 206
    assert response.content == CONTENT[:10]


def test_if_range_with_another_validator_serves_the_whole_file(
    client: TestClient, path: str
) -> None:
    for stale in ('"art_autre"', "Wed, 21 Oct 2015 07:28:00 GMT", 'W/"x"'):
        response = fetch(client, path, Range="bytes=0-9", If_Range=stale)
        assert response.status_code == 200
        assert response.content == CONTENT


def test_head_gives_the_headers_without_the_body(
    client: TestClient, path: str, recording: ArtifactRow
) -> None:
    response = client.head(path)
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == str(len(CONTENT))
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["etag"] == f'"{recording.id}"'


def test_head_follows_the_same_rules_as_get(
    client: TestClient, path: str, clock: FixedClock
) -> None:
    partial = client.head(path, headers={"Range": "bytes=0-9"})
    assert partial.status_code == 206
    assert partial.headers["content-range"] == f"bytes 0-9/{len(CONTENT)}"
    assert partial.headers["content-length"] == "10"
    assert client.head(path, headers={"Range": "bytes=99999-"}).status_code == 416
    assert client.head("/v1/downloads/abc").status_code == 404
    clock.advance(DOWNLOAD_URL_TTL)
    assert client.head(path).status_code == 410


# --- états inattendus du téléchargement ------------------------------------------------------


def test_an_artifact_that_is_no_longer_ready_is_not_served(
    client: TestClient, db: Session, path: str, recording: ArtifactRow
) -> None:
    recording.status = ArtifactStatus.PENDING
    db.commit()
    response = client.get(path)
    assert response.status_code == 409
    assert error_code(response) == "artifact_not_ready"


def test_a_ready_row_whose_file_is_gone_is_a_404_not_a_crash(
    client: TestClient, path: str, storage: FakeStorage, recording: ArtifactRow
) -> None:
    assert recording.storage_key is not None
    del storage.objects[recording.storage_key]
    response = client.get(path)
    assert response.status_code == 404
    assert error_code(response) == "artifact_file_missing"


def test_a_file_that_vanishes_after_the_checks_is_a_clean_404(
    client: TestClient, path: str, storage: FakeStorage, recording: ArtifactRow
) -> None:
    """Suppression entre la vérification de la taille et l'ouverture du flux (course)."""
    original = storage.open_range

    def vanish(key: str, start: int, end: int) -> Any:
        storage.objects.pop(key, None)
        return original(key, start, end)

    storage.open_range = vanish  # type: ignore[method-assign]
    response = client.get(path)
    assert response.status_code == 404
    assert error_code(response) == "artifact_file_missing"


def test_s3_down_is_a_502_and_the_url_works_again_afterwards(
    client: TestClient, path: str, storage: FakeStorage
) -> None:
    storage.unreachable = True
    response = client.get(path)
    assert response.status_code == 502
    assert error_code(response) == "storage_unavailable"
    storage.unreachable = False
    assert client.get(path).content == CONTENT


def test_production_refuses_plain_http_for_downloads(
    set_env: Callable[[dict[str, str]], None], tmp_path: Any
) -> None:
    db_url = f"sqlite:///{tmp_path / 'prod.db'}"
    set_env({**valid_env("production"), "DATABASE_URL": db_url})
    upgrade(db_url)
    app = create_app(settings=Settings(), storage=FakeStorage(), run_workers=False)  # type: ignore[call-arg]
    with TestClient(app) as http_client:
        refused = http_client.get("/v1/downloads/abc")
        assert refused.status_code == 403
        assert error_code(refused) == "https_required"
        proxied = http_client.get("/v1/downloads/abc", headers={"X-Forwarded-Proto": "https"})
        assert proxied.status_code == 404  # on est passé jusqu'à la vérification du jeton
        refused_delete = http_client.delete("/v1/recordings/art_x")
        assert refused_delete.status_code in (401, 403)


# --- suppression (scénario 8) ----------------------------------------------------------------


def test_deleting_needs_the_api_key(client: TestClient, recording: ArtifactRow) -> None:
    assert client.delete(f"/v1/recordings/{recording.id}").status_code == 401
    bad = {"Authorization": "Bearer pas-la-bonne-cle"}
    assert client.delete(f"/v1/recordings/{recording.id}", headers=bad).status_code == 401


def test_scenario_8_an_issued_url_stops_working_the_moment_the_recording_is_deleted(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    storage: FakeStorage,
    recording: ArtifactRow,
    meeting_id: str,
) -> None:
    key, recording_id = recording.storage_key, recording.id
    assert key is not None
    path = path_of(url_of(client, auth, meeting_id))
    assert client.get(path).content == CONTENT  # l'URL marche...

    deleted = client.delete(f"/v1/recordings/{recording_id}", headers=auth)

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert key not in storage.objects  # ... l'objet n'est plus sur le S3 ...
    db.expire_all()
    assert db.get(ArtifactRow, recording_id) is None  # ... ni ses métadonnées en base ...
    gone = client.get(path)  # ... et l'URL déjà émise, encore dans ses 15 minutes, ne marche plus
    assert gone.status_code == 410
    assert error_code(gone) == "artifact_deleted"
    assert client.head(path).status_code == 410
    assert fetch(client, path, Range="bytes=0-9").status_code == 410
    assert listing(client, auth, meeting_id) == []
    assert client.get(f"/v1/recordings/{recording_id}", headers=auth).status_code == 404


def test_deleting_twice_or_an_unknown_recording_is_a_clean_404(
    client: TestClient, auth: dict[str, str], recording: ArtifactRow
) -> None:
    assert client.delete(f"/v1/recordings/{recording.id}", headers=auth).status_code == 204
    again = client.delete(f"/v1/recordings/{recording.id}", headers=auth)
    assert again.status_code == 404
    assert error_code(again) == "artifact_not_found"
    unknown = client.delete("/v1/recordings/art_inconnu", headers=auth)
    assert unknown.status_code == 404
    assert unknown.json() == {
        "error": {"code": "artifact_not_found", "message": "aucun artefact avec cet identifiant"}
    }


def test_a_pending_recording_cannot_be_deleted_and_nothing_is_touched(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    storage: FakeStorage,
    meeting_id: str,
) -> None:
    """Egress (ou la fusion des segments) écrit encore : supprimer laisserait un orphelin."""
    pending = add_artifact(db, storage, meeting_id, status=ArtifactStatus.PENDING)
    response = client.delete(f"/v1/recordings/{pending.id}", headers=auth)
    assert response.status_code == 409
    assert error_code(response) == "artifact_not_ready"
    assert pending.storage_key in storage.objects
    db.expire_all()
    assert db.get(ArtifactRow, pending.id) is not None


def test_a_failed_recording_can_be_deleted_with_its_leftovers(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    storage: FakeStorage,
    meeting_id: str,
) -> None:
    failed = add_artifact(
        db, storage, meeting_id, status=ArtifactStatus.FAILED, content=None, error="échec"
    )
    assert failed.storage_key is not None
    failed_id = failed.id
    stem = failed.storage_key.removesuffix(".mp4")
    storage.objects[f"{stem}/seg_00000.ts"] = b"segment"  # segments d'une fusion abandonnée
    storage.objects[f"{stem}/seg_00001.ts"] = b"segment"
    assert client.delete(f"/v1/recordings/{failed_id}", headers=auth).status_code == 204
    assert not [key for key in storage.objects if key.startswith(stem)]
    db.expire_all()
    assert db.get(ArtifactRow, failed_id) is None


def test_deleting_removes_everything_that_shares_the_opaque_path_and_nothing_else(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    storage: FakeStorage,
    create: Create,
) -> None:
    mine, other_meeting = str(create()["id"]), str(create()["id"])
    target = add_artifact(db, storage, mine)
    sibling = add_artifact(db, storage, mine)  # un autre enregistrement de la même réunion
    foreign = add_artifact(db, storage, other_meeting)
    assert target.storage_key and sibling.storage_key and foreign.storage_key
    stem = target.storage_key.removesuffix(".mp4")
    storage.objects[f"{stem}.mp4.json"] = b"{}"  # manifeste d'Egress
    storage.objects[f"{stem}/seg_00003.ts"] = b"segment"  # segment non fusionné

    assert client.delete(f"/v1/recordings/{target.id}", headers=auth).status_code == 204

    assert not [key for key in storage.objects if key.startswith(stem)]
    assert sibling.storage_key in storage.objects
    assert foreign.storage_key in storage.objects
    assert [item["id"] for item in listing(client, auth, mine)] == [sibling.id]
    assert [item["id"] for item in listing(client, auth, other_meeting)] == [foreign.id]


def test_a_recording_id_never_deletes_a_transcript(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    storage: FakeStorage,
    meeting_id: str,
) -> None:
    transcript = add_artifact(
        db, storage, meeting_id, kind=ArtifactKind.TRANSCRIPT, extension="json", content=b"{}"
    )
    assert client.delete(f"/v1/recordings/{transcript.id}", headers=auth).status_code == 404
    assert transcript.storage_key in storage.objects


def test_deleting_when_the_s3_is_down_keeps_the_row_and_can_be_replayed(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    storage: FakeStorage,
    recording: ArtifactRow,
) -> None:
    storage.unreachable = True
    failed = client.delete(f"/v1/recordings/{recording.id}", headers=auth)
    assert failed.status_code == 502
    assert error_code(failed) == "storage_unavailable"
    db.expire_all()
    assert db.get(ArtifactRow, recording.id) is not None  # rien de perdu : ni fichier orphelin
    storage.unreachable = False
    assert client.delete(f"/v1/recordings/{recording.id}", headers=auth).status_code == 204
    assert recording.storage_key not in storage.objects


def test_deleting_a_row_whose_file_is_already_gone_succeeds(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    storage: FakeStorage,
    recording: ArtifactRow,
) -> None:
    """Le S3 répond « ok » pour un objet absent : la base est nettoyée, l'appel est rejouable."""
    assert recording.storage_key is not None
    recording_id = recording.id
    del storage.objects[recording.storage_key]
    assert client.delete(f"/v1/recordings/{recording_id}", headers=auth).status_code == 204
    db.expire_all()
    assert db.get(ArtifactRow, recording_id) is None


class _ForgetfulStorage(FakeStorage):
    """Un S3 qui répond « supprimé » sans rien supprimer (ex. verrou d'objet)."""

    def purge(self, prefix: str) -> int:
        return 0


def test_a_deletion_the_s3_does_not_confirm_keeps_the_row(
    settings: Settings, clock: FixedClock, db: Session, create: Create, tmp_path: Any
) -> None:
    store = _ForgetfulStorage()
    app = create_app(settings=settings, clock=clock, storage=store, run_workers=False)
    with TestClient(app) as http_client:
        auth = {"Authorization": f"Bearer {valid_env()['API_KEY']}"}
        created = http_client.post(
            "/v1/meetings",
            json={
                "title": "x",
                "starts_at": "2027-01-02T16:00:00+01:00",
                "ends_at": "2027-01-02T18:00:00+01:00",
                "organizers": [{"display_name": "Marie Durand"}],
            },
            headers=auth,
        ).json()
        row = add_artifact(db, store, created["id"])
        response = http_client.delete(f"/v1/recordings/{row.id}", headers=auth)
        assert response.status_code == 502
        assert error_code(response) == "storage_unavailable"
        db.expire_all()
        assert db.get(ArtifactRow, row.id) is not None
        assert row.storage_key in store.objects


def test_the_deletion_leaves_an_audit_trail_without_any_secret(
    client: TestClient,
    auth: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    recording: ArtifactRow,
    meeting_id: str,
) -> None:
    caplog.set_level(logging.INFO, logger="visio.artifacts")
    url = url_of(client, auth, meeting_id)
    client.delete(f"/v1/recordings/{recording.id}", headers=auth)
    (record,) = [r for r in caplog.records if r.getMessage() == "artefact supprimé"]
    assert record.__dict__["artifact"] == recording.id
    assert record.__dict__["meeting_id"] == meeting_id
    assert record.__dict__["objects"] == 1
    assert recording.storage_key is not None
    assert recording.storage_key not in caplog.text
    assert url.rsplit("/", 1)[1] not in caplog.text


def test_a_download_is_logged_without_the_token(
    client: TestClient,
    auth: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    recording: ArtifactRow,
    meeting_id: str,
) -> None:
    caplog.set_level(logging.INFO, logger="visio.artifacts")
    url = url_of(client, auth, meeting_id)
    client.get(path_of(url))
    assert any(r.__dict__.get("artifact") == recording.id for r in caplog.records)
    assert url.rsplit("/", 1)[1] not in caplog.text


# --- purge_prefix ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (
            "meetings/mtg_1/recordings/0b2f6a3e-1c5d-4e9a-8f7b-2d3c4e5f6a7b.mp4",
            "meetings/mtg_1/recordings/0b2f6a3e-1c5d-4e9a-8f7b-2d3c4e5f6a7b",
        ),
        (
            "meetings/mtg_1/transcripts/0b2f6a3e-1c5d-4e9a-8f7b-2d3c4e5f6a7b.json",
            "meetings/mtg_1/transcripts/0b2f6a3e-1c5d-4e9a-8f7b-2d3c4e5f6a7b",
        ),
        # Clé inattendue (pas un UUID) : on ne supprime que la clé exacte, jamais un préfixe large.
        ("meetings/mtg_1/recordings/weird.mp4", "meetings/mtg_1/recordings/weird.mp4"),
        ("meetings/mtg_1/recordings/a.mp4", "meetings/mtg_1/recordings/a.mp4"),
        ("fichier-sans-dossier.mp4", "fichier-sans-dossier.mp4"),
        ("meetings/mtg_1/recordings/", "meetings/mtg_1/recordings/"),
    ],
)
def test_purge_prefix_is_never_broader_than_one_artifact(key: str, expected: str) -> None:
    assert artifacts_service.purge_prefix(key) == expected


def test_a_weird_key_only_deletes_itself(
    client: TestClient, auth: dict[str, str], db: Session, storage: FakeStorage, meeting_id: str
) -> None:
    row = add_artifact(db, storage, meeting_id)
    row.storage_key = f"meetings/{meeting_id}/recordings/weird.mp4"
    db.commit()
    storage.objects[row.storage_key] = b"x"
    neighbour = f"meetings/{meeting_id}/recordings/weird-2.mp4"
    storage.objects[neighbour] = b"x"
    assert client.delete(f"/v1/recordings/{row.id}", headers=auth).status_code == 204
    assert row.storage_key not in storage.objects
    assert neighbour in storage.objects


# --- OpenAPI ---------------------------------------------------------------------------------


def test_openapi_documents_the_artifact_routes(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    validate(spec)
    paths = spec["paths"]
    listing_route = paths["/v1/meetings/{meeting_id}/recordings"]["get"]
    one = paths["/v1/recordings/{recording_id}"]
    download_route = paths["/v1/downloads/{token}"]["get"]
    assert set(one) == {"get", "delete"}
    assert set(paths["/v1/downloads/{token}"]) == {"get"}  # HEAD existe mais n'est pas documenté
    for route in (listing_route, one["get"], one["delete"]):
        assert route["security"] == [{"ApiKey": []}]
    assert "security" not in download_route  # le jeton de l'URL fait foi, pas la clé d'API
    assert {"404"} <= set(listing_route["responses"])
    assert {"204", "404", "409", "502"} <= set(one["delete"]["responses"])
    assert {"200", "206", "404", "410", "416", "502"} <= set(download_route["responses"])
    schema = spec["components"]["schemas"]["RecordingOut"]["properties"]
    assert {"id", "status", "size_bytes", "download_url", "download_url_expires_at"} <= set(schema)


def test_the_app_can_hold_any_object_store(settings: Settings, clock: FixedClock) -> None:
    store: ObjectStore = FakeStorage()  # mypy : FakeStorage respecte bien le protocole
    app = create_app(settings=settings, clock=clock, storage=store, run_workers=False)
    with TestClient(app) as http_client:
        assert http_client.get("/health").status_code == 200
