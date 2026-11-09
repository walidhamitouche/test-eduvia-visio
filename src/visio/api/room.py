"""Routes de la salle `/v1/room/*` (étapes 8, 9 et 11).

Appelées par l'interface de visio, **pas** par le backend Eduvia : l'appelant s'authentifie avec
le jeton de son lien (`token`), comme pour `POST /v1/join`. Le rôle, l'admission et le créneau
sont revérifiés côté serveur à chaque appel (voir `services.room_admin`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from visio.api.auth import require_https
from visio.api.deps import AppClock, AppSettings, DbSession, Recorder, Rooms
from visio.api.errors import ErrorResponse
from visio.api.schemas import (
    ChatIn,
    ChatMessageOut,
    LinkIn,
    LobbyCountOut,
    LobbyDecisionIn,
    LobbyOut,
    MuteIn,
    MuteOut,
    OkOut,
    RemoveIn,
)
from visio.domain.slot import to_paris
from visio.services import chat as chat_service
from visio.services import room_admin

router = APIRouter(
    prefix="/v1/room",
    tags=["salle (interface de visio)"],
    dependencies=[Depends(require_https)],
    responses={
        403: {
            "model": ErrorResponse,
            "description": "`organizer_required`, `target_is_organizer`, `not_admitted`, "
            "`participant_excluded` ou `https_required`.",
        },
        404: {"model": ErrorResponse, "description": "`invalid_link`, `participant_not_found`."},
        409: {
            "model": ErrorResponse,
            "description": "`not_open_yet`, `meeting_ended` ou `meeting_cancelled`.",
        },
        502: {"model": ErrorResponse, "description": "`media_server_unavailable`."},
    },
)


@router.post(
    "/mute",
    response_model=MuteOut,
    summary="Couper le micro, la caméra ou le partage d'un participant (organisateur)",
    description="Lien organisateur requis. Coupe côté serveur la source demandée (`microphone`, "
    "`camera` ou `screen_share`) de la personne visée et renvoie le nombre de pistes coupées "
    "(0 si elle ne publie rien).",
)
def mute(
    body: MuteIn, db: DbSession, clock: AppClock, settings: AppSettings, rooms: Rooms
) -> dict[str, Any]:
    muted = room_admin.mute_participant(
        db,
        settings,
        rooms,
        token=body.token,
        identity=body.identity,
        source=body.source,
        now=clock.now(),
    )
    return {"muted": muted}


@router.post(
    "/remove",
    response_model=OkOut,
    summary="Exclure définitivement un participant (organisateur)",
    description="La personne est déconnectée et ne peut plus revenir avec son lien (lien "
    "nominatif révoqué ; lien générique : identité du navigateur exclue).",
)
def remove(
    body: RemoveIn, db: DbSession, clock: AppClock, settings: AppSettings, rooms: Rooms
) -> dict[str, Any]:
    room_admin.remove_participant(
        db, settings, rooms, token=body.token, identity=body.identity, now=clock.now()
    )
    return {"ok": True}


@router.post(
    "/end",
    response_model=OkOut,
    summary="Terminer la réunion pour tout le monde (organisateur)",
    description="Lien organisateur requis. Ferme la salle, déconnecte tout le monde et arrête "
    "l'enregistrement en cours. Les `participant.left` des présents puis `meeting.ended` "
    "(`reason: organizer`) sont envoyés par webhook.",
)
def end(
    body: LinkIn,
    db: DbSession,
    clock: AppClock,
    settings: AppSettings,
    rooms: Rooms,
    recorder: Recorder,
) -> dict[str, Any]:
    room_admin.end_for_everyone(
        db, settings, rooms, token=body.token, now=clock.now(), recorder=recorder
    )
    return {"ok": True}


@router.post(
    "/lobby",
    response_model=LobbyOut,
    summary="Liste de la salle d'attente (organisateur)",
    description="Lien organisateur requis. Renvoie les demandes d'entrée : `id` (à passer à "
    "`/v1/room/lobby/admit` ou `/v1/room/lobby/deny`), nom, identifiant externe, statut et heure "
    "de la demande.",
)
def lobby(body: LinkIn, db: DbSession, clock: AppClock, settings: AppSettings) -> dict[str, Any]:
    now = clock.now()
    rows = room_admin.list_lobby(db, settings, token=body.token, now=now)
    return {
        "entries": [
            {
                "id": row.id,
                "display_name": row.display_name,
                "external_id": row.external_id,
                "status": row.status.value,
                "requested_at": to_paris(row.requested_at),
            }
            for row in rows
        ]
    }


@router.post(
    "/lobby/admit",
    response_model=LobbyCountOut,
    summary="Admettre des personnes",
    description="Lien organisateur requis. Admet les demandes d'entrée indiquées (`ids` pris dans "
    "la liste de la salle d'attente) et renvoie le nombre de personnes concernées.",
)
def admit(
    body: LobbyDecisionIn, db: DbSession, clock: AppClock, settings: AppSettings
) -> dict[str, Any]:
    count = room_admin.decide_lobby(
        db, settings, token=body.token, ids=body.ids, action="admit", now=clock.now()
    )
    return {"count": count}


@router.post(
    "/lobby/deny",
    response_model=LobbyCountOut,
    summary="Refuser des personnes",
    description="Le refus est définitif tant qu'un organisateur ne réadmet pas la personne : "
    "elle ne peut pas redemander à entrer.",
)
def deny(
    body: LobbyDecisionIn, db: DbSession, clock: AppClock, settings: AppSettings
) -> dict[str, Any]:
    count = room_admin.decide_lobby(
        db, settings, token=body.token, ids=body.ids, action="deny", now=clock.now()
    )
    return {"count": count}


@router.post(
    "/lobby/admit-all",
    response_model=LobbyCountOut,
    summary="Tout admettre",
    description="Lien organisateur requis. Admet toutes les personnes en attente et renvoie leur "
    "nombre.",
)
def admit_all(
    body: LinkIn, db: DbSession, clock: AppClock, settings: AppSettings
) -> dict[str, Any]:
    count = room_admin.admit_all(db, settings, token=body.token, now=clock.now())
    return {"count": count}


@router.post(
    "/chat",
    response_model=ChatMessageOut,
    summary="Envoyer un message dans la discussion",
    description="Le message est enregistré avec le nom vérifié de l'expéditeur, puis diffusé à "
    "la salle. C'est la seule façon d'écrire : les participants ne peuvent pas publier de "
    "données directement.",
)
def chat(
    body: ChatIn, db: DbSession, clock: AppClock, settings: AppSettings, rooms: Rooms
) -> dict[str, Any]:
    message = chat_service.post_message(
        db,
        settings,
        rooms,
        token=body.token,
        client_id=body.client_id,
        text=body.text,
        now=clock.now(),
    )
    return {
        "id": message.id,
        "author": {
            "display_name": message.display_name,
            "external_id": message.external_id,
            "role": message.role,
        },
        "sent_at": to_paris(message.sent_at),
        "text": message.text,
    }
