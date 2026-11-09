"""Passerelle vers LiveKit Egress (étape 13c) : ce que le backend ordonne à l'enregistrement.

Même principe que `services.rooms` : l'API « Twirp » de LiveKit (JSON sur HTTP), un appel `httpx`
authentifié par un jeton de courte durée, sans SDK. Le jeton porte le droit `roomRecord`.

L'étape 13c s'en sert pour vérifier que le service tourne et écrit sur le S3
(`uv run task egress-check`) ; l'étape 14 pour enregistrer les réunions (`start_room_composite`,
piloté par `services.recording`).

Les identifiants du S3 ne passent **pas** par ces appels : le service Egress les reçoit dans sa
propre configuration (`deploy/dev/.generated/egress.yaml`, générée par `docker-dev.ps1`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
import jwt

from visio.domain.errors import MediaServerError

SERVICE = "livekit.Egress"
TOKEN_TTL = timedelta(seconds=60)

# Réglages de l'enregistrement des réunions (DECISIONS.md, point 81) : la mise en page « speaker »
# met le partage d'écran au premier plan ; 720p à 30 images/s limite CPU et taille (mesure en 14d).
RECORDING_LAYOUT = "speaker"
RECORDING_PRESET = "H264_720P_30"

# `EgressStatus` dans le protocole LiveKit (valeur numérique -> nom court).
_STATUS_BY_NUMBER = {
    0: "starting",
    1: "active",
    2: "ending",
    3: "complete",
    4: "failed",
    5: "aborted",
    6: "limit_reached",
}
FINAL_STATUSES = frozenset({"complete", "failed", "aborted", "limit_reached"})


class EgressUnavailableError(MediaServerError):
    """Aucun service Egress n'a répondu (pas démarré, ou pas encore enregistré dans Redis)."""


@dataclass(frozen=True, slots=True)
class EgressInfo:
    egress_id: str
    status: str  # starting | active | ending | complete | failed | aborted | limit_reached
    error: str
    files: tuple[str, ...]  # chemins des fichiers produits (clés d'objet dans le S3)
    duration_seconds: int | None = None  # durée du fichier, si Egress l'a donnée
    started_at: datetime | None = None  # début réel du fichier (UTC), si Egress l'a donné

    @property
    def is_final(self) -> bool:
        return self.status in FINAL_STATUSES


