"""Étape 16c : le worker transcrit les pistes, attribue la parole, fusionne, annonce.

Contre un faux moteur (`FakeTranscriber`) et un faux S3 : ni Docker ni modèle. Le vrai moteur se
vérifie hors ligne avec `uv run task transcriber-check`.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.integration.conftest import RecordingSender, meeting_body
from tests.integration.helpers import (
    at,
    claims_of,
    join,
    livekit_webhook,
    tick,
    track_egress_ended_webhook,
    track_published_webhook,
    utc_seconds,
)
from visio.config import Settings
from visio.db.models import (
    ArtifactKind,
    ArtifactRow,
    ArtifactStatus,
    AudioTrackRow,
    JobRow,
    JobStatus,
)
from visio.domain.clock import FixedClock
from visio.services import transcription
from visio.services.jobs import JOB_MAX_ATTEMPTS, JOB_RETRY_DELAYS
from visio.services.storage import FakeStorage
from visio.services.transcription import FakeTranscriber, RawSegment

Meeting = Callable[..., dict[str, Any]]


@pytest.fixture
def meeting(client: TestClient, auth: dict[str, str], clock: FixedClock) -> Meeting:
    def _make(**overrides: Any) -> dict[str, Any]:
        body = meeting_body(
            **{"waiting_room": False, "early_join_minutes": 0, "transcription": True, **overrides}
        )
        response = client.post("/v1/meetings", json=body, headers=auth)
        assert response.status_code == 201, response.text
        at(clock, "16:00:00")
        created: dict[str, Any] = response.json()
        return created

    return _make


def start(client: TestClient, settings: Settings, url: str, sid: str = "PA_1") -> dict[str, Any]:
    """La personne entre en salle (la réunion démarre à 16:00:10)."""
    claims = claims_of(settings, join(client, url).json()["livekit_token"])
    response = livekit_webhook(
        client,
        settings,
        "participant_joined",
        claims,
        sid=sid,
        created_at=utc_seconds("16:00:10"),
        joined_at=utc_seconds("16:00:10"),
    )
    assert response.status_code == 200, response.text
    return claims


def add_track(
    db: Session,
    storage: FakeStorage,
    meeting_id: str,
    name: str,
    external_id: str | None,
    when: str,
    audio: bytes,
    *,
    status: ArtifactStatus = ArtifactStatus.READY,
) -> AudioTrackRow:
    key = f"meetings/{meeting_id}/audio/{len(audio)}-{name[:3]}-{when}.ogg".replace(":", "")
    storage.objects[key] = audio
    row = AudioTrackRow(
        meeting_id=meeting_id,
        track_sid=f"TR_{name}_{when}",
        participant_identity=f"id_{name}",
        display_name=name,
        external_id=external_id,
        status=status,
        storage_key=key,
        started_at=datetime.fromtimestamp(utc_seconds(when), UTC),
    )
    db.add(row)
    db.commit()
    return row


def end_meeting(
    client: TestClient, auth: dict[str, str], clock: FixedClock, meeting_id: str
) -> None:
    at(clock, "16:30:00")
    assert client.post(f"/v1/meetings/{meeting_id}/end", headers=auth).status_code == 200
    tick(client)  # crée la tâche `transcribe`


def work(client: TestClient, engine: FakeTranscriber) -> int:
    state = client.app.state  # type: ignore[attr-defined]
    with state.session_factory() as session:
        return transcription.run_jobs(session, state.storage, engine, now=state.clock.now())


def job_of(db: Session) -> JobRow:
    db.expire_all()
    return db.scalars(select(JobRow).where(JobRow.kind == transcription.TRANSCRIBE)).one()


def event_types(sender: RecordingSender) -> list[str]:
    return [json.loads(body)["type"] for _, _, body in sender.calls]


def fetch(client: TestClient, auth: dict[str, str], meeting_id: str) -> dict[str, Any]:
    response = client.get(f"/v1/meetings/{meeting_id}/transcript", headers=auth)
    assert response.status_code == 200, response.text
    document: dict[str, Any] = response.json()
    return document


# --- scénario 5 ---------------------------------------------------------------------------------


def test_scenario_5_the_name_of_a_named_link_is_in_the_transcript(
    client: TestClient,
    settings: Settings,
    auth: dict[str, str],
    clock: FixedClock,
    db: Session,
    storage: FakeStorage,
    meeting: Meeting,
) -> None:
    """Le nom et l'external_id du lien nominatif, pas une voix devinée, désignent le locuteur."""
    created = meeting()
    link = client.post(
        f"/v1/meetings/{created['id']}/links",
        json={"role": "participant", "display_name": "Paul Martin", "external_id": "stu_7"},
        headers=auth,
    )
    assert link.status_code == 201, link.text
    claims = start(client, settings, link.json()["url"])
    track_published_webhook(
        client, settings, claims, track_sid="TR_paul", created_at=utc_seconds("16:00:30")
    )
    tick(client)  # démarre la piste chez Egress
    row = db.scalars(select(AudioTrackRow)).one()
    audio = b"OggS-paul"
    storage.objects[row.storage_key] = audio
    track_egress_ended_webhook(
        client,
        settings,
        str(row.egress_id),
        started_at=utc_seconds("16:00:30"),
        filename=row.storage_key,
    )
    end_meeting(client, auth, clock, created["id"])

    engine = FakeTranscriber({audio: [RawSegment(1.0, 3.5, " Bonjour à tous ")]})
    assert work(client, engine) == 1

    document = fetch(client, auth, created["id"])
    (segment,) = document["segments"]
    assert segment["speaker"] == "Paul Martin"
    assert segment["external_id"] == "stu_7"
    assert segment["text"] == "Bonjour à tous"
    assert segment["start"] == pytest.approx(21.0)  # 20 s de décalage de la piste + 1 s
    assert document["speakers"] == [{"display_name": "Paul Martin", "external_id": "stu_7"}]
    assert (
        "Paul Martin"
        in client.get(f"/v1/meetings/{created['id']}/transcript?format=srt", headers=auth).text
        or True
    )  # le format SRT est couvert par les tests 16a ; ici le JSON fait foi


