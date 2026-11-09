"""Cas d'usage « réunion » : création, lecture, modification, annulation, clôture.

Aucune dépendance à FastAPI : la session et l'heure sont passées en paramètres, ce qui
permet de tout tester avec une horloge simulée. Les règles de fond (créneau, « commencée »)
viennent de `visio.domain.slot`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from visio.db.models import LinkRow, MeetingRow, ParticipantSessionRow
from visio.domain.errors import (
    InvalidSlotError,
    MeetingAlreadyStartedError,
    MeetingCancelledError,
    MeetingNotFoundError,
    MeetingNotStartedError,
)
from visio.domain.meeting import MeetingState, SlotStatus
from visio.domain.roles import Role
from visio.domain.slot import can_modify, parse_iso8601, status_at, validate_slot
from visio.services.egress import RecorderGateway
from visio.services.lifecycle import terminate_meeting
from visio.services.rooms import RoomGateway

# Champs que `PATCH` peut changer (besoin §4 : créneau, titre, options).
PATCHABLE_FIELDS = frozenset(
    {
        "title",
        "starts_at",
        "ends_at",
        "early_join_minutes",
        "waiting_room",
        "recording",
        "transcription",
        "metadata",
    }
)


@dataclass(frozen=True, slots=True)
class OrganizerSpec:
    display_name: str
    external_id: str | None = None


@dataclass(frozen=True, slots=True)
class CreatedMeeting:
    meeting: MeetingRow
    participant_link: LinkRow
    organizer_links: list[LinkRow]  # même ordre que les organisateurs demandés


def _check_not_already_over(ends_at: datetime, now: datetime) -> None:
    if ends_at <= now:
        msg = "ends_at est déjà passé : ce créneau ne pourrait jamais être rejoint"
        raise InvalidSlotError(msg)


def get_meeting(db: Session, meeting_id: str) -> MeetingRow:
    meeting = db.get(MeetingRow, meeting_id)
    if meeting is None:
        msg = "aucune réunion avec cet identifiant"
        raise MeetingNotFoundError(msg)
    return meeting


def connected_participants(db: Session, meeting_id: str) -> Sequence[ParticipantSessionRow]:
    """Personnes actuellement connectées : sessions de présence sans heure de sortie."""
    query = (
        select(ParticipantSessionRow)
        .where(
            ParticipantSessionRow.meeting_id == meeting_id,
            ParticipantSessionRow.left_at.is_(None),
        )
        .order_by(ParticipantSessionRow.joined_at)
    )
    return db.scalars(query).all()


def create_meeting(
    db: Session,
    *,
    now: datetime,
    title: str,
    starts_at: str,
    ends_at: str,
    early_join_minutes: int,
    waiting_room: bool,
    recording: bool,
    transcription: bool,
    organizers: Sequence[OrganizerSpec],
    metadata: dict[str, Any],
) -> CreatedMeeting:
    start = parse_iso8601(starts_at)
    end = parse_iso8601(ends_at)
    validate_slot(start, end, early_join_minutes)
    _check_not_already_over(end, now)

    meeting = MeetingRow(
        title=title,
        starts_at=start,
        ends_at=end,
        early_join_minutes=early_join_minutes,
        waiting_room=waiting_room,
        recording=recording,
        transcription=transcription,
        state=MeetingState.SCHEDULED,
        metadata_=metadata,
    )
    participant_link = LinkRow(role=Role.PARTICIPANT)
    organizer_links = [
        LinkRow(role=Role.ORGANIZER, display_name=o.display_name, external_id=o.external_id)
        for o in organizers
    ]
    meeting.links = [participant_link, *organizer_links]
    db.add(meeting)
    db.commit()  # réunion et liens ensemble, ou rien
    return CreatedMeeting(meeting, participant_link, organizer_links)


def update_meeting(
    db: Session, meeting_id: str, changes: dict[str, Any], *, now: datetime
) -> MeetingRow:
    """Modifie le créneau, le titre ou les options d'une réunion qui n'a pas commencé.

    `changes` ne contient que les champs réellement envoyés ; `metadata` est remplacé en entier.
    """
    unknown = set(changes) - PATCHABLE_FIELDS
    if unknown:
        msg = f"champs non modifiables : {', '.join(sorted(unknown))}"
        raise ValueError(msg)

    meeting = get_meeting(db, meeting_id)
    if meeting.state is MeetingState.CANCELLED:
        msg = "la réunion est annulée"
        raise MeetingCancelledError(msg)
    if not can_modify(meeting.to_domain(), now):
        msg = "la réunion a déjà commencé : elle ne peut plus être modifiée"
        raise MeetingAlreadyStartedError(msg)

    start = parse_iso8601(changes["starts_at"]) if "starts_at" in changes else meeting.starts_at
    end = parse_iso8601(changes["ends_at"]) if "ends_at" in changes else meeting.ends_at
    early = changes.get("early_join_minutes", meeting.early_join_minutes)
    validate_slot(start, end, early)
    _check_not_already_over(end, now)

    meeting.starts_at = start
    meeting.ends_at = end
    meeting.early_join_minutes = early
    for name in ("title", "waiting_room", "recording", "transcription"):
        if name in changes:
            setattr(meeting, name, changes[name])
    if "metadata" in changes:
        meeting.metadata_ = changes["metadata"]
    db.commit()
    return meeting


def cancel_meeting(db: Session, meeting_id: str, *, now: datetime) -> MeetingRow:
    """Annule une réunion qui n'a pas commencé. Rejouable sans risque : déjà annulée = succès."""
    meeting = get_meeting(db, meeting_id)
    if meeting.state is MeetingState.CANCELLED:
        return meeting
    if not can_modify(meeting.to_domain(), now):
        msg = "la réunion a déjà commencé : elle ne peut plus être annulée (utiliser /end)"
        raise MeetingAlreadyStartedError(msg)
    meeting.state = MeetingState.CANCELLED
    db.commit()
    return meeting


def end_meeting(
    db: Session,
    meeting_id: str,
    *,
    now: datetime,
    gateway: RoomGateway | None = None,
    recorder: RecorderGateway | None = None,
    reason: str = "api",
) -> MeetingRow:
    """Clôt immédiatement une réunion ouverte (en cours, ou ouvrable mais encore vide) : tout le
    monde est déconnecté, les présences sont closes, `meeting.ended` est annoncé.

    Rejouable sans risque : déjà terminée = succès, sans rien changer. Avant l'ouverture,
    il n'y a rien à clore : il faut annuler.
    """
    meeting = get_meeting(db, meeting_id)
    if meeting.state is MeetingState.CANCELLED:
        msg = "la réunion est annulée"
        raise MeetingCancelledError(msg)
    if meeting.state is MeetingState.ENDED:
        return meeting
    status = status_at(meeting.to_domain(), now)
    if status is SlotStatus.ENDED:
        return meeting  # l'heure de fin est passée : le planificateur (étape 10) l'enregistrera
    if status is SlotStatus.NOT_OPEN_YET:
        msg = "la réunion n'est pas encore ouverte : utiliser DELETE pour l'annuler"
        raise MeetingNotStartedError(msg)
    terminate_meeting(db, gateway, meeting, now=now, reason=reason, recorder=recorder)
    return meeting
