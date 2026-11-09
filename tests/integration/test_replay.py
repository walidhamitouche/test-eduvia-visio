"""Échecs visibles et rejouables (étape 17c) : `jobs-list`, `jobs-retry`.

Parcours réels : l'échec est produit par le planificateur lui-même (Egress coupé, récepteur de
webhooks en panne), listé, rejoué, puis le planificateur le mène au succès. Aucun doublon.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.integration.conftest import RecordingSender, meeting_body
from tests.integration.helpers import at, claims_of, join, livekit_webhook, tick, utc_seconds
from visio.config import Settings
from visio.db.models import (
    ArtifactKind,
    ArtifactRow,
    ArtifactStatus,
    AudioTrackRow,
    JobRow,
    JobStatus,
    WebhookDeliveryRow,
    WebhookStatus,
)
from visio.domain.clock import FixedClock
from visio.services import replay
from visio.services.egress import FakeRecorder
from visio.services.jobs import JOB_MAX_ATTEMPTS, JOB_RETRY_DELAYS
from visio.services.recording import MERGE_RECORDING, START_RECORDING, run_merge_jobs
from visio.services.replay import Result
from visio.services.transcription import TRANSCRIBE
from visio.services.webhooks import MAX_ATTEMPTS, RETRY_DELAYS, SendResult
from visio.tools import jobslist, jobsretry

Meeting = Callable[..., dict[str, Any]]


@pytest.fixture
def meeting(client: TestClient, auth: dict[str, str], clock: FixedClock) -> Meeting:
    def _make(**overrides: Any) -> dict[str, Any]:
        body = meeting_body(waiting_room=False, early_join_minutes=0, **overrides)
        response = client.post("/v1/meetings", json=body, headers=auth)
        assert response.status_code == 201, response.text
        at(clock, "16:00:00")
        created: dict[str, Any] = response.json()
        return created

    return _make


def connect(client: TestClient, settings: Settings, url: str, when: str = "16:00:10") -> None:
    claims = claims_of(settings, join(client, url).json()["livekit_token"])
    response = livekit_webhook(
        client,
        settings,
        "participant_joined",
        claims,
        created_at=utc_seconds(when),
        joined_at=utc_seconds(when),
    )
    assert response.status_code == 200, response.text


def fresh(db: Session) -> Session:
    db.expire_all()
    return db


def abandon_recording_start(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    recorder: FakeRecorder,
    created: dict[str, Any],
) -> tuple[JobRow, ArtifactRow]:
    """Egress coupé pendant toute la série de relances : tâche abandonnée, artefact en échec."""
    connect(client, settings, created["organizer_urls"][0])
    recorder.unavailable = True
    for attempt in range(JOB_MAX_ATTEMPTS):
        tick(client)
        if attempt < len(JOB_RETRY_DELAYS):
            clock.advance(JOB_RETRY_DELAYS[attempt] + timedelta(seconds=1))
    job = fresh(db).scalars(select(JobRow).where(JobRow.kind == START_RECORDING)).one()
    artifact = db.scalars(select(ArtifactRow)).one()
    assert job.status is JobStatus.ABANDONED
    assert artifact.status is ArtifactStatus.FAILED
    return job, artifact


def event_types(sender: RecordingSender) -> list[str]:
    return [headers["X-Visio-Event-Type"] for _, headers, _ in sender.calls]


# --- 1. une tâche abandonnée : visible, rejouée, menée au succès --------------------------------


def test_an_abandoned_start_is_listed_replayed_and_then_succeeds(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    meeting: Meeting,
    recorder: FakeRecorder,
) -> None:
    created = meeting()
    job, artifact = abandon_recording_start(client, settings, db, clock, recorder, created)
    assert recorder.started == []

    # visible : la tâche, avec son erreur, et l'artefact qu'elle a fait échouer
    failures = {f.identifier: f for f in replay.list_failures(db)}
    assert failures[job.id].status == "abandonnée"
    assert failures[job.id].attempts == JOB_MAX_ATTEMPTS
    assert failures[job.id].error
    assert failures[artifact.id].replay == job.id  # l'artefact renvoie à sa tâche
    assert "démarrage impossible" in (failures[artifact.id].error or "")

    # rejouée : Egress est revenu
    recorder.unavailable = False
    outcome = replay.retry(db, job.id, now=clock.now())
    assert outcome.result is Result.REQUEUED
    fresh(db)
    assert (job.status, job.attempts) == (JobStatus.PENDING, 0)
    assert artifact.status is ArtifactStatus.PENDING  # il n'est plus « en échec »
    assert artifact.error is None

    # succès : le planificateur reprend la tâche au tour suivant
    tick(client)
    fresh(db)
    assert job.status is JobStatus.DONE
    assert len(recorder.started) == 1
    assert artifact.egress_id is not None
    assert replay.list_failures(db) == []


def test_replaying_twice_never_starts_a_second_recording(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    meeting: Meeting,
    recorder: FakeRecorder,
) -> None:
    created = meeting()
    job, artifact = abandon_recording_start(client, settings, db, clock, recorder, created)
    recorder.unavailable = False

    first = replay.retry(db, artifact.id, now=clock.now())  # par l'artefact
    second = replay.retry(db, job.id, now=clock.now())  # puis par la tâche, avant le tour
    assert (first.result, second.result) == (Result.REQUEUED, Result.ALREADY)
    tick(client)
    third = replay.retry(db, job.id, now=clock.now())  # après le succès
    assert third.result is Result.REFUSED
    tick(client)
    assert len(recorder.started) == 1
    assert db.scalars(select(JobRow).where(JobRow.kind == START_RECORDING)).all() == [job]
    assert len(db.scalars(select(ArtifactRow)).all()) == 1


def test_a_start_cannot_be_replayed_once_the_meeting_is_over(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    meeting: Meeting,
    recorder: FakeRecorder,
) -> None:
    created = meeting()
    job, artifact = abandon_recording_start(client, settings, db, clock, recorder, created)
    at(clock, "18:05:00")
    tick(client)  # fin du créneau
    recorder.unavailable = False

    outcome = replay.retry(db, job.id, now=clock.now())
    assert outcome.result is Result.REFUSED
    assert "plus en cours" in outcome.detail
    fresh(db)
    assert job.status is JobStatus.ABANDONED  # rien n'a bougé
    assert artifact.status is ArtifactStatus.FAILED
    assert recorder.started == []


# --- 2. un échec définitif n'est pas rejouable --------------------------------------------------


def test_a_failure_without_a_job_to_replay_is_final_and_says_so(
    db: Session, clock: FixedClock, meeting: Meeting, client: TestClient
) -> None:
    created = meeting()
    artifact = ArtifactRow(
        meeting_id=created["id"],
        kind=ArtifactKind.RECORDING,
        status=ArtifactStatus.FAILED,
        error="Egress a échoué : disque plein",
    )
    db.add(artifact)
    db.commit()

    (failure,) = replay.list_failures(db)
    assert failure.replay is None
    assert "définitif" in failure.note
    outcome = replay.retry(db, artifact.id, now=clock.now())
    assert outcome.result is Result.REFUSED
    fresh(db)
    assert artifact.status is ArtifactStatus.FAILED


def test_unknown_identifiers_are_reported_without_touching_anything(
    db: Session, clock: FixedClock
) -> None:
    now = clock.now()
    assert replay.retry(db, "job_inconnu", now=now).result is Result.NOT_FOUND
    assert replay.retry(db, "evt_inconnu", now=now).result is Result.NOT_FOUND
    assert replay.retry(db, "art_inconnu", now=now).result is Result.NOT_FOUND
    assert replay.retry(db, "trk_inconnu", now=now).result is Result.NOT_FOUND
    assert replay.retry(db, "n'importe quoi", now=now).result is Result.NOT_FOUND


# --- 3. fusion et transcription ----------------------------------------------------------------


def add_abandoned_merge(db: Session, meeting_id: str) -> tuple[JobRow, ArtifactRow]:
    artifact = ArtifactRow(
        meeting_id=meeting_id,
        kind=ArtifactKind.RECORDING,
        status=ArtifactStatus.FAILED,
        storage_key=f"meetings/{meeting_id}/recordings/x.mp4",
        error="fusion des segments impossible : ffmpeg est introuvable",
    )
    db.add(artifact)
    db.flush()
    job = JobRow(
        kind=MERGE_RECORDING,
        meeting_id=meeting_id,
        payload={"artifact_id": artifact.id, "duration_seconds": 60.0},
        status=JobStatus.ABANDONED,
        attempts=JOB_MAX_ATTEMPTS,
        last_error="ffmpeg est introuvable",
    )
    db.add(job)
    db.commit()
    return job, artifact


def test_an_abandoned_merge_is_replayed_and_announces_the_recording_once(
    client: TestClient,
    db: Session,
    clock: FixedClock,
    meeting: Meeting,
    sender: RecordingSender,
) -> None:
    created = meeting()
    job, artifact = add_abandoned_merge(db, created["id"])
    assert [f.identifier for f in replay.list_failures(db)] == [job.id, artifact.id]

    assert replay.retry(db, artifact.id, now=clock.now()).result is Result.REQUEUED
    fresh(db)
    assert artifact.status is ArtifactStatus.PENDING  # sans cela la fusion ignorerait la tâche
    assert run_merge_jobs(db, lambda key: 4096, now=clock.now()) == 1
    fresh(db)
    assert (job.status, artifact.status, artifact.size_bytes) == (
        JobStatus.DONE,
        ArtifactStatus.READY,
        4096,
    )
    tick(client)
    assert event_types(sender).count("recording.ready") == 1

    assert replay.retry(db, job.id, now=clock.now()).result is Result.REFUSED
    assert run_merge_jobs(db, lambda key: 4096, now=clock.now()) == 0
    tick(client)
    assert event_types(sender).count("recording.ready") == 1  # pas de doublon


def test_a_merge_that_fails_again_is_abandoned_again(
    db: Session, clock: FixedClock, meeting: Meeting
) -> None:
    from visio.services.segments import MergeError

    created = meeting()
    job, artifact = add_abandoned_merge(db, created["id"])
    replay.retry(db, job.id, now=clock.now())

    def broken(key: str) -> int:
        raise MergeError("encore en panne")  # noqa: EM101

    for _ in range(JOB_MAX_ATTEMPTS):
        run_merge_jobs(db, broken, now=clock.now())
        clock.advance(timedelta(hours=2))
    fresh(db)
    assert job.status is JobStatus.ABANDONED
    assert artifact.status is ArtifactStatus.FAILED


def test_an_abandoned_transcription_is_requeued_and_keeps_its_progress(
    db: Session, clock: FixedClock, meeting: Meeting
) -> None:
    created = meeting()
    transcript = ArtifactRow(
        meeting_id=created["id"],
        kind=ArtifactKind.TRANSCRIPT,
        status=ArtifactStatus.FAILED,
        storage_key=f"meetings/{created['id']}/transcripts/t.json",
        error="transcription impossible : moteur en panne",
    )
    job = JobRow(
        kind=TRANSCRIBE,
        meeting_id=created["id"],
        payload={"written": True},
        status=JobStatus.ABANDONED,
        attempts=JOB_MAX_ATTEMPTS,
    )
    db.add_all([transcript, job])
    db.commit()

    (_, listed) = sorted(replay.list_failures(db), key=lambda f: f.category)
    assert listed.replay == job.id

    assert replay.retry(db, transcript.id, now=clock.now()).result is Result.REQUEUED
    fresh(db)
    assert (job.status, job.attempts) == (JobStatus.PENDING, 0)
    assert job.payload == {"written": True}  # « déjà écrite » est gardé : pas de nouveau calcul
    assert transcript.status is ArtifactStatus.PENDING
    assert transcript.error is None


def test_an_abandoned_track_start_is_requeued_only_while_the_meeting_is_live(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    meeting: Meeting,
) -> None:
    created = meeting()
    connect(client, settings, created["organizer_urls"][0])
    track = AudioTrackRow(
        meeting_id=created["id"],
        track_sid="TR_1",
        participant_identity="p1",
        display_name="Marie Durand",
        storage_key=f"meetings/{created['id']}/audio/a.ogg",
        status=ArtifactStatus.FAILED,
        error="démarrage impossible : Egress injoignable",
    )
    db.add(track)
    db.flush()
    job = JobRow(
        kind="start_track",
        meeting_id=created["id"],
        payload={"track_id": track.id},
        status=JobStatus.ABANDONED,
        attempts=JOB_MAX_ATTEMPTS,
    )
    db.add(job)
    db.commit()

    assert replay.retry(db, track.id, now=clock.now()).result is Result.REQUEUED
    fresh(db)
    assert (job.status, track.status, track.error) == (
        JobStatus.PENDING,
        ArtifactStatus.PENDING,
        None,
    )


# --- 4. webhooks --------------------------------------------------------------------------------


def abandon_a_webhook(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    sender: RecordingSender,
    created: dict[str, Any],
) -> WebhookDeliveryRow:
    """Récepteur Eduvia en panne pendant toute la série de relances de `meeting.started`."""
    sender.responses = [SendResult(503, None)] * (MAX_ATTEMPTS * 3)
    connect(client, settings, created["organizer_urls"][0])
    for attempt in range(MAX_ATTEMPTS):
        tick(client)
        if attempt < len(RETRY_DELAYS):
            clock.advance(RETRY_DELAYS[attempt] + timedelta(seconds=1))
    hook = (
        fresh(db)
        .scalars(
            select(WebhookDeliveryRow).where(WebhookDeliveryRow.event_type == "meeting.started")
        )
        .one()
    )
    assert hook.status is WebhookStatus.ABANDONED
    return hook


def test_an_abandoned_webhook_is_listed_replayed_and_delivered_once_with_the_same_id(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    meeting: Meeting,
    sender: RecordingSender,
) -> None:
    created = meeting()
    hook = abandon_a_webhook(client, settings, db, clock, sender, created)
    failure = next(f for f in replay.list_failures(db) if f.identifier == hook.id)
    assert failure.abandoned
    assert failure.error and "503" in failure.error

    sender.responses.clear()  # le récepteur est revenu
    calls_before = len(sender.calls)
    assert replay.retry(db, hook.id, now=clock.now()).result is Result.REQUEUED
    tick(client)
    fresh(db)
    assert hook.status is WebhookStatus.DELIVERED
    resent = sender.calls[calls_before:]
    started = [h for _, h, _ in resent if h["X-Visio-Event-Type"] == "meeting.started"]
    assert [h["X-Visio-Event-Id"] for h in started] == [
        hook.id
    ]  # même identifiant : dédoublonnable
    assert (
        json.loads(next(b for _, h, b in resent if h["X-Visio-Event-Id"] == hook.id))["id"]
        == hook.id
    )

    # déjà livré : jamais de second envoi
    assert replay.retry(db, hook.id, now=clock.now()).result is Result.REFUSED
    count = len(sender.calls)
    tick(client)
    assert len(sender.calls) == count


def test_a_webhook_still_waiting_for_its_first_delivery_is_not_a_failure(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    meeting: Meeting,
) -> None:
    created = meeting()
    connect(client, settings, created["organizer_urls"][0])  # événements écrits, pas encore livrés
    pending = fresh(db).scalars(select(WebhookDeliveryRow)).all()
    assert pending and all(h.attempts == 0 for h in pending)
    assert replay.list_failures(db) == []
    assert replay.retry(db, pending[0].id, now=clock.now()).result is Result.ALREADY


def test_a_webhook_in_retry_is_listed_and_can_be_brought_forward(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    meeting: Meeting,
    sender: RecordingSender,
) -> None:
    created = meeting()
    sender.responses = [SendResult(500, None)]
    connect(client, settings, created["organizer_urls"][0])
    tick(client)  # première livraison de meeting.started : échec, relance programmée
    (hook,) = [
        h
        for h in fresh(db).scalars(select(WebhookDeliveryRow))
        if h.status is WebhookStatus.PENDING and h.attempts == 1
    ]
    failure = next(f for f in replay.list_failures(db) if f.identifier == hook.id)
    assert not failure.abandoned
    assert failure.next_attempt_at is not None

    assert replay.retry(db, hook.id, now=clock.now()).result is Result.REQUEUED
    fresh(db)
    assert (hook.attempts, hook.next_attempt_at) == (0, clock.now())


# --- 5. `--all`, filtre par réunion -------------------------------------------------------------


def test_replay_all_only_touches_what_is_abandoned_and_is_idempotent(
    client: TestClient,
    db: Session,
    clock: FixedClock,
    meeting: Meeting,
) -> None:
    created = meeting()
    abandoned, _ = add_abandoned_merge(db, created["id"])
    waiting = JobRow(
        kind="close_room",
        meeting_id=created["id"],
        status=JobStatus.FAILED,
        attempts=2,
        run_at=clock.now() + timedelta(minutes=10),
    )
    db.add(waiting)
    db.commit()

    outcomes = replay.retry_all(db, now=clock.now())
    assert [(o.identifier, o.result) for o in outcomes] == [(abandoned.id, Result.REQUEUED)]
    fresh(db)
    assert (waiting.status, waiting.attempts) == (JobStatus.FAILED, 2)  # il se rattrape seul
    assert replay.retry_all(db, now=clock.now()) == []  # rien d'abandonné : rien à refaire


def test_the_list_can_be_filtered_by_meeting(
    client: TestClient, db: Session, clock: FixedClock, meeting: Meeting, auth: dict[str, str]
) -> None:
    first = meeting()
    add_abandoned_merge(db, first["id"])
    other = client.post("/v1/meetings", json=meeting_body(title="Autre"), headers=auth).json()
    add_abandoned_merge(db, other["id"])

    assert {f.meeting_id for f in replay.list_failures(db)} == {first["id"], other["id"]}
    assert {f.meeting_id for f in replay.list_failures(db, meeting_id=other["id"])} == {other["id"]}
    assert replay.list_failures(db, meeting_id="mtg_vide") == []


# --- 6. rien de sensible, erreurs bornées ------------------------------------------------------


def test_the_listing_is_bounded_single_line_and_without_secrets(
    db: Session, clock: FixedClock, meeting: Meeting, settings: Settings
) -> None:
    created = meeting()
    hook = WebhookDeliveryRow(
        meeting_id=created["id"],
        event_type="meeting.started",
        payload={"meeting": {"metadata": {"eduvia_session_id": 1234}}},
        status=WebhookStatus.ABANDONED,
        attempts=MAX_ATTEMPTS,
        last_error="ConnectError: " + "très long\nmessage " * 100,
    )
    db.add(hook)
    db.commit()

    lines = jobslist.render_all(replay.list_failures(db))
    text = "\n".join(lines)
    assert hook.id in text
    assert all(len(line) < 400 for line in lines)
    erreur = next(line for line in lines if "erreur :" in line)
    assert erreur.endswith("…") and "\n" not in erreur
    for secret in (
        settings.webhook_secret.get_secret_value(),
        settings.api_key.get_secret_value(),
        settings.webhook_url,
        "eduvia_session_id",  # le contenu de l'événement n'est jamais affiché
    ):
        assert secret not in text


# --- 7. les commandes --------------------------------------------------------------------------


def test_jobs_list_command_reports_failures_and_its_exit_code(
    settings: Settings, db: Session, clock: FixedClock, meeting: Meeting
) -> None:
    out: list[str] = []
    assert jobslist.main([], out=out.append) == 0
    assert out == ["Rien en échec."]

    created = meeting()
    job, _ = add_abandoned_merge(db, created["id"])
    out.clear()
    assert jobslist.main([], out=out.append) == 1  # utilisable par une supervision
    text = "\n".join(out)
    assert job.id in text and f"jobs-retry {job.id}" in text
    assert "ffmpeg est introuvable" in text
    out.clear()
    assert jobslist.main([created["id"]], out=out.append) == 1
    out.clear()
    assert jobslist.main(["mtg_autre"], out=out.append) == 0


def test_jobs_retry_command_requeues_then_says_it_is_already_waiting(
    settings: Settings, db: Session, clock: FixedClock, meeting: Meeting
) -> None:
    created = meeting()
    job, artifact = add_abandoned_merge(db, created["id"])
    out: list[str] = []
    assert jobsretry.main([job.id], out=out.append) == 0
    assert "remis en file" in out[0]
    fresh(db)
    assert (job.status, job.attempts) == (JobStatus.PENDING, 0)
    assert artifact.status is ArtifactStatus.PENDING

    out.clear()
    assert jobsretry.main([job.id], out=out.append) == 0
    assert "déjà en attente" in out[0]
    out.clear()
    assert jobsretry.main(["job_inconnu"], out=out.append) == 1
    assert "introuvable" in out[0]


def test_jobs_retry_all_command(
    settings: Settings, db: Session, clock: FixedClock, meeting: Meeting
) -> None:
    created = meeting()
    job, _ = add_abandoned_merge(db, created["id"])
    out: list[str] = []
    assert jobsretry.main(["--all"], out=out.append) == 0
    assert job.id in out[0]
    out.clear()
    assert jobsretry.main(["--all"], out=out.append) == 0
    assert out == ["Rien d'abandonné à rejouer."]


@pytest.mark.parametrize(
    "args", [[], ["--all", "mtg_1", "mtg_2"], ["--force", "job_1"], ["--all", "--oups"]]
)
def test_jobs_retry_command_refuses_a_bad_command_line(args: list[str]) -> None:
    out: list[str] = []
    assert jobsretry.main(args, out=out.append) == 2
    assert out == [jobsretry.USAGE]


# --- 8. corrélation (limite 174 de 17a) --------------------------------------------------------


def test_the_abandon_log_carries_the_meeting_id(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    meeting: Meeting,
    recorder: FakeRecorder,
    caplog: pytest.LogCaptureFixture,
) -> None:
    created = meeting()
    with caplog.at_level(logging.ERROR):
        abandon_recording_start(client, settings, db, clock, recorder, created)
    abandoned = [r for r in caplog.records if "abandonné" in r.getMessage()]
    assert abandoned
    assert all(getattr(r, "meeting_id", None) == created["id"] for r in abandoned)
