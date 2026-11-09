"""Webhooks sortants signés (étape 12) : format, signature, relances, reprise après redémarrage."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.integration.conftest import RecordingSender, meeting_body
from tests.integration.helpers import at, claims_of, join, livekit_webhook, tick, utc_seconds
from visio.config import Settings
from visio.db.models import WebhookAttemptRow, WebhookDeliveryRow, WebhookStatus
from visio.domain.clock import FixedClock
from visio.main import create_app
from visio.services.rooms import FakeRoomGateway, LiveKitGateway
from visio.services.webhooks import (
    MAX_ATTEMPTS,
    RETRY_DELAYS,
    SendResult,
    verify_signature,
)


@pytest.fixture
def meeting(client: TestClient, auth: dict[str, str], clock: FixedClock) -> dict[str, Any]:
    body = meeting_body(waiting_room=False, early_join_minutes=0)
    response = client.post("/v1/meetings", json=body, headers=auth)
    assert response.status_code == 201
    at(clock, "16:00:00")
    created: dict[str, Any] = response.json()
    return created


def one_event(client: TestClient, settings: Settings, meeting: dict[str, Any]) -> None:
    claims = claims_of(settings, join(client, meeting["organizer_urls"][0]).json()["livekit_token"])
    livekit_webhook(
        client, settings, "participant_joined", claims,
        created_at=utc_seconds("16:00:00"), joined_at=utc_seconds("16:00:00"),
    )  # fmt: skip


def test_each_call_is_signed_and_carries_the_metadata(
    client: TestClient,
    clock: FixedClock,
    settings: Settings,
    sender: RecordingSender,
    meeting: dict[str, Any],
) -> None:
    one_event(client, settings, meeting)
    tick(client)

    assert len(sender.calls) == 2  # meeting.started puis participant.joined
    secret = settings.webhook_secret.get_secret_value()
    for url, headers, body in sender.calls:
        assert url == settings.webhook_url
        assert verify_signature(
            secret,
            body,
            timestamp=headers["X-Visio-Timestamp"],
            signature=headers["X-Visio-Signature"],
            now=clock.now().timestamp(),
        )
        event = json.loads(body)
        assert headers["X-Visio-Event-Id"] == event["id"]
        assert headers["X-Visio-Event-Type"] == event["type"]
        assert event["meeting"]["id"] == meeting["id"]
        assert event["meeting"]["metadata"] == {"eduvia_session_id": 1234}  # tel quel
        assert event["meeting"]["title"] == "Cours BTS MCO – Groupe A"
    started, joined = (json.loads(b) for _, _, b in sender.calls)
    assert started["type"] == "meeting.started"
    assert joined["type"] == "participant.joined"
    assert joined["data"]["participant"] == {
        "display_name": "Marie Durand",
        "external_id": "staff_42",
        "role": "organizer",
    }
    assert started["id"] != joined["id"]


def test_a_tampered_delivery_would_not_verify(
    client: TestClient, clock: FixedClock, settings: Settings, sender: RecordingSender,
    meeting: dict[str, Any],
) -> None:  # fmt: skip
    one_event(client, settings, meeting)
    tick(client)
    _, headers, body = sender.calls[0]
    assert not verify_signature(
        settings.webhook_secret.get_secret_value(),
        body.replace(b"meeting.started", b"meeting.ended"),
        timestamp=headers["X-Visio-Timestamp"],
        signature=headers["X-Visio-Signature"],
        now=clock.now().timestamp(),
    )


def test_delivered_events_are_not_sent_again(
    client: TestClient, settings: Settings, sender: RecordingSender, db: Session,
    meeting: dict[str, Any],
) -> None:  # fmt: skip
    one_event(client, settings, meeting)
    tick(client)
    tick(client)
    assert len(sender.calls) == 2
    db.expire_all()
    assert {d.status for d in db.query(WebhookDeliveryRow)} == {WebhookStatus.DELIVERED}


def test_a_failing_receiver_is_retried_at_growing_delays_then_abandoned(
    client: TestClient,
    clock: FixedClock,
    settings: Settings,
    sender: RecordingSender,
    db: Session,
    meeting: dict[str, Any],
) -> None:
    claims = claims_of(
        settings, join(client, meeting["participant_url"], display_name="A").json()["livekit_token"]
    )  # noqa: E501
    sender.responses = [SendResult(500, None)] * 200  # le récepteur est en panne
    livekit_webhook(
        client, settings, "participant_joined", claims,
        created_at=utc_seconds("16:00:00"), joined_at=utc_seconds("16:00:00"),
    )  # fmt: skip
    # on ne suit que l'événement `participant.joined` : on retire meeting.started pour l'isoler
    db.query(WebhookDeliveryRow).filter_by(event_type="meeting.started").delete()
    db.commit()

    start = clock.now()
    attempt_times = []
    for _ in range(MAX_ATTEMPTS + 3):
        before = len(
            [c for c in sender.calls if c[1]["X-Visio-Event-Type"] == "participant.joined"]
        )
        tick(client)
        if (
            len([c for c in sender.calls if c[1]["X-Visio-Event-Type"] == "participant.joined"])
            > before
        ):
            attempt_times.append(clock.now() - start)
        clock.set(clock.now() + timedelta(seconds=5))
        # avance jusqu'à la prochaine échéance connue
        db.expire_all()
        row = db.query(WebhookDeliveryRow).filter_by(event_type="participant.joined").one()
        if row.status is not WebhookStatus.PENDING:
            break
        if row.next_attempt_at > clock.now():
            clock.set(row.next_attempt_at)

    gaps = [b - a for a, b in zip(attempt_times, attempt_times[1:], strict=False)]
    assert gaps == list(RETRY_DELAYS)  # 10 s, 1 min, 5 min, 30 min, 2 h
    db.expire_all()
    row = db.query(WebhookDeliveryRow).filter_by(event_type="participant.joined").one()
    assert row.status is WebhookStatus.ABANDONED
    assert row.attempts == MAX_ATTEMPTS == 6
    assert row.last_error and "500" in row.last_error
    journal = (
        db.query(WebhookAttemptRow)
        .filter_by(delivery_id=row.id)
        .order_by(WebhookAttemptRow.attempted_at)
        .all()
    )
    assert [a.status_code for a in journal] == [500] * 6  # journal des tentatives
    # une même identité d'événement à chaque tentative (idempotence côté Eduvia)
    joined_calls = [c for c in sender.calls if c[1]["X-Visio-Event-Type"] == "participant.joined"]
    assert len({h["X-Visio-Event-Id"] for _, h, _ in joined_calls}) == 1
    # et le timestamp signé est celui de *chaque* tentative (sinon l'anti-rejeu la refuserait)
    stamps = [int(h["X-Visio-Timestamp"]) for _, h, _ in joined_calls]
    assert stamps == sorted(set(stamps))


def test_a_network_error_is_retried_and_a_later_success_stops_the_retries(
    client: TestClient, clock: FixedClock, settings: Settings, sender: RecordingSender,
    db: Session, meeting: dict[str, Any],
) -> None:  # fmt: skip
    sender.responses = [SendResult(None, "ConnectError: refusé"), SendResult(503, None)]
    one_event(client, settings, meeting)
    db.query(WebhookDeliveryRow).filter_by(event_type="meeting.started").delete()
    db.commit()

    tick(client)  # échec réseau
    db.expire_all()
    row = db.query(WebhookDeliveryRow).one()
    assert (row.status, row.attempts, row.last_error) == (
        WebhookStatus.PENDING,
        1,
        "ConnectError: refusé",
    )
    tick(client)  # trop tôt (10 s)
    assert len(sender.calls) == 1
    clock.set(clock.now() + timedelta(seconds=10))
    tick(client)  # 503
    clock.set(clock.now() + timedelta(minutes=1))
    tick(client)  # 200
    db.expire_all()
    row = db.query(WebhookDeliveryRow).one()
    assert (row.status, row.attempts, row.last_error) == (WebhookStatus.DELIVERED, 3, None)
    assert row.delivered_at is not None
    tick(client)
    assert len(sender.calls) == 3


@pytest.mark.parametrize("status", [301, 400, 401, 404, 410, 500, 502])
def test_anything_but_2xx_is_a_failure(
    client: TestClient, settings: Settings, sender: RecordingSender, db: Session,
    meeting: dict[str, Any], status: int,
) -> None:  # fmt: skip
    sender.responses = [SendResult(status, None)] * 2
    one_event(client, settings, meeting)
    tick(client)
    db.expire_all()
    assert {d.status for d in db.query(WebhookDeliveryRow)} == {WebhookStatus.PENDING}


def test_a_restart_during_the_retries_resumes_them(
    client: TestClient,
    clock: FixedClock,
    settings: Settings,
    rooms: FakeRoomGateway,
    sender: RecordingSender,
    meeting: dict[str, Any],
) -> None:
    sender.responses = [SendResult(500, None)] * 2
    one_event(client, settings, meeting)
    tick(client)  # les deux événements échouent une première fois
    assert len(sender.calls) == 2
    client.close()  # le service s'arrête

    clock.set(clock.now() + timedelta(seconds=11))
    restarted = create_app(
        settings=settings, clock=clock, rooms=rooms, sender=sender, run_workers=False
    )
    with TestClient(restarted) as again:
        tick(again)  # la file persistante est reprise, l'ordre d'origine est conservé
    assert len(sender.calls) == 4
    assert [json.loads(b)["type"] for _, _, b in sender.calls[2:]] == [
        "meeting.started",
        "participant.joined",
    ]
    assert sender.calls[2][1]["X-Visio-Event-Id"] == sender.calls[0][1]["X-Visio-Event-Id"]


def test_the_event_is_written_in_the_same_transaction_as_the_change(
    client: TestClient, settings: Settings, db: Session, meeting: dict[str, Any]
) -> None:
    """L'événement est en base dès que le changement l'est, avant toute livraison : un arrêt
    brutal entre les deux ne peut pas le perdre."""
    one_event(client, settings, meeting)  # pas de tick : rien n'est encore parti
    assert db.query(WebhookDeliveryRow).count() == 2


# --- passerelle vers le serveur de médias (appels HTTP réels simulés) --------------------------


def gateway_with(handler: Any) -> LiveKitGateway:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return LiveKitGateway("ws://localhost:7880", "devkey", "s" * 40, client=client)


def test_the_gateway_speaks_twirp_with_an_admin_token_limited_to_the_room() -> None:
    import jwt

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    gateway = gateway_with(handler)
    gateway.remove_participant("rm_x", "pg_1")
    gateway.mute_track("rm_x", "pg_1", "TR_1", muted=True)
    gateway.delete_room("rm_x")
    gateway.send_data("rm_x", b'{"a":1}', topic="chat")

    paths = [r.url.path for r in seen]
    assert paths == [
        "/twirp/livekit.RoomService/RemoveParticipant",
        "/twirp/livekit.RoomService/MutePublishedTrack",
        "/twirp/livekit.RoomService/DeleteRoom",
        "/twirp/livekit.RoomService/SendData",
    ]
    assert str(seen[0].url).startswith("http://localhost:7880/")  # ws:// -> http://
    assert json.loads(seen[0].content) == {"room": "rm_x", "identity": "pg_1"}
    assert json.loads(seen[1].content) == {
        "room": "rm_x", "identity": "pg_1", "track_sid": "TR_1", "muted": True,
    }  # fmt: skip
    assert json.loads(seen[3].content)["topic"] == "chat"
    claims = jwt.decode(
        seen[0].headers["Authorization"].removeprefix("Bearer "),
        "s" * 40,
        algorithms=["HS256"],
    )
    assert claims["iss"] == "devkey"
    assert claims["video"]["roomAdmin"] is True and claims["video"]["room"] == "rm_x"


def test_the_gateway_maps_participants_tracks_and_roles() -> None:
    payload = {
        "participants": [
            {
                "sid": "PA_1",
                "identity": "pg_1",
                "name": "Lina",
                "metadata": json.dumps({"role": "participant", "external_id": None}),
                "tracks": [
                    {"sid": "TR_a", "source": "MICROPHONE", "muted": False},
                    {"sid": "TR_b", "source": 3, "muted": True},
                ],
            }
        ]
    }
    gateway = gateway_with(lambda request: httpx.Response(200, json=payload))
    [lina] = gateway.list_participants("rm_x")
    assert (lina.identity, lina.name, lina.role) == ("pg_1", "Lina", "participant")
    assert [(t.sid, t.source, t.muted) for t in lina.tracks] == [
        ("TR_a", "microphone", False),
        ("TR_b", "screen_share", True),
    ]


def test_the_gateway_turns_failures_into_a_clean_error_and_ignores_gone_rooms() -> None:
    from visio.domain.errors import MediaServerError

    down = gateway_with(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refusé")))
    with pytest.raises(MediaServerError):
        down.delete_room("rm_x")
    refused = gateway_with(lambda r: httpx.Response(401, json={"code": "unauthenticated"}))
    with pytest.raises(MediaServerError):
        refused.delete_room("rm_x")
    gone = gateway_with(lambda r: httpx.Response(404, json={"code": "not_found"}))
    gone.delete_room("rm_x")  # salle déjà fermée : sans objet, pas une erreur
    gone.remove_participant("rm_x", "pg_1")
