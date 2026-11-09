"""Aides communes aux tests de la salle (modération, salle d'attente, présence, chat)."""

from __future__ import annotations

import json
from typing import Any

import jwt
from fastapi.testclient import TestClient

from visio.config import Settings
from visio.domain.clock import FixedClock
from visio.domain.slot import parse_iso8601
from visio.services.rooms import FakeRoomGateway, RoomParticipant, RoomTrack

DAY = "2027-01-02"
TRACKS = ("microphone", "camera")


def at(clock: FixedClock, time: str) -> None:
    """Place l'horloge simulée à `time` (heure de Paris) le 2 janvier 2027."""
    clock.set(parse_iso8601(f"{DAY}T{time}+01:00"))


def token_of(url: str) -> str:
    return url.rsplit("/j/", 1)[1]


def join(client: TestClient, url_or_token: str, **extra: Any) -> Any:
    token = token_of(url_or_token) if "/j/" in url_or_token else url_or_token
    # Chaque appel simule un navigateur à part : le cookie d'identité posé par le serveur n'est
    # pas rejoué (un même navigateur le rejoue : voir test_moderation, test d'exclusion).
    client.cookies.clear()
    return client.post("/v1/join", json={"token": token, **extra})


def error_code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def claims_of(settings: Settings, livekit_token: str) -> dict[str, Any]:
    decoded: dict[str, Any] = jwt.decode(
        livekit_token,
        settings.livekit_api_secret.get_secret_value(),
        algorithms=["HS256"],
        options={"verify_exp": False, "verify_nbf": False, "verify_iat": False},
    )
    return decoded


def seat(
    rooms: FakeRoomGateway,
    claims: dict[str, Any],
    *,
    tracks: tuple[str, ...] = TRACKS,
    sid: str | None = None,
) -> RoomParticipant:
    """Installe dans le faux serveur de médias la personne que décrit un jeton émis."""
    metadata = json.loads(claims["metadata"])
    identity = str(claims["sub"])
    person = RoomParticipant(
        sid=sid or f"PA_{identity[-6:]}",
        identity=identity,
        name=str(claims["name"]),
        role=metadata["role"],
        tracks=tuple(
            RoomTrack(sid=f"TR_{identity[-6:]}_{t}", source=t, muted=False) for t in tracks
        ),
    )
    rooms.add(str(claims["video"]["room"]), person)
    return person


def enter(
    client: TestClient,
    settings: Settings,
    rooms: FakeRoomGateway,
    url: str,
    **extra: Any,
) -> RoomParticipant:
    """Rejoint par `POST /v1/join` (doit réussir) et s'installe dans la salle simulée."""
    response = join(client, url, **extra)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "admitted", body
    return seat(rooms, claims_of(settings, body["livekit_token"]))


def post_room(client: TestClient, route: str, url: str, **body: Any) -> Any:
    return client.post(f"/v1/room/{route}", json={"token": token_of(url), **body})


# --- planificateur et webhooks ------------------------------------------------------------------


def tick(client: TestClient) -> None:
    """Un tour du planificateur avec l'horloge simulée (fin de créneau, fermeture de salle,
    livraison des webhooks), comme le ferait la boucle en tâche de fond."""
    from visio.workers.scheduler import run_once

    state = client.app.state  # type: ignore[attr-defined]
    run_once(
        state.session_factory,
        state.settings,
        state.rooms,
        state.recorder,
        state.webhook_sender,
        state.clock,
        state.storage,
    )


def livekit_webhook(
    client: TestClient,
    settings: Settings,
    event: str,
    claims: dict[str, Any] | None,
    *,
    sid: str = "PA_one",
    room: str | None = None,
    created_at: float | None = None,
    joined_at: float | None = None,
    identity: str | None = None,
    key: str | None = None,
    secret: str | None = None,
    tamper: bool = False,
) -> Any:
    """Envoie un webhook comme le ferait LiveKit : corps JSON + jeton signé portant l'empreinte
    du corps (`sha256`). `claims` est le jeton de salle émis par `/v1/join`."""
    import base64
    import hashlib
    import time

    participant: dict[str, Any] = {"sid": sid}
    if claims is not None:
        participant |= {
            "identity": claims["sub"],
            "name": claims["name"],
            "metadata": claims["metadata"],
        }
        room = room or claims["video"]["room"]
    if identity is not None:
        participant["identity"] = identity
    now = created_at if created_at is not None else time.time()
    if joined_at is not None:
        participant["joinedAt"] = int(joined_at)
    body = json.dumps(
        {
            "event": event,
            "room": {"name": room or "rm_inconnue"},
            "participant": participant,
            "createdAt": int(now),
            "id": "EV_x",
        }
    ).encode()
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    signed = jwt.encode(
        {"iss": key or settings.livekit_api_key, "sha256": digest, "exp": int(time.time()) + 300},
        secret or settings.livekit_api_secret.get_secret_value(),
        algorithm="HS256",
    )
    if tamper:
        body = body.replace(b'"event"', b'"evenT"')
    return client.post(
        "/v1/livekit/webhook",
        content=body,
        headers={"Authorization": signed, "Content-Type": "application/webhook+json"},
    )


