"""Routes `/v1/meetings` (besoin §4). Toutes exigent la clé d'API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response

from visio.api.auth import require_api_key
from visio.api.deps import AppClock, AppSettings, DbSession, Recorder, Rooms
from visio.api.errors import ErrorResponse
from visio.api.schemas import (
    AttendanceOut,
    ChatExportOut,
    ConnectedParticipantOut,
    CreateMeetingIn,
    MeetingCreatedOut,
    MeetingOut,
    OrganizerLinkOut,
    UpdateMeetingIn,
)
from visio.db.models import MeetingRow
from visio.domain.slot import lifecycle_status, to_paris
from visio.services import chat as chat_service
from visio.services import meetings as service
from visio.services import presence
from visio.services.links import issue_link_url

router = APIRouter(
    prefix="/v1/meetings",
    tags=["réunions"],
    dependencies=[Depends(require_api_key)],
    responses={
        401: {"model": ErrorResponse, "description": "Clé d'API absente ou invalide."},
        403: {"model": ErrorResponse, "description": "HTTPS obligatoire (production)."},
    },
)

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "`meeting_not_found`."}
}


def _paris_or_none(value: Any) -> Any:
    return None if value is None else to_paris(value)


def _present(db: DbSession, meeting: MeetingRow, now: Any) -> dict[str, Any]:
    participants = [
        ConnectedParticipantOut(
            display_name=p.display_name,
            external_id=p.external_id,
            role=p.role,
            joined_at=to_paris(p.joined_at),
        )
        for p in service.connected_participants(db, meeting.id)
    ]
    return {
        "id": meeting.id,
        "title": meeting.title,
        "status": lifecycle_status(meeting.to_domain(), now),
        "starts_at": to_paris(meeting.starts_at),
        "ends_at": to_paris(meeting.ends_at),
        "early_join_minutes": meeting.early_join_minutes,
        "waiting_room": meeting.waiting_room,
        "recording": meeting.recording,
        "transcription": meeting.transcription,
        "started_at": _paris_or_none(meeting.started_at),
        "ended_at": _paris_or_none(meeting.ended_at),
        "metadata": meeting.metadata_,
        "participants": participants,
        "created_at": to_paris(meeting.created_at),
        "updated_at": to_paris(meeting.updated_at),
    }


@router.post(
    "",
    status_code=201,
    response_model=MeetingCreatedOut,
    summary="Créer une réunion",
    description="Crée une réunion sur un créneau strict et génère son lien participant "
    "générique et un lien par organisateur. Les liens ne sont renvoyés qu'ici.",
    responses={
        422: {"model": ErrorResponse, "description": "`invalid_slot` ou `validation_error`."}
    },
)
def create_meeting(
    body: CreateMeetingIn,
    response: Response,
    db: DbSession,
    clock: AppClock,
    settings: AppSettings,
) -> dict[str, Any]:
    now = clock.now()
    created = service.create_meeting(
        db,
        now=now,
        title=body.title,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        early_join_minutes=body.early_join_minutes,
        waiting_room=body.waiting_room,
        recording=body.recording,
        transcription=body.transcription,
        organizers=[service.OrganizerSpec(o.display_name, o.external_id) for o in body.organizers],
        metadata=body.metadata,
    )
    organizer_urls = [issue_link_url(settings, link) for link in created.organizer_links]
    response.headers["Location"] = f"/v1/meetings/{created.meeting.id}"
    return {
        **_present(db, created.meeting, now),
        "participant_url": issue_link_url(settings, created.participant_link),
        "organizer_urls": organizer_urls,
        "organizers": [
            OrganizerLinkOut(display_name=o.display_name, external_id=o.external_id, url=url)
            for o, url in zip(body.organizers, organizer_urls, strict=True)
        ],
    }


@router.get(
    "/{meeting_id}",
    response_model=MeetingOut,
    summary="État d'une réunion",
    description="État (planifiée, en cours, terminée, annulée), options, `metadata` et "
    "participants connectés. Ne renvoie jamais de lien.",
    responses=_NOT_FOUND,
)
def get_meeting(meeting_id: str, db: DbSession, clock: AppClock) -> dict[str, Any]:
    meeting = service.get_meeting(db, meeting_id)
    return _present(db, meeting, clock.now())


@router.patch(
    "/{meeting_id}",
    response_model=MeetingOut,
    summary="Modifier une réunion",
    description="Modifie le créneau, le titre ou les options **tant que la réunion n'a pas "
    "commencé** (personne connectée et `starts_at` pas atteint). Seuls les champs envoyés "
    "changent ; `metadata` est remplacé en entier.",
    responses={
        **_NOT_FOUND,
        409: {
            "model": ErrorResponse,
            "description": "`meeting_already_started` ou `meeting_cancelled`.",
        },
        422: {"model": ErrorResponse, "description": "`invalid_slot` ou `validation_error`."},
    },
)
def update_meeting(
    meeting_id: str, body: UpdateMeetingIn, db: DbSession, clock: AppClock
) -> dict[str, Any]:
    now = clock.now()
    meeting = service.update_meeting(db, meeting_id, body.model_dump(exclude_unset=True), now=now)
    return _present(db, meeting, now)


@router.post(
    "/{meeting_id}/end",
    response_model=MeetingOut,
    summary="Clore une réunion",
    description="Clôt immédiatement une réunion ouverte : plus personne ne peut rejoindre. "
    "Sans effet (200) si elle est déjà terminée. Avant l'ouverture : `meeting_not_started`, "
    "utiliser `DELETE`.",
    responses={
        **_NOT_FOUND,
        409: {
            "model": ErrorResponse,
            "description": "`meeting_not_started` ou `meeting_cancelled`.",
        },
    },
)
def end_meeting(
    meeting_id: str, db: DbSession, clock: AppClock, rooms: Rooms, recorder: Recorder
) -> dict[str, Any]:
    now = clock.now()
    meeting = service.end_meeting(
        db, meeting_id, now=now, gateway=rooms, recorder=recorder, reason="api"
    )
    return _present(db, meeting, now)


@router.delete(
    "/{meeting_id}",
    response_model=MeetingOut,
    summary="Annuler une réunion",
    description="Annule une réunion qui n'a pas commencé et renvoie son état (`cancelled`). "
    "Sans effet (200) si elle est déjà annulée. Une réunion commencée se clôt avec `/end`.",
    responses={
        **_NOT_FOUND,
        409: {"model": ErrorResponse, "description": "`meeting_already_started`."},
    },
)
def cancel_meeting(meeting_id: str, db: DbSession, clock: AppClock) -> dict[str, Any]:
    now = clock.now()
    meeting = service.cancel_meeting(db, meeting_id, now=now)
    return _present(db, meeting, now)


@router.get(
    "/{meeting_id}/attendance",
    response_model=AttendanceOut,
    summary="Présence (émargement)",
    description="Chaque entrée et sortie par personne, et le temps de présence total : durée de "
    "l'**union** des connexions (une reconnexion ou un second onglet ne compte pas deux fois), "
    "jusqu'à la fin de la réunion au plus. Une personne encore connectée a `left_at: null`.",
    responses=_NOT_FOUND,
)
def attendance(meeting_id: str, db: DbSession, clock: AppClock) -> dict[str, Any]:
    now = clock.now()
    meeting = service.get_meeting(db, meeting_id)
    people = presence.attendance(db, meeting, now=now)
    return {
        "meeting_id": meeting.id,
        "participants": [
            {
                "display_name": p.display_name,
                "external_id": p.external_id,
                "role": p.role,
                "total_seconds": p.total_seconds,
                "first_joined_at": to_paris(p.first_joined_at),
                "last_left_at": _paris_or_none(p.last_left_at),
                "sessions": [
                    {"joined_at": to_paris(j), "left_at": _paris_or_none(left), "seconds": secs}
                    for j, left, secs in p.sessions
                ],
            }
            for p in people
        ],
    }


@router.get(
    "/{meeting_id}/chat",
    response_model=ChatExportOut,
    summary="Historique du chat",
    description="Auteur (nom, `external_id`, rôle), horodatage et message, dans l'ordre d'envoi.",
    responses=_NOT_FOUND,
)
def chat_history(meeting_id: str, db: DbSession) -> dict[str, Any]:
    meeting = service.get_meeting(db, meeting_id)
    return {
        "messages": [
            {
                "id": m.id,
                "author": {
                    "display_name": m.display_name,
                    "external_id": m.external_id,
                    "role": m.role,
                },
                "sent_at": to_paris(m.sent_at),
                "text": m.text,
            }
            for m in chat_service.list_messages(db, meeting.id)
        ]
    }
