"""Étape 16a : transcription (stockage S3, lecture JSON/txt/SRT/VTT, suppression), sans Docker.

Le moteur de reconnaissance vocale (étape 16c) n'est pas là : on fournit des segments déjà
reconnus, comme le worker le fera, et on vérifie tout ce qui les entoure.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openapi_spec_validator import validate
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.integration.helpers import error_code
from visio.config import Settings
from visio.db.models import ArtifactKind, ArtifactRow, ArtifactStatus
from visio.domain import transcript as fmt
from visio.domain.errors import StorageUnavailableError
from visio.domain.transcript import Segment
from visio.services import transcripts as service
from visio.services.storage import FakeStorage

Create = Callable[..., dict[str, Any]]

MARIE = [Segment(0.0, 4.0, "Marie Durand", "staff_42", "Bonjour à tous.")]
PAUL = [Segment(4.5, 9.0, "Paul Martin", "stu_7", "Bonjour Marie.")]
STARTED_AT = "2027-01-02T16:00:00+01:00"


@pytest.fixture
def meeting_id(create: Create) -> str:
    return str(create()["id"])


def produce(
    db: Session,
    storage: FakeStorage,
    meeting_id: str,
    segments: list[Segment] | None = None,
) -> ArtifactRow:
    row = service.begin(db, meeting_id)
    merged = fmt.merge([MARIE, PAUL] if segments is None else [segments])
    return service.complete(db, storage, row, merged, started_at=STARTED_AT)


def url(meeting_id: str) -> str:
    return f"/v1/meetings/{meeting_id}/transcript"


def rows(db: Session, meeting_id: str) -> list[ArtifactRow]:
    db.expire_all()
    return list(
        db.scalars(
            select(ArtifactRow).where(
                ArtifactRow.meeting_id == meeting_id, ArtifactRow.kind == ArtifactKind.TRANSCRIPT
            )
        )
    )


# --- sécurité et erreurs -----------------------------------------------------------------------


def test_the_routes_need_the_api_key(client: TestClient, meeting_id: str) -> None:
    assert client.get(url(meeting_id)).status_code == 401
    assert client.delete(url(meeting_id)).status_code == 401
    bad = {"Authorization": "Bearer pas-la-bonne-cle"}
    assert client.get(url(meeting_id), headers=bad).status_code == 401


def test_an_unknown_meeting_is_a_clean_404(client: TestClient, auth: dict[str, str]) -> None:
    for call in (client.get, client.delete):
        response = call("/v1/meetings/mtg_inconnue/transcript", headers=auth)
        assert response.status_code == 404
        assert error_code(response) == "meeting_not_found"


def test_a_meeting_without_transcript_is_a_clean_404(
    client: TestClient, auth: dict[str, str], meeting_id: str
) -> None:
    for call in (client.get, client.delete):
        response = call(url(meeting_id), headers=auth)
        assert response.status_code == 404
        assert error_code(response) == "artifact_not_found"


# --- lecture ---------------------------------------------------------------------------------


def test_the_json_transcript_has_timestamped_segments_per_speaker(
    client: TestClient, auth: dict[str, str], db: Session, storage: FakeStorage, meeting_id: str
) -> None:
    produce(db, storage, meeting_id)
    body = client.get(url(meeting_id), headers=auth).json()
    assert body["status"] == "ready"
    assert body["language"] == "fr"
    assert body["started_at"] == STARTED_AT
    assert body["duration_seconds"] == 9.0
    assert body["error"] is None
    assert body["speakers"] == [
        {"display_name": "Marie Durand", "external_id": "staff_42"},
        {"display_name": "Paul Martin", "external_id": "stu_7"},
    ]
    assert body["segments"] == [
        {
            "start": 0.0,
            "end": 4.0,
            "speaker": "Marie Durand",
            "external_id": "staff_42",
            "text": "Bonjour à tous.",
        },
        {
            "start": 4.5,
            "end": 9.0,
            "speaker": "Paul Martin",
            "external_id": "stu_7",
            "text": "Bonjour Marie.",
        },
    ]


@pytest.mark.parametrize(
    ("fmt_name", "media_type", "expected"),
    [
        ("txt", "text/plain", "[00:00:04] Paul Martin : Bonjour Marie."),
        ("srt", "application/x-subrip", "00:00:04,500 --> 00:00:09,000\nPaul Martin : Bonjour"),
        ("vtt", "text/vtt", "00:00:04.500 --> 00:00:09.000\n<v Paul Martin>Bonjour Marie."),
    ],
)
def test_exports_in_text_srt_and_vtt(
    client: TestClient,
    auth: dict[str, str],
    db: Session,
    storage: FakeStorage,
    meeting_id: str,
    fmt_name: str,
    media_type: str,
    expected: str,
) -> None:
    produce(db, storage, meeting_id)
    response = client.get(url(meeting_id), params={"format": fmt_name}, headers=auth)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media_type)
    assert response.headers["cache-control"] == "private, no-store"
    assert f".{fmt_name}" in response.headers["content-disposition"]
    assert expected in response.text
    assert "Marie Durand" in response.text


def test_an_unknown_format_is_refused(
    client: TestClient, auth: dict[str, str], db: Session, storage: FakeStorage, meeting_id: str
) -> None:
    produce(db, storage, meeting_id)
    response = client.get(url(meeting_id), params={"format": "pdf"}, headers=auth)
    assert response.status_code == 422


def test_a_meeting_without_speech_gives_an_empty_ready_transcript(
    client: TestClient, auth: dict[str, str], db: Session, storage: FakeStorage, meeting_id: str
) -> None:
    produce(db, storage, meeting_id, segments=[])
    body = client.get(url(meeting_id), headers=auth).json()
    assert body["status"] == "ready"
    assert body["segments"] == []
    assert body["speakers"] == []
    assert body["duration_seconds"] == 0.0
    assert client.get(url(meeting_id), params={"format": "txt"}, headers=auth).text == ""
    assert client.get(url(meeting_id), params={"format": "srt"}, headers=auth).text == ""
    vtt = client.get(url(meeting_id), params={"format": "vtt"}, headers=auth)
    assert vtt.text == "WEBVTT\n"


def test_pending_and_failed_states_are_visible_without_segments(
    client: TestClient, auth: dict[str, str], db: Session, meeting_id: str
) -> None:
    row = service.begin(db, meeting_id)
    pending = client.get(url(meeting_id), headers=auth).json()
    assert pending["status"] == "pending"
    assert pending["segments"] == []
    assert pending["language"] is None
    service.fail(db, row, "modèle de transcription introuvable")
    failed = client.get(url(meeting_id), headers=auth).json()
    assert failed["status"] == "failed"
    assert failed["error"] == "modèle de transcription introuvable"
    assert failed["segments"] == []


@pytest.mark.parametrize("fmt_name", ["txt", "srt", "vtt"])
def test_an_export_of_an_unfinished_transcript_is_a_409(
    client: TestClient, auth: dict[str, str], db: Session, meeting_id: str, fmt_name: str
) -> None:
    service.begin(db, meeting_id)
    response = client.get(url(meeting_id), params={"format": fmt_name}, headers=auth)
    assert response.status_code == 409
    assert error_code(response) == "artifact_not_ready"


def test_a_missing_file_is_a_404_and_a_dead_s3_a_502(
    client: TestClient, auth: dict[str, str], db: Session, storage: FakeStorage, meeting_id: str
) -> None:
    row = produce(db, storage, meeting_id)
    storage.unreachable = True
    response = client.get(url(meeting_id), headers=auth)
    assert response.status_code == 502
    assert error_code(response) == "storage_unavailable"
    storage.unreachable = False
    del storage.objects[service.key_for(row, "json")]
    response = client.get(url(meeting_id), headers=auth)
    assert response.status_code == 404
    assert error_code(response) == "artifact_file_missing"


def test_nothing_of_the_s3_leaks_in_the_responses(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    db: Session,
    storage: FakeStorage,
    meeting_id: str,
) -> None:
    row = produce(db, storage, meeting_id)
    text = client.get(url(meeting_id), headers=auth).text
    assert row.storage_key is not None
    for secret in (row.storage_key, "meetings/", settings.s3_endpoint, settings.s3_bucket):
        assert secret not in text


# --- stockage --------------------------------------------------------------------------------


def test_four_files_share_one_opaque_name_under_the_meeting(
    db: Session, storage: FakeStorage, meeting_id: str
) -> None:
    row = produce(db, storage, meeting_id)
    assert row.status is ArtifactStatus.READY
    assert row.storage_key is not None
    assert row.storage_key.startswith(f"meetings/{meeting_id}/transcripts/")
    stem = row.storage_key.removesuffix(".json")
    assert set(storage.objects) == {f"{stem}.{e}" for e in ("json", "txt", "srt", "vtt")}
    assert row.size_bytes == len(storage.objects[row.storage_key])


def test_beginning_twice_reuses_the_same_row_and_files_never_duplicate(
    db: Session, storage: FakeStorage, meeting_id: str
) -> None:
    """Worker coupé puis relancé : une seule ligne, les mêmes fichiers réécrits, pas de doublon."""
    first = produce(db, storage, meeting_id)
    key, count = first.storage_key, len(storage.objects)
    again = produce(db, storage, meeting_id)
    assert again.id == first.id
    assert again.storage_key == key
    assert len(rows(db, meeting_id)) == 1
    assert len(storage.objects) == count


def test_an_interrupted_run_leaves_no_ready_transcript(
    db: Session, storage: FakeStorage, meeting_id: str
) -> None:
    row = service.begin(db, meeting_id)
    storage.unreachable = True
    with pytest.raises(StorageUnavailableError):
        service.complete(db, storage, row, fmt.merge([MARIE]))
    storage.unreachable = False
    assert rows(db, meeting_id)[0].status is ArtifactStatus.PENDING
    assert storage.objects == {}


# --- suppression (scénario 8, transcription) ---------------------------------------------------


def test_deleting_removes_the_four_files_and_the_row(
    client: TestClient, auth: dict[str, str], db: Session, storage: FakeStorage, meeting_id: str
) -> None:
    produce(db, storage, meeting_id)
    other = f"meetings/{meeting_id}/recordings/00000000-0000-0000-0000-000000000000.mp4"
    storage.objects[other] = b"video"
    assert client.delete(url(meeting_id), headers=auth).status_code == 204
    assert storage.objects == {other: b"video"}  # l'enregistrement n'est pas touché
    assert rows(db, meeting_id) == []
    assert client.get(url(meeting_id), headers=auth).status_code == 404
    assert client.delete(url(meeting_id), headers=auth).status_code == 404


def test_deleting_an_unfinished_transcript_is_refused(
    client: TestClient, auth: dict[str, str], db: Session, meeting_id: str
) -> None:
    service.begin(db, meeting_id)
    response = client.delete(url(meeting_id), headers=auth)
    assert response.status_code == 409
    assert error_code(response) == "artifact_not_ready"
    assert len(rows(db, meeting_id)) == 1


def test_a_failed_transcript_can_be_deleted(
    client: TestClient, auth: dict[str, str], db: Session, meeting_id: str
) -> None:
    service.fail(db, service.begin(db, meeting_id), "échec")
    assert client.delete(url(meeting_id), headers=auth).status_code == 204
    assert rows(db, meeting_id) == []


def test_a_dead_s3_keeps_the_row_so_the_call_can_be_replayed(
    client: TestClient, auth: dict[str, str], db: Session, storage: FakeStorage, meeting_id: str
) -> None:
    produce(db, storage, meeting_id)
    storage.unreachable = True
    response = client.delete(url(meeting_id), headers=auth)
    assert response.status_code == 502
    assert len(rows(db, meeting_id)) == 1
    storage.unreachable = False
    assert client.delete(url(meeting_id), headers=auth).status_code == 204
    assert storage.objects == {}


def test_deleting_the_transcript_leaves_the_recordings_alone(
    client: TestClient, auth: dict[str, str], db: Session, storage: FakeStorage, meeting_id: str
) -> None:
    recording = ArtifactRow(
        meeting_id=meeting_id,
        kind=ArtifactKind.RECORDING,
        status=ArtifactStatus.READY,
        storage_key=f"meetings/{meeting_id}/recordings/11111111-1111-1111-1111-111111111111.mp4",
    )
    db.add(recording)
    db.commit()
    produce(db, storage, meeting_id)
    assert client.delete(url(meeting_id), headers=auth).status_code == 204
    recordings = client.get(f"/v1/meetings/{meeting_id}/recordings", headers=auth).json()
    assert [r["id"] for r in recordings["recordings"]] == [recording.id]


# --- OpenAPI ---------------------------------------------------------------------------------


def test_the_openapi_document_describes_the_transcript_routes(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    validate(spec)
    path = spec["paths"]["/v1/meetings/{meeting_id}/transcript"]
    assert set(path) == {"get", "delete"}
    assert "TranscriptOut" in spec["components"]["schemas"]