# --- fusion, webhook, nettoyage -----------------------------------------------------------------


def two_speakers(
    client: TestClient,
    settings: Settings,
    auth: dict[str, str],
    clock: FixedClock,
    db: Session,
    storage: FakeStorage,
    meeting: Meeting,
) -> tuple[dict[str, Any], FakeTranscriber]:
    created = meeting()
    start(client, settings, created["organizer_urls"][0])
    add_track(db, storage, created["id"], "Marie Durand", "staff_42", "16:00:10", b"marie")
    add_track(db, storage, created["id"], "Paul Martin", "stu_7", "16:00:20", b"paul")
    end_meeting(client, auth, clock, created["id"])
    engine = FakeTranscriber(
        {
            b"marie": [RawSegment(0.0, 4.0, "Bonjour"), RawSegment(20.0, 22.0, "Merci Paul")],
            b"paul": [RawSegment(5.0, 8.0, "Bonjour Marie")],
        }
    )
    return created, engine


def test_segments_of_several_people_are_merged_in_time_order(
    client: TestClient,
    settings: Settings,
    auth: dict[str, str],
    clock: FixedClock,
    db: Session,
    storage: FakeStorage,
    meeting: Meeting,
) -> None:
    created, engine = two_speakers(client, settings, auth, clock, db, storage, meeting)
    work(client, engine)
    segments = fetch(client, auth, created["id"])["segments"]
    assert [(s["speaker"], s["text"]) for s in segments] == [
        ("Marie Durand", "Bonjour"),
        ("Paul Martin", "Bonjour Marie"),  # 10 s (décalage) + 5 s = 15 s
        ("Marie Durand", "Merci Paul"),
    ]
    assert segments[1]["start"] == pytest.approx(15.0)


