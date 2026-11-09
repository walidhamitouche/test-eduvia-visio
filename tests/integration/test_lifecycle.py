"""Fin de créneau, clôture, annulation, modification (étape 10).

Le planificateur est sans état : il relit la base à chaque tour. Ces tests le font tourner à la
main (`tick`) avec l'horloge simulée, y compris « redémarrage du service » (nouvelle application
sur la même base).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.integration.conftest import RecordingSender, meeting_body
from tests.integration.helpers import (
    at,
    enter,
    error_code,
    join,
    livekit_webhook,
    tick,
    utc_seconds,
)
from visio.config import Settings
from visio.db.models import JobRow, JobStatus, MeetingRow, ParticipantSessionRow
from visio.domain.clock import FixedClock
from visio.domain.meeting import MeetingState
from visio.main import create_app
from visio.services.jobs import JOB_MAX_ATTEMPTS
from visio.services.rooms import FakeRoomGateway


@pytest.fixture
def meeting(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    body = meeting_body(waiting_room=False, early_join_minutes=0)
    response = client.post("/v1/meetings", json=body, headers=auth)
    assert response.status_code == 201
    created: dict[str, Any] = response.json()
    return created


def status_of(client: TestClient, auth: dict[str, str], meeting: dict[str, Any]) -> str:
    return str(client.get(f"/v1/meetings/{meeting['id']}", headers=auth).json()["status"])


def go_live(
    client: TestClient, settings: Settings, rooms: FakeRoomGateway, meeting: dict[str, Any]
) -> list[Any]:
    """Une organisatrice et deux participants entrent (jeton + webhook de connexion)."""
    from tests.integration.helpers import claims_of

    people = []
    for url, extra in (
        (meeting["organizer_urls"][0], {}),
        (meeting["participant_url"], {"display_name": "Lina"}),
        (meeting["participant_url"], {"display_name": "Karim"}),
    ):
        person = enter(client, settings, rooms, url, **extra)
        body = join(client, url, **extra).json()
        claims = claims_of(settings, body["livekit_token"])
        livekit_webhook(
            client, settings, "participant_joined", claims, sid=person.sid,
            created_at=utc_seconds("16:00:00"), joined_at=utc_seconds("16:00:00"),
        )  # fmt: skip
        people.append(person)
    return people


# --- scénario 1 : la coupure à l'heure ---------------------------------------------------------


def test_at_the_end_of_the_slot_the_room_is_closed_and_the_meeting_ended(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    settings: Settings,
    rooms: FakeRoomGateway,
    meeting: dict[str, Any],
) -> None:
    at(clock, "16:00:00")
    go_live(client, settings, rooms, meeting)
    room = next(iter(rooms.participants))

    at(clock, "17:59:59")
    tick(client)
    assert rooms.called("delete_room") == []  # pas avant l'heure
    assert status_of(client, auth, meeting) == "live"

    at(clock, "18:00:00")
    tick(client)

    assert rooms.called("delete_room") == [(room,)]
    assert status_of(client, auth, meeting) == "ended"
    at(clock, "18:01:00")
    refused = join(client, meeting["participant_url"], display_name="Retard")
    assert refused.status_code == 409
    assert error_code(refused) == "meeting_ended"


def test_every_open_presence_is_closed_at_the_end_time_and_announced(
    client: TestClient,
    clock: FixedClock,
    settings: Settings,
    rooms: FakeRoomGateway,
    sender: RecordingSender,
    db: Session,
    meeting: dict[str, Any],
) -> None:
    at(clock, "16:00:00")
    go_live(client, settings, rooms, meeting)
    at(clock, "18:00:05")  # le planificateur a 5 s de retard : la coupure date de 18:00:00
    tick(client)
    db.expire_all()

    sessions = db.query(ParticipantSessionRow).all()
    assert len(sessions) == 3
    assert {s.left_at for s in sessions} == {sessions[0].left_at}
    assert sessions[0].left_at is not None
    assert sessions[0].left_at.isoformat().startswith("2027-01-02T17:00:00")  # 18:00 Paris
    import json

    types = [json.loads(body)["type"] for _, _, body in sender.calls]
    # `recording.failed` (enregistrement pas encore démarré, étape 14b) n'entre pas dans l'ordre.
    types = [t for t in types if t.startswith(("participant.", "meeting."))]
    assert types.count("participant.left") == 3
    assert types[-1] == "meeting.ended"  # les sorties d'abord, la fin ensuite
    ended = next(json.loads(b) for _, _, b in sender.calls if b'"meeting.ended"' in b)
    assert ended["data"]["reason"] == "slot_end"
    assert ended["data"]["ended_at"].startswith("2027-01-02T17:00:00")


def test_a_meeting_nobody_joined_still_ends_without_touching_the_media_server(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    rooms: FakeRoomGateway,
    sender: RecordingSender,
    meeting: dict[str, Any],
) -> None:
    at(clock, "18:00:00")
    tick(client)
    assert status_of(client, auth, meeting) == "ended"
    assert rooms.calls == []  # aucune salle n'a jamais existé
    import json

    assert [json.loads(b)["type"] for _, _, b in sender.calls] == ["meeting.ended"]


def test_a_cancelled_meeting_is_left_alone(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    sender: RecordingSender,
    meeting: dict[str, Any],
) -> None:
    client.delete(f"/v1/meetings/{meeting['id']}", headers=auth)
    at(clock, "18:30:00")
    tick(client)
    assert status_of(client, auth, meeting) == "cancelled"
    assert sender.calls == []


# --- modification et clôture -------------------------------------------------------------------


def test_patching_the_slot_moves_the_cut_off(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    rooms: FakeRoomGateway,
    meeting: dict[str, Any],
) -> None:
    patched = client.patch(
        f"/v1/meetings/{meeting['id']}",
        json={"ends_at": "2027-01-02T19:00:00+01:00"},
        headers=auth,
    )
    assert patched.status_code == 200

    at(clock, "18:30:00")  # l'ancienne heure de fin est passée
    tick(client)
    assert status_of(client, auth, meeting) == "scheduled"

    at(clock, "19:00:00")
    tick(client)
    assert status_of(client, auth, meeting) == "ended"


def test_the_api_ends_a_running_meeting_and_closes_the_room_at_once(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    settings: Settings,
    rooms: FakeRoomGateway,
    sender: RecordingSender,
    meeting: dict[str, Any],
) -> None:
    at(clock, "16:30:00")
    go_live(client, settings, rooms, meeting)
    room = next(iter(rooms.participants))
    at(clock, "17:00:00")

    response = client.post(f"/v1/meetings/{meeting['id']}/end", headers=auth)

    assert response.status_code == 200
    assert response.json()["status"] == "ended"
    assert rooms.called("delete_room") == [(room,)]  # sans attendre le planificateur
    import json

    tick(client)  # la livraison des webhooks est le travail du planificateur
    ended = [json.loads(b) for _, _, b in sender.calls if b"meeting.ended" in b]
    assert ended and ended[0]["data"]["reason"] == "api"
    # rejouer ne refait rien
    sender.calls.clear()
    assert client.post(f"/v1/meetings/{meeting['id']}/end", headers=auth).status_code == 200
    tick(client)
    assert [c for c in sender.calls if b"meeting.ended" in c[2]] == []
    assert len(rooms.called("delete_room")) == 1


def test_ending_early_is_the_cut_off_for_joining_even_inside_the_slot(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    meeting: dict[str, Any],
) -> None:
    at(clock, "16:30:00")
    assert join(client, meeting["participant_url"], display_name="A").status_code == 200
    client.post(f"/v1/meetings/{meeting['id']}/end", headers=auth)
    at(clock, "17:00:01")
    assert join(client, meeting["participant_url"], display_name="B").status_code == 409


# --- résilience --------------------------------------------------------------------------------


def test_a_restart_before_the_end_still_cuts_at_the_end(
    settings: Settings,
    clock: FixedClock,
    rooms: FakeRoomGateway,
    sender: RecordingSender,
    client: TestClient,
    auth: dict[str, str],
    meeting: dict[str, Any],
) -> None:
    at(clock, "16:30:00")
    go_live(client, settings, rooms, meeting)
    client.close()  # le service s'arrête

    at(clock, "18:00:00")  # … et revient après l'heure de fin
    restarted = create_app(
        settings=settings, clock=clock, rooms=rooms, sender=sender, run_workers=False
    )
    with TestClient(restarted) as again:
        tick(again)
        assert again.get(f"/v1/meetings/{meeting['id']}", headers=auth).json()["status"] == "ended"
    assert len(rooms.called("delete_room")) == 1


def test_a_restart_after_the_end_detects_the_meeting_is_already_over(
    settings: Settings,
    clock: FixedClock,
    rooms: FakeRoomGateway,
    sender: RecordingSender,
    client: TestClient,
    meeting: dict[str, Any],
) -> None:
    at(clock, "16:30:00")
    go_live(client, settings, rooms, meeting)
    at(clock, "18:00:00")
    tick(client)
    sent, deleted = len(sender.calls), len(rooms.called("delete_room"))
    client.close()

    restarted = create_app(
        settings=settings, clock=clock, rooms=rooms, sender=sender, run_workers=False
    )
    with TestClient(restarted) as again:
        tick(again)
        tick(again)
    assert len(sender.calls) == sent  # aucun événement en double
    assert len(rooms.called("delete_room")) == deleted


def test_if_the_media_server_is_down_the_close_is_retried_with_backoff(
    client: TestClient,
    auth: dict[str, str],
    clock: FixedClock,
    settings: Settings,
    rooms: FakeRoomGateway,
    db: Session,
    meeting: dict[str, Any],
) -> None:
    at(clock, "16:30:00")
    go_live(client, settings, rooms, meeting)
    room = next(iter(rooms.participants))
    rooms.failing = True
    at(clock, "18:00:00")
    tick(client)

    # la réunion est bien terminée en base, la fermeture est en attente (et n'est pas perdue)
    assert status_of(client, auth, meeting) == "ended"
    job = db.query(JobRow).filter(JobRow.kind == "close_room").one()
    assert job.status is JobStatus.FAILED and job.attempts == 1

    tick(client)  # trop tôt : pas de nouvelle tentative
    db.expire_all()
    assert db.query(JobRow).filter(JobRow.kind == "close_room").one().attempts == 1

    rooms.failing = False
    clock.set(clock.now() + timedelta(seconds=6))
    tick(client)
    db.expire_all()
    assert db.query(JobRow).filter(JobRow.kind == "close_room").one().status is JobStatus.DONE
    assert rooms.called("delete_room") == [(room,)]


def test_a_close_that_never_succeeds_is_abandoned_and_visible(
    client: TestClient,
    clock: FixedClock,
    settings: Settings,
    rooms: FakeRoomGateway,
    db: Session,
    meeting: dict[str, Any],
) -> None:
    at(clock, "16:30:00")
    go_live(client, settings, rooms, meeting)
    rooms.failing = True
    at(clock, "18:00:00")
    for _ in range(JOB_MAX_ATTEMPTS + 2):
        tick(client)
        clock.set(clock.now() + timedelta(hours=2))
    db.expire_all()
    job = db.query(JobRow).filter(JobRow.kind == "close_room").one()
    assert job.status is JobStatus.ABANDONED
    assert job.attempts == JOB_MAX_ATTEMPTS
    assert job.last_error
    assert db.query(MeetingRow).one().state is MeetingState.ENDED