def utc_seconds(clock_time: str) -> float:
    """Heure de Paris du 2 janvier 2027 -> secondes UTC (pour les événements LiveKit)."""
    return parse_iso8601(f"{DAY}T{clock_time}+01:00").timestamp()


def egress_ended_webhook(
    client: TestClient,
    settings: Settings,
    egress_id: str,
    *,
    status: str = "EGRESS_COMPLETE",
    error: str = "",
    duration_seconds: int = 90,
) -> Any:
    """Webhook `egress_ended` tel que LiveKit l'envoie (même signature que les autres)."""
    import base64
    import hashlib
    import time

    info: dict[str, Any] = {
        "egressId": egress_id,
        "status": status,
        "error": error,
        "fileResults": [{"filename": "x.mp4", "duration": str(duration_seconds * 10**9)}],
    }
    body = json.dumps({"event": "egress_ended", "egressInfo": info, "id": "EV_eg"}).encode()
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    signed = jwt.encode(
        {
            "iss": settings.livekit_api_key,
            "sha256": digest,
            "exp": int(time.time()) + 300,
        },
        settings.livekit_api_secret.get_secret_value(),
        algorithm="HS256",
    )
    return client.post(
        "/v1/livekit/webhook",
        content=body,
        headers={"Authorization": signed, "Content-Type": "application/webhook+json"},
    )


def signed_event(client: TestClient, settings: Settings, payload: dict[str, Any]) -> Any:
    """Envoie un événement LiveKit quelconque, signé comme le fait LiveKit."""
    import base64
    import hashlib
    import time

    body = json.dumps(payload).encode()
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    signed = jwt.encode(
        {"iss": settings.livekit_api_key, "sha256": digest, "exp": int(time.time()) + 300},
        settings.livekit_api_secret.get_secret_value(),
        algorithm="HS256",
    )
    return client.post(
        "/v1/livekit/webhook",
        content=body,
        headers={"Authorization": signed, "Content-Type": "application/webhook+json"},
    )


def track_published_webhook(
    client: TestClient,
    settings: Settings,
    claims: dict[str, Any],
    *,
    track_sid: str = "TR_mic1",
    source: str | int = "MICROPHONE",
    kind: str | int = "AUDIO",
    created_at: float | None = None,
) -> Any:
    """Webhook `track_published` : la personne décrite par `claims` publie une piste."""
    return signed_event(
        client,
        settings,
        {
            "event": "track_published",
            "room": {"name": claims["video"]["room"]},
            "participant": {
                "sid": "PA_1",
                "identity": claims["sub"],
                "name": claims["name"],
                "metadata": claims["metadata"],
            },
            "track": {"sid": track_sid, "type": kind, "source": source},
            "createdAt": int(created_at if created_at is not None else 1_800_000_000),
            "id": "EV_t",
        },
    )


def track_egress_ended_webhook(
    client: TestClient,
    settings: Settings,
    egress_id: str,
    *,
    status: str = "EGRESS_COMPLETE",
    error: str = "",
    started_at: float | None = None,
    filename: str = "x.ogg",
) -> Any:
    """`egress_ended` d'une piste audio (avec le début réel du fichier, en nanosecondes)."""
    result: dict[str, Any] = {"filename": filename, "duration": str(60 * 10**9)}
    if started_at is not None:
        result["startedAt"] = str(int(started_at * 10**9))
    return signed_event(
        client,
        settings,
        {
            "event": "egress_ended",
            "egressInfo": {
                "egressId": egress_id,
                "status": status,
                "error": error,
                "fileResults": [result],
            },
            "id": "EV_tg",
        },
    )
