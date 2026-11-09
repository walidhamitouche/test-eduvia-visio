"""Étape 16b : une piste audio par participant, pour attribuer la parole (transcription).

Contre un faux Egress et un faux S3 : aucun Docker. Le vrai Egress se vérifie à la main
(`test-enregistrement.ps1`, une réunion à deux, `uv run task recordings-list`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.integration.conftest import meeting_body
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
from visio.db.models import ArtifactStatus, AudioTrackRow, JobRow, JobStatus
from visio.domain.clock import FixedClock
from visio.services import tracks
from visio.services.egress import EgressGateway, FakeRecorder
from visio.services.jobs import JOB_MAX_ATTEMPTS, JOB_RETRY_DELAYS
from visio.services.storage import FakeStorage

KEY = re.compile(r"^meetings/mtg_[0-9a-f]{32}/audio/[0-9a-f-]{36}\.ogg$")

Meeting = Callable[..., dict[str, Any]]


@pytest.fixture
def meeting(client: TestClient, auth: dict[str, str], clock: FixedClock) -> Meeting:
    def _make(**overrides: Any) -> dict[str, Any]:
        response = client.post(
            "/v1/meetings",
            json=meeting_body(**{"waiting_room": False, "early_join_minutes": 0, **overrides}),
            headers=auth,
        )
        assert response.status_code == 201, response.text
        at(clock, "16:00:00")
        created: dict[str, Any] = response.json()
        return created

    return _make


def connect(
    client: TestClient, settings: Settings, url: str, when: str = "16:00:10", sid: str = "PA_1"
) -> dict[str, Any]:
    claims = claims_of(settings, join(client, url).json()["livekit_token"])
    response = livekit_webhook(
        client,
        settings,
        "participant_joined",
        claims,
        sid=sid,
        created_at=utc_seconds(when),
        joined_at=utc_seconds(when),
    )
    assert response.status_code == 200, response.text
    return claims


def all_tracks(db: Session) -> list[AudioTrackRow]:
    db.expire_all()
    return list(db.scalars(select(AudioTrackRow).order_by(AudioTrackRow.created_at)).all())


def track_jobs(db: Session) -> list[JobRow]:
    db.expire_all()
    return list(db.scalars(select(JobRow).where(JobRow.kind == tracks.START_TRACK)).all())


def publish(
    client: TestClient, settings: Settings, claims: dict[str, Any], sid: str = "TR_mic1", **kw: Any
) -> str:
    response = track_published_webhook(client, settings, claims, track_sid=sid, **kw)
    assert response.status_code == 200, response.text
    return str(response.json()["result"])


# --- création de la piste ------------------------------------------------------------------------


def test_a_microphone_gets_its_own_pending_track_with_the_name_of_the_link(
    client: TestClient, settings: Settings, db: Session, meeting: Meeting, recorder: FakeRecorder
) -> None:
    created = meeting()
    claims = connect(client, settings, created["organizer_urls"][0])
    assert publish(client, settings, claims) == "queued"

    (row,) = all_tracks(db)
    assert row.meeting_id == created["id"]
    assert row.track_sid == "TR_mic1"
    assert row.display_name == "Marie Durand"
    assert row.external_id == "staff_42"
    assert row.status is ArtifactStatus.PENDING
    assert KEY.match(row.storage_key)
    assert row.egress_id is None
    assert recorder.tracks_started == []  # c'est la tâche qui démarre, pas la requête du webhook
    (job,) = track_jobs(db)
    assert job.payload == {"track_id": row.id}


def test_a_named_link_gives_its_name_and_external_id_to_the_track(
    client: TestClient,
    settings: Settings,
    auth: dict[str, str],
    db: Session,
    meeting: Meeting,
) -> None:
    """Scénario 5 (amont) : le nom du lien nominatif est attaché à la piste, pas deviné."""
    created = meeting()
    response = client.post(
        f"/v1/meetings/{created['id']}/links",
        json={"role": "participant", "display_name": "Paul Martin", "external_id": "stu_7"},
        headers=auth,
    )
    assert response.status_code == 201, response.text
    claims = connect(client, settings, response.json()["url"])
    publish(client, settings, claims, sid="TR_paul")
    (row,) = all_tracks(db)
    assert (row.display_name, row.external_id) == ("Paul Martin", "stu_7")


def test_every_participant_has_a_separate_file(
    client: TestClient, settings: Settings, auth: dict[str, str], db: Session, meeting: Meeting
) -> None:
    created = meeting()
    marie = connect(client, settings, created["organizer_urls"][0], sid="PA_1")
    paul_link = client.post(
        f"/v1/meetings/{created['id']}/links",
        json={"role": "participant", "display_name": "Paul Martin", "external_id": "stu_7"},
        headers=auth,
    ).json()["url"]
    paul = connect(client, settings, paul_link, sid="PA_2")
    publish(client, settings, marie, sid="TR_marie")
    publish(client, settings, paul, sid="TR_paul")
    rows = all_tracks(db)
    assert {r.display_name for r in rows} == {"Marie Durand", "Paul Martin"}
    assert len({r.storage_key for r in rows}) == 2


def test_no_track_when_transcription_is_off(
    client: TestClient, settings: Settings, db: Session, meeting: Meeting
) -> None:
    created = meeting(transcription=False)
    claims = connect(client, settings, created["organizer_urls"][0])
    assert publish(client, settings, claims) == "transcription-off"
    assert all_tracks(db) == []
    assert track_jobs(db) == []


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"source": "CAMERA", "kind": "VIDEO"}, "not-a-microphone"),
        ({"source": "SCREEN_SHARE_AUDIO"}, "not-a-microphone"),
        ({"source": "SCREEN_SHARE", "kind": "VIDEO"}, "not-a-microphone"),
    ],
)
def test_only_microphones_are_recorded(
    client: TestClient,
    settings: Settings,
    db: Session,
    meeting: Meeting,
    kw: dict[str, str],
    expected: str,
) -> None:
    claims = connect(client, settings, meeting()["organizer_urls"][0])
    assert publish(client, settings, claims, **kw) == expected
    assert all_tracks(db) == []


def test_numeric_enums_of_the_webhook_are_understood(
    client: TestClient, settings: Settings, db: Session, meeting: Meeting
) -> None:
    claims = connect(client, settings, meeting()["organizer_urls"][0])
    assert publish(client, settings, claims, kind=0, source=2) == "queued"
    assert len(all_tracks(db)) == 1


def test_an_audio_track_arrives_without_a_type_field_in_the_real_webhook(
    client: TestClient, settings: Settings, db: Session, meeting: Meeting
) -> None:
    """LiveKit omet les valeurs par défaut des énumérations (AUDIO = 0) : pas de champ `type`."""
    from tests.integration.helpers import signed_event

    claims = connect(client, settings, meeting()["organizer_urls"][0])
    response = signed_event(
        client,
        settings,
        {
            "event": "track_published",
            "room": {"name": claims["video"]["room"]},
            "participant": {"sid": "PA_1", "identity": claims["sub"]},
            "track": {"sid": "TR_real", "source": "MICROPHONE", "mimeType": "audio/red"},
            "createdAt": 1_800_000_000,
        },
    )
    assert response.json() == {"result": "queued"}
    assert [r.track_sid for r in all_tracks(db)] == ["TR_real"]


def test_a_replayed_webhook_does_not_create_a_second_track(
    client: TestClient, settings: Settings, db: Session, meeting: Meeting
) -> None:
    claims = connect(client, settings, meeting()["organizer_urls"][0])
    assert publish(client, settings, claims) == "queued"
    assert publish(client, settings, claims) == "duplicate"
    assert len(all_tracks(db)) == 1
    assert len(track_jobs(db)) == 1


def test_someone_without_access_is_not_recorded(
    client: TestClient,
    settings: Settings,
    auth: dict[str, str],
    db: Session,
    meeting: Meeting,
) -> None:
    created = meeting(waiting_room=True)
    # Personne en salle d'attente, jamais admise : on n'enregistre pas sa voix.
    body = join(client, created["participant_url"], display_name="Intrus").json()
    assert body["status"] == "waiting"
    other = claims_of(settings, join(client, created["organizer_urls"][0]).json()["livekit_token"])
    forged = {**other, "sub": "pg_" + "0" * 32}
    assert publish(client, settings, forged) == "not-admitted"
    assert all_tracks(db) == []


def test_non_human_participants_and_unknown_rooms_are_ignored(
    client: TestClient, settings: Settings, db: Session, meeting: Meeting
) -> None:
    claims = connect(client, settings, meeting()["organizer_urls"][0])
    recorder_like = {**claims, "sub": "EG_recorder"}
    assert publish(client, settings, recorder_like) == "ignored"
    elsewhere = {**claims, "video": {"room": "rm_inconnue"}}
    assert publish(client, settings, elsewhere) == "unknown-room"
    assert all_tracks(db) == []


def test_the_webhook_must_be_signed(client: TestClient) -> None:
    response = client.post("/v1/livekit/webhook", json={"event": "track_published"})
    assert response.status_code == 401


# --- démarrage ------------------------------------------------------------------------------


def test_the_scheduler_starts_each_track_once(
    client: TestClient,
    settings: Settings,
    db: Session,
    meeting: Meeting,
    recorder: FakeRecorder,
) -> None:
    created = meeting()
    claims = connect(client, settings, created["organizer_urls"][0])
    publish(client, settings, claims)
    tick(client)
    tick(client)
    (row,) = all_tracks(db)
    assert len(recorder.tracks_started) == 1
    assert recorder.tracks_started[0][1:] == ("TR_mic1", row.storage_key)
    assert row.egress_id is not None
    assert [j.status for j in track_jobs(db)] == [JobStatus.DONE]


def test_an_unavailable_egress_is_retried_then_the_track_fails_but_nothing_else_does(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    meeting: Meeting,
    recorder: FakeRecorder,
) -> None:
    claims = connect(client, settings, meeting()["organizer_urls"][0])
    publish(client, settings, claims)
    recorder.unavailable = True
    for attempt in range(JOB_MAX_ATTEMPTS):
        tick(client)
        if attempt < len(JOB_RETRY_DELAYS):  # reste dans le créneau : la réunion n'est pas finie
            clock.set(clock.now() + JOB_RETRY_DELAYS[attempt])
    (row,) = all_tracks(db)
    assert row.status is ArtifactStatus.FAILED
    assert row.error is not None
    assert row.error.startswith("démarrage impossible")
    assert [j.status for j in track_jobs(db)] == [JobStatus.ABANDONED]


def test_egress_back_after_a_retry_starts_the_track(
    client: TestClient, settings: Settings, db: Session, meeting: Meeting, recorder: FakeRecorder
) -> None:
    claims = connect(client, settings, meeting()["organizer_urls"][0])
    publish(client, settings, claims)
    recorder.unavailable = True
    tick(client)
    assert all_tracks(db)[0].egress_id is None
    recorder.unavailable = False
    clock: FixedClock = client.app.state.clock  # type: ignore[attr-defined]
    clock.set(clock.now() + timedelta(minutes=1))
    tick(client)
    assert all_tracks(db)[0].egress_id is not None
    assert len(recorder.tracks_started) == 1


# --- issue ----------------------------------------------------------------------------------


def started_track(
    client: TestClient, settings: Settings, db: Session, meeting: Meeting
) -> tuple[AudioTrackRow, dict[str, Any]]:
    created = meeting()
    claims = connect(client, settings, created["organizer_urls"][0])
    publish(client, settings, claims, created_at=utc_seconds("16:00:12"))
    tick(client)
    (row,) = all_tracks(db)
    assert row.egress_id is not None
    return row, created


def test_a_finished_track_becomes_ready_with_the_real_start_of_the_file(
    client: TestClient, settings: Settings, db: Session, storage: FakeStorage, meeting: Meeting
) -> None:
    row, _ = started_track(client, settings, db, meeting)
    assert row.started_at.timestamp() == pytest.approx(utc_seconds("16:00:12"))
    storage.objects[row.storage_key] = b"OggS" * 100
    real_start = utc_seconds("16:00:15")
    response = track_egress_ended_webhook(
        client, settings, str(row.egress_id), started_at=real_start, filename=row.storage_key
    )
    assert response.json() == {"result": "ready"}
    (row,) = all_tracks(db)
    assert row.status is ArtifactStatus.READY
    assert row.size_bytes == 400
    assert row.started_at.timestamp() == pytest.approx(real_start)
    assert [r.id for r in tracks.ready_tracks(db, row.meeting_id)] == [row.id]


def test_a_track_without_file_or_with_an_egress_error_fails_with_a_reason(
    client: TestClient, settings: Settings, db: Session, meeting: Meeting
) -> None:
    row, _ = started_track(client, settings, db, meeting)
    response = track_egress_ended_webhook(client, settings, str(row.egress_id), filename="x.ogg")
    assert response.json() == {"result": "failed"}
    (row,) = all_tracks(db)
    assert row.status is ArtifactStatus.FAILED
    assert row.error and "absent du stockage" in row.error
    assert tracks.ready_tracks(db, row.meeting_id) == []


def test_egress_error_gives_its_reason(
    client: TestClient, settings: Settings, db: Session, meeting: Meeting
) -> None:
    row, _ = started_track(client, settings, db, meeting)
    track_egress_ended_webhook(
        client,
        settings,
        str(row.egress_id),
        status="EGRESS_FAILED",
        error="Start signal not received",
    )
    assert "aucune piste" in str(all_tracks(db)[0].error)


def test_egress_may_name_the_file_differently(
    client: TestClient, settings: Settings, db: Session, storage: FakeStorage, meeting: Meeting
) -> None:
    row, created = started_track(client, settings, db, meeting)
    actual = f"meetings/{created['id']}/audio/autre-nom.ogg"
    storage.objects[actual] = b"OggS"
    track_egress_ended_webhook(client, settings, str(row.egress_id), filename=actual)
    (row,) = all_tracks(db)
    assert row.status is ArtifactStatus.READY
    assert row.storage_key == actual


def test_a_replayed_egress_ended_changes_nothing(
    client: TestClient, settings: Settings, db: Session, storage: FakeStorage, meeting: Meeting
) -> None:
    row, _ = started_track(client, settings, db, meeting)
    storage.objects[row.storage_key] = b"OggS"
    track_egress_ended_webhook(client, settings, str(row.egress_id), filename=row.storage_key)
    again = track_egress_ended_webhook(
        client, settings, str(row.egress_id), filename=row.storage_key
    )
    assert again.json() == {"result": "unchanged"}


def test_the_recording_webhook_still_works_next_to_the_tracks(
    client: TestClient, settings: Settings, meeting: Meeting
) -> None:
    response = track_egress_ended_webhook(client, settings, "EG_inconnu")
    assert response.json() == {"result": "unknown-egress"}


def test_the_scheduler_net_settles_a_track_whose_webhook_was_lost(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    storage: FakeStorage,
    meeting: Meeting,
    recorder: FakeRecorder,
) -> None:
    row, _ = started_track(client, settings, db, meeting)
    storage.objects[row.storage_key] = b"OggS" * 10
    at(clock, "18:00:30")
    tick(client)  # fin de créneau : la réunion se termine, la salle se ferme
    assert row.egress_id in recorder.stopped  # piste arrêtée avant la fermeture
    recorder.finish(str(row.egress_id))
    clock.set(clock.now() + timedelta(minutes=1))
    tick(client)
    (row,) = all_tracks(db)
    assert row.status is ArtifactStatus.READY


def test_a_track_not_yet_started_when_the_meeting_ends_is_cancelled(
    client: TestClient,
    settings: Settings,
    db: Session,
    clock: FixedClock,
    meeting: Meeting,
    recorder: FakeRecorder,
) -> None:
    claims = connect(client, settings, meeting()["organizer_urls"][0])
    publish(client, settings, claims)
    recorder.unavailable = True
    at(clock, "18:00:30")
    tick(client)
    (row,) = all_tracks(db)
    assert row.status is ArtifactStatus.FAILED
    assert row.error and "avant le démarrage" in row.error
    assert [j.status for j in track_jobs(db)] == [JobStatus.DONE]


# --- lecture et nettoyage (pour le worker) -------------------------------------------------


def test_offsets_replace_each_track_on_the_meeting_timeline(
    client: TestClient, settings: Settings, db: Session, storage: FakeStorage, meeting: Meeting
) -> None:
    row, _ = started_track(client, settings, db, meeting)
    storage.objects[row.storage_key] = b"OggS"
    track_egress_ended_webhook(
        client,
        settings,
        str(row.egress_id),
        started_at=utc_seconds("16:00:15"),
        filename=row.storage_key,
    )
    (row,) = all_tracks(db)
    origin = row.started_at - timedelta(seconds=5)
    assert tracks.offset_seconds(row, origin) == pytest.approx(5.0)
    assert tracks.offset_seconds(row, row.started_at + timedelta(seconds=9)) == 0.0  # jamais < 0


def test_purge_removes_the_work_files_and_rows_but_not_the_recording(
    client: TestClient, settings: Settings, db: Session, storage: FakeStorage, meeting: Meeting
) -> None:
    row, created = started_track(client, settings, db, meeting)
    storage.objects[row.storage_key] = b"OggS"
    video = f"meetings/{created['id']}/recordings/11111111-1111-1111-1111-111111111111.mp4"
    storage.objects[video] = b"video"
    assert tracks.purge(db, storage, created["id"]) == 1
    assert storage.objects == {video: b"video"}
    assert all_tracks(db) == []


def test_purge_with_a_dead_s3_keeps_the_rows(
    client: TestClient, settings: Settings, db: Session, storage: FakeStorage, meeting: Meeting
) -> None:
    row, created = started_track(client, settings, db, meeting)
    storage.objects[row.storage_key] = b"OggS"
    storage.unreachable = True
    with pytest.raises(Exception, match="injoignable"):
        tracks.purge(db, storage, created["id"])
    assert len(all_tracks(db)) == 1


# --- passerelle HTTP vers Egress ---------------------------------------------------------------


def test_the_gateway_orders_a_track_egress_on_one_microphone() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"egressId": "EG_t", "status": "EGRESS_STARTING"})

    gateway = EgressGateway(
        "http://livekit.test",
        "key",
        "s" * 40,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    info = gateway.start_track_audio("rm_x", "TR_abc", "meetings/m/audio/u.ogg")
    assert info.egress_id == "EG_t"
    (request,) = seen
    assert request.url.path == "/twirp/livekit.Egress/StartTrackEgress"
    assert request.read() == (
        b'{"room_name":"rm_x","track_id":"TR_abc","file":{"filepath":"meetings/m/audio/u.ogg"}}'
    )


def test_the_tracks_list_tool_shows_who_and_why(
    client: TestClient, settings: Settings, db: Session, meeting: Meeting
) -> None:
    from visio.tools.trackslist import render

    row, _ = started_track(client, settings, db, meeting)
    text = "\n".join(render(row, None))
    assert "Marie Durand (staff_42) : pending, absent du S3" in text
    row.error = "boom"
    assert "erreur : boom" in "\n".join(render(row, 2048))
    assert "2.0 Ko" in "\n".join(render(row, 2048))


# --- démarrage des pistes : boucle à part (16d) --------------------------------------------------


def test_the_shared_loop_leaves_the_tracks_to_their_own_loop(
    client: TestClient,
    settings: Settings,
    db: Session,
    meeting: Meeting,
    recorder: FakeRecorder,
) -> None:
    from visio.workers.scheduler import run_once

    created = meeting()
    claims = connect(client, settings, created["organizer_urls"][0])
    publish(client, settings, claims)
    state = client.app.state  # type: ignore[attr-defined]
    run_once(
        state.session_factory,
        state.settings,
        state.rooms,
        state.recorder,
        state.webhook_sender,
        state.clock,
        state.storage,
        with_tracks=False,
    )
    assert recorder.tracks_started == []  # la boucle commune ne démarre plus les pistes
    assert [j.status for j in track_jobs(db)] == [JobStatus.PENDING]


def test_a_slow_recording_start_does_not_hold_back_a_track(
    client: TestClient,
    settings: Settings,
    db: Session,
    meeting: Meeting,
    recorder: FakeRecorder,
) -> None:
    """Mesuré en réel : l'ordre de démarrer l'enregistrement de la réunion bloque ~10 s ; la piste
    audio de la première personne attendait derrière lui et son début de phrase était perdu."""
    import asyncio
    import contextlib
    import threading

    from visio.workers import scheduler

    created = meeting()
    claims = connect(client, settings, created["organizer_urls"][0])
    publish(client, settings, claims)
    release = threading.Event()
    start_composite = recorder.start_room_composite

    def slow_composite(room: str, key: str) -> Any:
        release.wait(10)  # Chrome + gabarit : l'appel à Egress ne rend la main qu'ensuite
        return start_composite(room, key)

    recorder.start_room_composite = slow_composite  # type: ignore[method-assign]
    state = client.app.state  # type: ignore[attr-defined]

    async def scenario() -> bool:
        loop = asyncio.create_task(
            scheduler.run_forever(
                state.session_factory,
                state.settings,
                state.rooms,
                state.recorder,
                state.webhook_sender,
                state.clock,
                state.storage,
                interval=0.05,
            )
        )
        try:
            for _ in range(100):
                await asyncio.sleep(0.05)
                if recorder.tracks_started:
                    break
            return bool(recorder.tracks_started) and not recorder.started
        finally:
            release.set()
            loop.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop

    assert asyncio.run(scenario()), "la piste a attendu la fin du démarrage de l'enregistrement"