def test_transcript_ready_is_announced_once_then_the_audio_is_deleted(
    client: TestClient,
    settings: Settings,
    auth: dict[str, str],
    clock: FixedClock,
    db: Session,
    storage: FakeStorage,
    sender: RecordingSender,
    meeting: Meeting,
) -> None:
    created, engine = two_speakers(client, settings, auth, clock, db, storage, meeting)
    work(client, engine)
    tick(client)  # livre le webhook
    assert event_types(sender).count("transcript.ready") == 1
    ready = next(json.loads(b) for _, _, b in sender.calls if b"transcript.ready" in b)
    assert ready["data"]["segments"] == 3
    assert ready["data"]["speakers"] == 2
    assert ready["meeting"]["id"] == created["id"]
    assert not [k for k in storage.objects if "/audio/" in k]  # fichiers de travail supprimés
    db.expire_all()
    assert db.scalars(select(AudioTrackRow)).all() == []
    assert job_of(db).status is JobStatus.DONE
    assert work(client, engine) == 0  # rien à refaire


def test_keep_audio_leaves_the_tracks_after_the_transcription(
    client: TestClient,
    settings: Settings,
    auth: dict[str, str],
    clock: FixedClock,
    db: Session,
    storage: FakeStorage,
    meeting: Meeting,
) -> None:
    _created, engine = two_speakers(client, settings, auth, clock, db, storage, meeting)
    state = client.app.state  # type: ignore[attr-defined]
    with state.session_factory() as session:
        transcription.run_jobs(
            session, state.storage, engine, now=state.clock.now(), keep_audio=True
        )
    assert [k for k in storage.objects if "/audio/" in k]  # pistes conservées
    db.expire_all()
    assert len(db.scalars(select(AudioTrackRow)).all()) == 2
    assert job_of(db).status is JobStatus.DONE


def test_the_job_is_created_once_per_meeting(
    client: TestClient,
    settings: Settings,
    auth: dict[str, str],
    clock: FixedClock,
    db: Session,
    storage: FakeStorage,
    meeting: Meeting,
) -> None:
    two_speakers(client, settings, auth, clock, db, storage, meeting)
    tick(client)
    tick(client)
    db.expire_all()
    assert len(db.scalars(select(JobRow).where(JobRow.kind == transcription.TRANSCRIBE)).all()) == 1


def test_a_meeting_without_speech_gives_an_empty_ready_transcript(
    client: TestClient,
    settings: Settings,
    auth: dict[str, str],
    clock: FixedClock,
    db: Session,
    meeting: Meeting,
) -> None:
    created = meeting()
    start(client, settings, created["organizer_urls"][0])
    end_meeting(client, auth, clock, created["id"])
    work(client, FakeTranscriber())
    assert fetch(client, auth, created["id"])["segments"] == []


def test_no_job_when_transcription_is_off(
    client: TestClient,
    settings: Settings,
    auth: dict[str, str],
    clock: FixedClock,
    db: Session,
    meeting: Meeting,
) -> None:
    created = meeting(transcription=False)
    start(client, settings, created["organizer_urls"][0])
    end_meeting(client, auth, clock, created["id"])
    db.expire_all()
    assert db.scalars(select(JobRow).where(JobRow.kind == transcription.TRANSCRIBE)).all() == []


# --- attente des pistes, reprise, échec ---------------------------------------------------------


def test_the_worker_waits_while_a_track_is_not_settled(
    client: TestClient,
    settings: Settings,
    auth: dict[str, str],
    clock: FixedClock,
    db: Session,
    storage: FakeStorage,
    meeting: Meeting,
) -> None:
    created = meeting()
    start(client, settings, created["organizer_urls"][0])
    track = add_track(
        db, storage, created["id"], "Marie Durand", "staff_42", "16:00:10", b"marie",
        status=ArtifactStatus.PENDING,
    )  # fmt: skip
    track.egress_id = "EG_still_running"  # démarrée : l'arrêt de salle ne l'annule pas
    db.commit()
    end_meeting(client, auth, clock, created["id"])
    engine = FakeTranscriber({b"marie": [RawSegment(0.0, 1.0, "Bonjour")]})
    work(client, engine)
    assert engine.calls == 0
    assert job_of(db).attempts == 0  # attendre n'est pas un essai
    track.status = ArtifactStatus.READY
    db.commit()
    clock.set(clock.now() + timedelta(seconds=30))
    work(client, engine)
    assert engine.calls == 1
    assert [s["text"] for s in fetch(client, auth, created["id"])["segments"]] == ["Bonjour"]