class EgressGateway:
    """Implémentation réelle. `client` est injectable (tests avec un faux transport HTTP)."""

    def __init__(
        self,
        url: str,
        api_key: str,
        api_secret: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
        template_url: str = "",
        segment_seconds: int = 0,
    ) -> None:
        self._segment_seconds = segment_seconds
        self._template_url = template_url
        base = url.rstrip("/")
        for websocket, plain in (("wss://", "https://"), ("ws://", "http://")):
            if base.startswith(websocket):
                base = plain + base.removeprefix(websocket)
        self._base = base
        self._key = api_key
        self._secret = api_secret
        self._client = client or httpx.Client(timeout=timeout)

    def _token(self) -> str:
        now = datetime.now().astimezone()
        claims = {
            "iss": self._key,
            "nbf": int(now.timestamp()) - 5,
            "exp": int((now + TOKEN_TTL).timestamp()),
            "video": {"roomRecord": True},
        }
        return jwt.encode(claims, self._secret, algorithm="HS256")

    def _call(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}/twirp/{SERVICE}/{method}"
        try:
            response = self._client.post(
                url, json=body, headers={"Authorization": f"Bearer {self._token()}"}
            )
        except httpx.HTTPError as error:
            msg = f"le serveur de médias ne répond pas ({type(error).__name__})"
            raise MediaServerError(msg) from None
        if response.status_code == 503 or _twirp_code(response) == "unavailable":
            msg = "aucun service Egress disponible (démarré ? enregistré dans Redis ?)"
            raise EgressUnavailableError(msg)
        if response.status_code >= 400:
            detail = _twirp_message(response)
            msg = f"Egress a refusé l'ordre {method} ({response.status_code}){detail}"
            raise MediaServerError(msg)
        data: dict[str, Any] = response.json() if response.content else {}
        return data

    def list(
        self, egress_id: str | None = None, *, room_name: str | None = None, active: bool = False
    ) -> list[EgressInfo]:
        """Sans argument : tous les enregistrements en cours. Avec un id : celui-là (même fini).
        `room_name` restreint à une salle ; `active` aux enregistrements non terminés."""
        body: dict[str, Any] = {}
        if egress_id:
            body["egress_id"] = egress_id
        if room_name:
            body["room_name"] = room_name
        if active:
            body["active"] = True
        data = self._call("ListEgress", body)
        return [_info(item) for item in data.get("items", [])]

    def start_room_composite(self, room: str, key: str) -> EgressInfo:
        """Enregistre une salle en un seul MP4 « comme à l'écran » (audio de tous, vidéo,
        partage d'écran) dans `key` (chemin d'objet du S3 configuré côté Egress)."""
        body: dict[str, Any] = {
            "room_name": room,
            "layout": RECORDING_LAYOUT,
            "preset": RECORDING_PRESET,
        }
        if self._segment_seconds > 0:
            # Segments HLS envoyés au S3 au fil de l'eau, fusionnés ensuite en `key` (segments.py).
            body["segment_outputs"] = [
                {
                    "filename_prefix": key.removesuffix(".mp4") + "/seg",
                    "playlist_name": "seg.m3u8",
                    "segment_duration": self._segment_seconds,
                }
            ]
        else:
            body["file_outputs"] = [{"file_type": "MP4", "filepath": key}]
        if not self._template_url:
            msg = "gabarit d'enregistrement manquant (RECORDING_TEMPLATE_URL) : refusé"
            raise MediaServerError(msg)
        # Mise en page maison (`/recording-template`, façon réunion : grille, partage en scène).
        # Toujours appliquée : aucun repli sur la mise en page par défaut de LiveKit.
        body["custom_base_url"] = self._template_url
        return _info(self._call("StartRoomCompositeEgress", body))

    def start_track_audio(self, room: str, track_sid: str, key: str) -> EgressInfo:
        """Enregistre **une seule piste** (le micro d'un participant) dans `key`, sans transcodage
        (Opus dans un fichier OGG). C'est ce qui permet d'attribuer la parole à une personne."""
        body: dict[str, Any] = {
            "room_name": room,
            "track_id": track_sid,
            "file": {"filepath": key},
        }
        return _info(self._call("StartTrackEgress", body))

    def start_web_to_file(self, url: str, key: str) -> EgressInfo:
        """Enregistre une page web dans un fichier du S3 configuré côté Egress (`key` = chemin)."""
        data = self._call("StartWebEgress", {"url": url, "file_outputs": [{"filepath": key}]})
        return _info(data)

    def stop(self, egress_id: str) -> EgressInfo:
        return _info(self._call("StopEgress", {"egress_id": egress_id}))


class RecorderGateway(Protocol):
    """Ce que le reste du code demande à Egress (les tests y mettent `FakeRecorder`)."""

    def start_room_composite(self, room: str, key: str) -> EgressInfo: ...

    def start_track_audio(self, room: str, track_sid: str, key: str) -> EgressInfo: ...

    def stop(self, egress_id: str) -> EgressInfo: ...

    def list(
        self, egress_id: str | None = None, *, room_name: str | None = None, active: bool = False
    ) -> list[EgressInfo]: ...


