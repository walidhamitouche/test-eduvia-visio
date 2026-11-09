"""Présence (émargement) et événements du serveur de médias (étape 11).

LiveKit prévient le backend par webhook (`POST /v1/livekit/webhook`, signé) quand une personne
entre ou sort de la salle. On en tire :
- la **première entrée** : la réunion passe à `live` (`meeting.started`) ;
- une ligne `participant_sessions` par connexion (identifiée par son `sid`, car une même
  identité peut se reconnecter, ou être connectée deux fois un instant) ;
- les événements sortants `participant.joined` / `participant.left`.

Les heures sont celles des événements LiveKit (pas l'heure de réception) : un webhook retardé
ne fausse pas l'émargement.

Garde-fou (étape 8) : à chaque entrée, si la personne n'est **plus admise** (exclue entre-temps
avec un jeton encore valable, jamais admise), elle est immédiatement déconnectée et rien n'est
enregistré. C'est ce qui ferme la fenêtre des 10 minutes de validité d'un jeton.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from visio.db.models import AccessStatus, MeetingRow, ParticipantSessionRow
from visio.domain.errors import DomainError
from visio.domain.meeting import MeetingState
from visio.domain.presence import merge_intervals, presence_seconds
from visio.domain.roles import Role
from visio.services import access, recording, webhooks
from visio.services.lifecycle import left_event_data, participant_payload
from visio.services.rooms import RoomGateway

logger = logging.getLogger("visio.presence")

# Nos identités ont toujours ce préfixe (`pn_` lien nominatif, `pg_` lien générique). Tout autre
# participant (enregistreur, agent de transcription : étapes 14 et 16) n'est pas une personne.
HUMAN_IDENTITY = re.compile(r"^p[ng]_[0-9a-f]{32}$")


def _event_time(seconds: Any, fallback: datetime) -> datetime:
    if isinstance(seconds, int | float) and seconds > 0:
        return datetime.fromtimestamp(float(seconds), UTC)
    return fallback


def meeting_for_room(db: Session, room: str) -> MeetingRow | None:
    return db.scalar(select(MeetingRow).where(MeetingRow.room_name == room))


def handle_event(db: Session, gateway: RoomGateway, event: dict[str, Any], *, now: datetime) -> str:
    """Traite un webhook LiveKit déjà authentifié. Renvoie ce qui a été fait (journal, tests)."""
    kind = event.get("event")
    if kind not in ("participant_joined", "participant_left"):
        return "ignored"
    room = str((event.get("room") or {}).get("name", ""))
    person: dict[str, Any] = event.get("participant") or {}
    identity = str(person.get("identity", ""))
    sid = str(person.get("sid", ""))
    if not HUMAN_IDENTITY.match(identity):
        return "not-a-person"
    meeting = meeting_for_room(db, room)
    if meeting is None:
        return "unknown-room"
    created = _event_time(event.get("createdAt"), now)
    if kind == "participant_joined":
        joined = _event_time(person.get("joinedAt"), created)
        return _joined(db, gateway, meeting, person, identity, sid, joined, now=now)
    return _left(db, meeting, identity, sid, created, now=now)


def _joined(
    db: Session,
    gateway: RoomGateway,
    meeting: MeetingRow,
    person: dict[str, Any],
    identity: str,
    sid: str,
    joined: datetime,
    *,
    now: datetime,
) -> str:
    entry = access.get_entry(db, meeting.id, identity)
    if (
        meeting.state in (MeetingState.ENDED, MeetingState.CANCELLED)
        or entry is None
        or entry.status is not AccessStatus.ADMITTED
    ):
        try:
            gateway.remove_participant(str(meeting.room_name), identity)
        except DomainError:
            logger.warning(
                "déconnexion d'une entrée refusée impossible", extra={"meeting_id": meeting.id}
            )
        return "refused"

    if sid and db.scalar(
        select(ParticipantSessionRow.id).where(
            ParticipantSessionRow.meeting_id == meeting.id,
            ParticipantSessionRow.participant_sid == sid,
        )
    ):
        return "duplicate"  # webhook rejoué

    try:
        metadata = json.loads(person.get("metadata") or "{}")
    except ValueError:
        metadata = {}
    role = Role.ORGANIZER if metadata.get("role") == Role.ORGANIZER.value else Role.PARTICIPANT
    session = ParticipantSessionRow(
        meeting_id=meeting.id,
        link_id=entry.link_id,
        participant_identity=identity,
        participant_sid=sid or None,
        display_name=str(person.get("name") or entry.display_name),
        external_id=metadata.get("external_id") or entry.external_id,
        role=role,
        joined_at=joined,
    )
    db.add(session)
    if meeting.state is MeetingState.SCHEDULED:
        meeting.state = MeetingState.LIVE
        meeting.started_at = joined
        webhooks.emit(
            db,
            meeting,
            webhooks.MEETING_STARTED,
            {"started_at": joined.isoformat()},
            now=now,
        )
        recording.schedule_start(db, meeting, now=now)  # no-op si `recording: false`
    webhooks.emit(
        db,
        meeting,
        webhooks.PARTICIPANT_JOINED,
        {"participant": participant_payload(session), "joined_at": joined.isoformat()},
        now=now,
    )
    db.commit()
    return "joined"


def _left(
    db: Session, meeting: MeetingRow, identity: str, sid: str, left: datetime, *, now: datetime
) -> str:
    query = select(ParticipantSessionRow).where(
        ParticipantSessionRow.meeting_id == meeting.id,
        ParticipantSessionRow.participant_identity == identity,
        ParticipantSessionRow.left_at.is_(None),
    )
    if sid:
        query = query.where(ParticipantSessionRow.participant_sid == sid)
    session = db.scalars(query.order_by(ParticipantSessionRow.joined_at.desc())).first()
    if session is None:
        return "no-open-session"  # déjà close (fin de réunion) ou jamais vue
    session.left_at = max(left, session.joined_at)
    webhooks.emit(db, meeting, webhooks.PARTICIPANT_LEFT, left_event_data(session), now=now)
    db.commit()
    return "left"


# --- émargement ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PersonAttendance:
    display_name: str
    external_id: str | None
    role: Role
    total_seconds: int
    first_joined_at: datetime
    last_left_at: datetime | None
    sessions: list[tuple[datetime, datetime | None, int]]


def attendance(db: Session, meeting: MeetingRow, *, now: datetime) -> list[PersonAttendance]:
    """Présence par personne (une personne = une identité). Le temps total est l'union des
    connexions, tronquée à la fin de la réunion ; une connexion encore ouverte court jusqu'à
    maintenant (ou jusqu'à la fin si la réunion est finie)."""
    until = now
    if meeting.ended_at is not None:
        until = min(now, meeting.ended_at)
    elif now >= meeting.ends_at:
        until = meeting.ends_at

    rows = db.scalars(
        select(ParticipantSessionRow)
        .where(ParticipantSessionRow.meeting_id == meeting.id)
        .order_by(ParticipantSessionRow.joined_at)
    ).all()
    by_person: dict[str, list[ParticipantSessionRow]] = defaultdict(list)
    for row in rows:
        by_person[row.participant_identity].append(row)

    people: list[PersonAttendance] = []
    for sessions in by_person.values():
        intervals = [(s.joined_at, s.left_at) for s in sessions]
        last = sessions[-1]
        still_in = any(s.left_at is None for s in sessions)
        people.append(
            PersonAttendance(
                display_name=last.display_name,
                external_id=last.external_id,
                role=last.role,
                total_seconds=presence_seconds(intervals, until=until),
                first_joined_at=sessions[0].joined_at,
                last_left_at=None if still_in else max(s.left_at for s in sessions if s.left_at),
                sessions=[
                    (
                        s.joined_at,
                        s.left_at,
                        presence_seconds([(s.joined_at, s.left_at)], until=until),
                    )
                    for s in sessions
                ],
            )
        )
    people.sort(key=lambda p: p.first_joined_at)
    return people


__all__ = ["PersonAttendance", "attendance", "handle_event", "merge_intervals"]
