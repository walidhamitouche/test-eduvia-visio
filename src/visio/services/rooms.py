"""Passerelle vers le serveur de médias (étapes 8 à 11) : ce que le backend *ordonne* à LiveKit.

Les ordres passent par l'API « Twirp » de LiveKit (JSON sur HTTP) : un simple appel `httpx`
authentifié par un jeton à droits d'administration, sans SDK ni dépendance de plus (les jetons
sont déjà fabriqués avec PyJWT, voir `livekit_tokens`). Le reste du code ne connaît que le
protocole `RoomGateway` : les tests y mettent un faux, le réel parle à LiveKit.

Aucune de ces méthodes ne décide de qui a le droit de quoi : l'autorisation est vérifiée avant,
dans les services (`room_admin`), à partir du lien de l'appelant.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx
import jwt

from visio.domain.errors import MediaServerError

SERVICE = "livekit.RoomService"
ADMIN_TOKEN_TTL = timedelta(seconds=60)

# Valeurs de `TrackSource` dans le protocole LiveKit.
SOURCE_BY_NAME = {"camera": 1, "microphone": 2, "screen_share": 3, "screen_share_audio": 4}
SOURCE_NAMES = {1: "camera", 2: "microphone", 3: "screen_share", 4: "screen_share_audio"}


@dataclass(frozen=True, slots=True)
class RoomTrack:
    sid: str
    source: str  # camera | microphone | screen_share | screen_share_audio | unknown
    muted: bool


@dataclass(frozen=True, slots=True)
class RoomParticipant:
    sid: str
    identity: str
    name: str
    role: str | None  # lu dans les métadonnées du jeton que nous avons signé
    tracks: tuple[RoomTrack, ...] = ()


class RoomGateway(Protocol):
    def ping(self) -> None:
        """Sonde de santé (étape 17b) : lève `MediaServerError` si le serveur ne répond pas."""
        ...

    def list_participants(self, room: str) -> list[RoomParticipant]: ...

    def mute_track(self, room: str, identity: str, track_sid: str, *, muted: bool) -> None: ...

    def remove_participant(self, room: str, identity: str) -> None: ...

    def delete_room(self, room: str) -> None: ...

    def send_data(
        self, room: str, payload: bytes, *, topic: str, identities: list[str] | None = None
    ) -> None: ...


class LiveKitGateway:
    """Implémentation réelle. `client` est injectable (tests avec un faux transport HTTP)."""

    def __init__(
        self,
        url: str,
        api_key: str,
        api_secret: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 5.0,
    ) -> None:
        base = url.rstrip("/")
        for websocket, plain in (("wss://", "https://"), ("ws://", "http://")):
            if base.startswith(websocket):
                base = plain + base.removeprefix(websocket)
        self._base = base
        self._key = api_key
        self._secret = api_secret
        self._client = client or httpx.Client(timeout=timeout)

    def _admin_token(self, room: str | None) -> str:
        now = datetime.now().astimezone()
        grant: dict[str, Any] = {"roomAdmin": True, "roomCreate": True, "roomList": True}
        if room is not None:
            grant["room"] = room
        claims = {
            "iss": self._key,
            "nbf": int(now.timestamp()) - 5,
            "exp": int((now + ADMIN_TOKEN_TTL).timestamp()),
            "video": grant,
        }
        return jwt.encode(claims, self._secret, algorithm="HS256")

    def _call(self, method: str, room: str | None, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}/twirp/{SERVICE}/{method}"
        try:
            response = self._client.post(
                url, json=body, headers={"Authorization": f"Bearer {self._admin_token(room)}"}
            )
        except httpx.HTTPError as error:
            msg = f"le serveur de médias ne répond pas ({type(error).__name__})"
            raise MediaServerError(msg) from None
        if response.status_code == 404 and _twirp_code(response) == "not_found":
            return {}  # salle ou personne déjà partie : l'ordre est sans objet
        if response.status_code >= 400:
            msg = f"le serveur de médias a refusé l'ordre {method} ({response.status_code})"
            raise MediaServerError(msg)
        data: dict[str, Any] = response.json() if response.content else {}
        return data

    def ping(self) -> None:
        # `ListRooms` : l'appel authentifié le plus léger (aucune salle n'est créée ni modifiée).
        self._call("ListRooms", None, {})

    def list_participants(self, room: str) -> list[RoomParticipant]:
        data = self._call("ListParticipants", room, {"room": room})
        return [_participant(item) for item in data.get("participants", [])]

    def mute_track(self, room: str, identity: str, track_sid: str, *, muted: bool) -> None:
        self._call(
            "MutePublishedTrack",
            room,
            {"room": room, "identity": identity, "track_sid": track_sid, "muted": muted},
        )

    def remove_participant(self, room: str, identity: str) -> None:
        self._call("RemoveParticipant", room, {"room": room, "identity": identity})

    def delete_room(self, room: str) -> None:
        self._call("DeleteRoom", room, {"room": room})

    def send_data(
        self, room: str, payload: bytes, *, topic: str, identities: list[str] | None = None
    ) -> None:
        body: dict[str, Any] = {
            "room": room,
            "data": base64.b64encode(payload).decode(),
            "kind": 0,  # RELIABLE
            "topic": topic,
        }
        if identities:
            body["destination_identities"] = identities
        self._call("SendData", room, body)


def _twirp_code(response: httpx.Response) -> str | None:
    try:
        code = response.json().get("code")
    except ValueError:
        return None
    return str(code) if code else None


def _participant(item: dict[str, Any]) -> RoomParticipant:
    role: str | None = None
    try:
        role = json.loads(item.get("metadata") or "{}").get("role")
    except (ValueError, AttributeError):
        role = None
    tracks = tuple(
        RoomTrack(
            sid=str(track.get("sid", "")),
            source=_source(track.get("source")),
            muted=bool(track.get("muted", False)),
        )
        for track in item.get("tracks", [])
    )
    return RoomParticipant(
        sid=str(item.get("sid", "")),
        identity=str(item.get("identity", "")),
        name=str(item.get("name", "")),
        role=role,
        tracks=tracks,
    )


def _source(value: Any) -> str:
    if isinstance(value, int):
        return SOURCE_NAMES.get(value, "unknown")
    name = str(value or "").lower()
    return name if name in SOURCE_BY_NAME else "unknown"


@dataclass
class FakeRoomGateway:
    """Faux serveur de médias pour les tests : garde les personnes présentes et note les ordres."""

    participants: dict[str, list[RoomParticipant]] = field(default_factory=dict)
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    failing: bool = False

    def _check(self) -> None:
        if self.failing:
            msg = "le serveur de médias ne répond pas (simulé)"
            raise MediaServerError(msg)

    def add(self, room: str, participant: RoomParticipant) -> None:
        self.participants.setdefault(room, []).append(participant)

    def ping(self) -> None:
        self._check()

    def list_participants(self, room: str) -> list[RoomParticipant]:
        self._check()
        return list(self.participants.get(room, []))

    def mute_track(self, room: str, identity: str, track_sid: str, *, muted: bool) -> None:
        self._check()
        self.calls.append(("mute", (room, identity, track_sid, muted)))

    def remove_participant(self, room: str, identity: str) -> None:
        self._check()
        self.calls.append(("remove", (room, identity)))
        self.participants[room] = [
            p for p in self.participants.get(room, []) if p.identity != identity
        ]

    def delete_room(self, room: str) -> None:
        self._check()
        self.calls.append(("delete_room", (room,)))
        self.participants.pop(room, None)

    def send_data(
        self, room: str, payload: bytes, *, topic: str, identities: list[str] | None = None
    ) -> None:
        self._check()
        self.calls.append(("send_data", (room, topic, json.loads(payload), identities)))

    def called(self, name: str) -> list[tuple[Any, ...]]:
        return [args for call, args in self.calls if call == name]