@dataclass
class FakeRecorder:
    """Faux Egress pour les tests : note les ordres, peut être indisponible ou en échec.

    `journal` (optionnel) est partagé avec d'autres faux pour vérifier l'ordre des ordres."""

    started: list[tuple[str, str]] = field(default_factory=list)
    tracks_started: list[tuple[str, str, str]] = field(default_factory=list)  # salle, piste, clé
    stopped: list[str] = field(default_factory=list)
    infos: dict[str, EgressInfo] = field(default_factory=dict)
    rooms: dict[str, str] = field(default_factory=dict)  # egress_id -> salle
    unavailable: bool = False
    failing_stop: bool = False
    journal: list[str] | None = None
    _counter: int = 0

    def _note(self, entry: str) -> None:
        if self.journal is not None:
            self.journal.append(entry)

    def start_room_composite(self, room: str, key: str) -> EgressInfo:
        if self.unavailable:
            msg = "aucun service Egress disponible (simulé)"
            raise EgressUnavailableError(msg)
        self._counter += 1
        info = EgressInfo(f"EG_fake{self._counter}", "active", "", ())
        self.started.append((room, key))
        self.infos[info.egress_id] = info
        self.rooms[info.egress_id] = room
        self._note("recorder.start")
        return info

    def start_track_audio(self, room: str, track_sid: str, key: str) -> EgressInfo:
        if self.unavailable:
            msg = "aucun service Egress disponible (simulé)"
            raise EgressUnavailableError(msg)
        self._counter += 1
        info = EgressInfo(f"EG_fake{self._counter}", "active", "", ())
        self.tracks_started.append((room, track_sid, key))
        self.infos[info.egress_id] = info
        self.rooms[info.egress_id] = room
        self._note("recorder.start_track")
        return info

    def finish(self, egress_id: str, status: str = "complete", error: str = "") -> EgressInfo:
        """Simule la fin d'un enregistrement côté Egress (état final, avec ou sans erreur)."""
        info = EgressInfo(egress_id, status, error, (), 60 if status == "complete" else None)
        self.infos[egress_id] = info
        return info

    def stop(self, egress_id: str) -> EgressInfo:
        self._note("recorder.stop")
        if self.failing_stop:
            msg = "Egress a refusé l'ordre StopEgress (simulé)"
            raise MediaServerError(msg)
        self.stopped.append(egress_id)
        info = EgressInfo(egress_id, "ending", "", ())
        self.infos[egress_id] = info
        return info

    def list(
        self, egress_id: str | None = None, *, room_name: str | None = None, active: bool = False
    ) -> list[EgressInfo]:
        found = list(self.infos.values())
        if egress_id:
            found = [i for i in found if i.egress_id == egress_id]
        if room_name:
            found = [i for i in found if self.rooms.get(i.egress_id) == room_name]
        if active:
            found = [i for i in found if not i.is_final and i.egress_id not in self.stopped]
        return found


def _pick(item: dict[str, Any], *names: str) -> Any:
    """Le JSON de Twirp peut être en camelCase ou en snake_case : on accepte les deux."""
    for name in names:
        if name in item:
            return item[name]
    return None


def _status(value: Any) -> str:
    if isinstance(value, int):
        return _STATUS_BY_NUMBER.get(value, "unknown")
    return str(value or "").lower().removeprefix("egress_") or "unknown"


def _info(item: dict[str, Any]) -> EgressInfo:
    results = _pick(item, "file_results", "fileResults") or []
    files = tuple(
        str(name)
        for result in results
        if (name := _pick(result, "filename", "location")) is not None
    )
    return EgressInfo(
        egress_id=str(_pick(item, "egress_id", "egressId") or ""),
        status=_status(item.get("status")),
        error=str(item.get("error") or ""),
        files=files,
        duration_seconds=_duration_seconds(results),
        started_at=_started_at(results),
    )


def _duration_seconds(results: list[dict[str, Any]]) -> int | None:
    """Durée du premier fichier. Twirp donne les entiers 64 bits en chaînes, en nanosecondes."""
    for result in results:
        raw = _pick(result, "duration")
        try:
            nanoseconds = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            continue
        if nanoseconds > 0:
            return nanoseconds // 1_000_000_000
    return None


def _started_at(results: list[dict[str, Any]]) -> datetime | None:
    """Début du premier fichier (nanosecondes depuis 1970, en chaîne dans le JSON de Twirp)."""
    for result in results:
        raw = _pick(result, "started_at", "startedAt")
        try:
            nanoseconds = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            continue
        if nanoseconds > 0:
            return datetime.fromtimestamp(nanoseconds / 1_000_000_000, UTC)
    return None


def info_from_json(item: dict[str, Any]) -> EgressInfo:
    """`EgressInfo` (webhook `egress_ended` ou réponse de `ListEgress`) vu par le reste du code."""
    return _info(item)


def _twirp_code(response: httpx.Response) -> str | None:
    try:
        code = response.json().get("code")
    except ValueError:
        return None
    return str(code) if code else None


def _twirp_message(response: httpx.Response) -> str:
    try:
        message = response.json().get("msg")
    except ValueError:
        return ""
    return f" : {message}" if message else ""