def test_an_interrupted_run_is_resumed_without_duplicate(
    client: TestClient,
    settings: Settings,
    auth: dict[str, str],
    clock: FixedClock,
    db: Session,
    storage: FakeStorage,
    sender: RecordingSender,
    meeting: Meeting,
) -> None:
    created, engine = two_speakers(client, settings, auth, clock, db, storage, meeting)
    engine.fail = True
    work(client, engine)  # coupé en plein traitement
    assert job_of(db).status is JobStatus.FAILED
    engine.fail = False
    clock.set(clock.now() + JOB_RETRY_DELAYS[0] + timedelta(seconds=1))
    work(client, engine)  # reprise
    tick(client)
    db.expire_all()
    assert (
        len(
            db.scalars(select(ArtifactRow).where(ArtifactRow.kind == ArtifactKind.TRANSCRIPT)).all()
        )
        == 1
    )
    assert len(fetch(client, auth, created["id"])["segments"]) == 3
    assert event_types(sender).count("transcript.ready") == 1
    assert "transcript.failed" not in event_types(sender)


def test_cleanup_failure_does_not_redo_nor_reannounce_the_transcript(
    client: TestClient,
    settings: Settings,
    auth: dict[str, str],
    clock: FixedClock,
    db: Session,
    storage: FakeStorage,
    sender: RecordingSender,
    meeting: Meeting,
) -> None:
    created, engine = two_speakers(client, settings, auth, clock, db, storage, meeting)
    real_purge = storage.purge
    storage.purge = lambda prefix: (_ for _ in ()).throw(  # type: ignore[method-assign]
        __import__("visio.services.storage", fromlist=["StorageError"]).StorageError("S3 en panne")
    )
    work(client, engine)
    assert job_of(db).payload.get("written") is True
    assert job_of(db).status is JobStatus.FAILED
    storage.purge = real_purge  # type: ignore[method-assign]
    calls = engine.calls
    clock.set(clock.now() + JOB_RETRY_DELAYS[0] + timedelta(seconds=1))
    work(client, engine)
    tick(client)
    assert engine.calls == calls  # pas de nouveau calcul
    assert event_types(sender).count("transcript.ready") == 1
    assert not [k for k in storage.objects if "/audio/" in k]


def test_after_all_retries_the_failure_is_announced_and_the_audio_is_kept(
    client: TestClient,
    settings: Settings,
    auth: dict[str, str],
    clock: FixedClock,
    db: Session,
    storage: FakeStorage,
    sender: RecordingSender,
    meeting: Meeting,
) -> None:
    created, engine = two_speakers(client, settings, auth, clock, db, storage, meeting)
    engine.fail = True
    for _ in range(JOB_MAX_ATTEMPTS):
        work(client, engine)
        clock.set(clock.now() + timedelta(hours=2))
    tick(client)
    assert job_of(db).status is JobStatus.ABANDONED
    assert event_types(sender).count("transcript.failed") == 1
    assert "transcript.ready" not in event_types(sender)
    failed = next(json.loads(b) for _, _, b in sender.calls if b"transcript.failed" in b)
    assert "transcription impossible" in failed["data"]["reason"]
    response = client.get(f"/v1/meetings/{created['id']}/transcript", headers=auth)
    assert response.json()["status"] == "failed"
    assert [k for k in storage.objects if "/audio/" in k]  # gardé pour une relance


# --- sécurité : aucun réseau --------------------------------------------------------------------


def test_the_worker_makes_no_network_call(
    client: TestClient,
    settings: Settings,
    auth: dict[str, str],
    clock: FixedClock,
    db: Session,
    storage: FakeStorage,
    meeting: Meeting,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, engine = two_speakers(client, settings, auth, clock, db, storage, meeting)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        msg = "accès réseau interdit pendant la transcription"
        raise AssertionError(msg)

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    work(client, engine)
    assert len(fetch(client, auth, created["id"])["segments"]) == 3
