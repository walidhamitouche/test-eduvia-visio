"""Réception des webhooks du serveur de médias : présence et première entrée (étape 11), fin
d'enregistrement `egress_ended` (étape 14b), publication d'un micro `track_published` (16b).

Route appelée par LiveKit lui-même, pas par un navigateur ni par Eduvia. Elle est protégée par la
**signature** de LiveKit (jeton JWT HS256 dans `Authorization`, signé avec `LIVEKIT_API_SECRET`,
dont la revendication `sha256` doit être l'empreinte du corps reçu) : sans elle, `401`. Elle ne
passe pas par `require_https` : l'appel vient du réseau interne, la signature fait foi.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import jwt
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from visio.api.deps import AppClock, AppSettings, DbSession, Objects, Rooms
from visio.api.errors import ApiError
from visio.services import presence, recording, tracks

router = APIRouter(include_in_schema=False)


def verify_livekit_signature(
    authorization: str | None, body: bytes, *, api_key: str, api_secret: str
) -> None:
    """Lève `ApiError(401)` si l'appel ne vient pas de notre serveur de médias."""
    refuse = ApiError(401, "unauthorized", "signature du webhook absente ou invalide")
    if not authorization:
        raise refuse
    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims: dict[str, Any] = jwt.decode(
            token, api_secret, algorithms=["HS256"], options={"verify_aud": False}
        )
    except jwt.PyJWTError:
        raise refuse from None
    if claims.get("iss") != api_key:
        raise refuse
    expected = base64.b64encode(hashlib.sha256(body).digest()).decode()
    if not hmac.compare_digest(str(claims.get("sha256", "")), expected):
        raise refuse


@router.post("/v1/livekit/webhook")
async def livekit_webhook(
    request: Request,
    db: DbSession,
    clock: AppClock,
    settings: AppSettings,
    rooms: Rooms,
    storage: Objects,
) -> dict[str, str]:
    body = await request.body()
    verify_livekit_signature(
        request.headers.get("authorization"),
        body,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret.get_secret_value(),
    )
    try:
        event = json.loads(body)
    except ValueError:
        raise ApiError(400, "invalid_body", "corps JSON invalide") from None
    if not isinstance(event, dict):
        raise ApiError(400, "invalid_body", "corps JSON invalide")
    if event.get("event") == "egress_ended":
        # Fin d'un enregistrement (étape 14b) : `ready` ou `failed`, et webhook sortant.
        outcome = await run_in_threadpool(
            recording.handle_egress_ended, db, event, storage, now=clock.now()
        )
        if outcome == "unknown-egress":
            # Pas l'enregistrement de la réunion : peut-être la piste audio d'un participant (16b).
            outcome = await run_in_threadpool(
                tracks.handle_egress_ended, db, event, storage, now=clock.now()
            )
        return {"result": outcome}
    if event.get("event") == "track_published":
        outcome = await run_in_threadpool(tracks.handle_published, db, event, now=clock.now())
        return {"result": outcome}
    outcome = await run_in_threadpool(presence.handle_event, db, rooms, event, now=clock.now())
    return {"result": outcome}
