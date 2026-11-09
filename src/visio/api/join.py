"""Route `POST /v1/join` : l'interface de visio échange le jeton d'un lien contre un jeton
LiveKit. Route **publique** (le jeton du lien est l'identifiant) : elle ne crée aucune réunion
et ne renvoie rien tant que le lien, le créneau et la révocation ne sont pas vérifiés."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from visio.api.auth import _is_https, require_https
from visio.api.deps import AppClock, AppSettings, DbSession
from visio.api.errors import ErrorResponse
from visio.api.ratelimit import limit_requests
from visio.api.schemas import JoinIn, JoinOut, PreviewIn, PreviewOut
from visio.domain.slot import to_paris
from visio.services import join as service

CLIENT_COOKIE = "visio_cid"


def _valid_client_id(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_-]{16,64}", value) is not None


router = APIRouter(
    prefix="/v1/join",
    tags=["visio"],
    dependencies=[Depends(require_https), Depends(limit_requests)],
    responses={403: {"model": ErrorResponse, "description": "HTTPS obligatoire (production)."}},
)


@router.post(
    "/preview",
    response_model=PreviewOut,
    summary="Aperçu d'un lien (sans rejoindre)",
    description="Pour les écrans d'attente et de pré-jointure : état de la réunion, heure "
    "d'ouverture, nom à saisir ou verrouillé. **N'émet aucun jeton** et ne donne aucun accès ; "
    "un lien invalide donne `invalid_link` (404), un lien révoqué `participant_excluded` (403).",
    responses={
        403: {"model": ErrorResponse, "description": "`participant_excluded` ou `https_required`."},
        404: {"model": ErrorResponse, "description": "`invalid_link` (inventé, modifié, inconnu)."},
    },
)
def preview(
    body: PreviewIn, db: DbSession, clock: AppClock, settings: AppSettings
) -> dict[str, Any]:
    result = service.preview_link(db, settings, token=body.token, now=clock.now())
    meeting = result.meeting
    return {
        "status": result.status,
        "server_time": to_paris(result.now),
        "opens_at": to_paris(result.opens_at),
        "meeting": {
            "title": meeting.title,
            "starts_at": to_paris(meeting.starts_at),
            "ends_at": to_paris(meeting.ends_at),
            "waiting_room": meeting.waiting_room,
            "recording": meeting.recording,
            "transcription": meeting.transcription,
        },
        "participant": {
            "role": result.link.role,
            "display_name": result.link.display_name,
            "external_id": result.link.external_id,
            "name_required": result.link.display_name is None,
        },
    }


@router.post(
    "",
    response_model=JoinOut,
    summary="Rejoindre une réunion avec un lien",
    description="Vérifie le lien (signature, base, révocation) puis le créneau, et renvoie un "
    "jeton LiveKit de courte durée aux droits du rôle du lien. À appeler à **chaque** "
    "connexion ou reconnexion. Aucune salle n'est ouverte si une vérification échoue.",
    responses={
        403: {"model": ErrorResponse, "description": "`participant_excluded` ou `https_required`."},
        404: {"model": ErrorResponse, "description": "`invalid_link` (inventé, modifié, inconnu)."},
        409: {
            "model": ErrorResponse,
            "description": "`not_open_yet` (avec `opens_at`), `meeting_ended`, "
            "`meeting_cancelled` ou `meeting_full` (plafond de participants).",
        },
        422: {
            "model": ErrorResponse,
            "description": "`validation_error` ou `display_name_required` (lien générique).",
        },
    },
)
def join(
    body: JoinIn,
    request: Request,
    response: Response,
    db: DbSession,
    clock: AppClock,
    settings: AppSettings,
) -> dict[str, Any]:
    # Le cookie posé par le serveur fait foi sur le `client_id` envoyé : vider le stockage local
    # du navigateur ne change plus l'identité d'une personne exclue (lien générique).
    cookie_id = request.cookies.get(CLIENT_COOKIE)
    result = service.join_meeting(
        db,
        settings,
        token=body.token,
        display_name=body.display_name,
        client_id=cookie_id if cookie_id and _valid_client_id(cookie_id) else body.client_id,
        now=clock.now(),
    )
    if result.client_id is not None:
        response.set_cookie(
            CLIENT_COOKIE,
            result.client_id,
            max_age=2 * 24 * 3600,
            httponly=True,
            samesite="lax",
            secure=_is_https(request),
            path="/",
        )
    meeting = result.meeting
    return {
        "status": result.status.value,
        "livekit_url": result.livekit_url,
        "livekit_token": result.livekit_token,
        "expires_at": None if result.expires_at is None else to_paris(result.expires_at),
        "seconds_until_end": result.seconds_until_end,
        "client_id": result.client_id,
        "meeting": {
            "title": meeting.title,
            "starts_at": to_paris(meeting.starts_at),
            "ends_at": to_paris(meeting.ends_at),
            "waiting_room": meeting.waiting_room,
            "recording": meeting.recording,
            "transcription": meeting.transcription,
        },
        "participant": {
            "display_name": result.display_name,
            "external_id": result.external_id,
            "role": result.role,
        },
    }
