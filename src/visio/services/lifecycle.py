"""Vie d'une réunion dans le temps (étape 10) : fin de créneau, clôture, rattrapage.

Tout est **sans état en mémoire** : le planificateur (`workers.scheduler`) relit la base à chaque
tour. Une fin de créneau n'est donc jamais « planifiée » quelque part qui pourrait se perdre :
si le service redémarre, ou si `PATCH` déplace le créneau, le tour suivant fait ce qu'il faut.

`terminate_meeting` est l'unique chemin de fin (créneau, API, organisateur) :
1. la réunion passe à `ended` (`ended_at` = instant de la coupure) ;
2. les présences encore ouvertes sont closes à cet instant (émargement exact) ;
3. les événements `participant.left` puis `meeting.ended` entrent dans la boîte d'envoi, **dans
   la même transaction** ;
4. l'ordre de fermer la salle est tenté tout de suite, et sinon repris par une tâche `close_room`
   avec relances (le serveur de médias peut être injoignable à cet instant). Cette tâche arrête
   d'abord l'enregistrement (`recording.stop_for_meeting`), puis ferme la salle.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from visio.db.models import JobRow, JobStatus, MeetingRow, ParticipantSessionRow
from visio.domain.errors import DomainError
from visio.domain.meeting import MeetingState
from visio.domain.roles import Role
from visio.services import recording, tracks, webhooks
from visio.services.egress import RecorderGateway
from visio.services.jobs import JOB_MAX_ATTEMPTS, JOB_RETRY_DELAYS
from visio.services.rooms import RoomGateway

logger = logging.getLogger("visio.lifecycle")

CLOSE_ROOM = "close_room"


def participant_payload(row: ParticipantSessionRow) -> dict[str, Any]:
    return {
        "display_name": row.display_name,
        "external_id": row.external_id,
        "role": row.role.value if isinstance(row.role, Role) else str(row.role),
    }


def left_event_data(row: ParticipantSessionRow) -> dict[str, Any]:
    assert row.left_at is not None
    return {
        "participant": participant_payload(row),
        "joined_at": row.joined_at.isoformat(),
        "left_at": row.left_at.isoformat(),
        "duration_seconds": max(0, int((row.left_at - row.joined_at).total_seconds())),
    }


def close_open_sessions(db: Session, meeting: MeetingRow, at: datetime) -> int:
    """Ferme les présences ouvertes à `at` et annonce chaque sortie. Sans valider."""
    rows = db.scalars(
        select(ParticipantSessionRow).where(
            ParticipantSessionRow.meeting_id == meeting.id,
            ParticipantSessionRow.left_at.is_(None),
        )
    ).all()
    for row in rows:
        row.left_at = max(at, row.joined_at)
        webhooks.emit(db, meeting, webhooks.PARTICIPANT_LEFT, left_event_data(row), now=row.left_at)
    return len(rows)


def terminate_meeting(
    db: Session,
    gateway: RoomGateway | None,
    meeting: MeetingRow,
    *,
    now: datetime,
    reason: str,
    at: datetime | None = None,
    recorder: RecorderGateway | None = None,
) -> bool:
    """Termine une réunion (idempotent : renvoie faux si elle l'est déjà ou si elle est annulée).

    `reason` : `slot_end` (fin du créneau), `organizer` ou `api`. `at` : instant de la coupure
    (par défaut `now` ; la fin de créneau passe `ends_at`, même rattrapée avec du retard).
    """
    if meeting.state in (MeetingState.ENDED, MeetingState.CANCELLED):
        return False
    cut = at or now
    meeting.state = MeetingState.ENDED
    meeting.ended_at = cut
    close_open_sessions(db, meeting, cut)
    webhooks.emit(
        db,
        meeting,
        webhooks.MEETING_ENDED,
        {
            "reason": reason,
            "started_at": meeting.started_at.isoformat() if meeting.started_at else None,
            "ended_at": cut.isoformat(),
        },
        now=now,
    )
    if meeting.room_name is not None:
        db.add(
            JobRow(
                kind=CLOSE_ROOM,
                meeting_id=meeting.id,
                payload={"room": meeting.room_name},
                run_at=now,
            )
        )
    db.commit()
    if gateway is not None:
        # tentative immédiate ; sinon le planificateur reprend
        run_jobs(db, gateway, now=now, recorder=recorder)
    return True


def run_due_meetings(
    db: Session,
    gateway: RoomGateway | None,
    *,
    now: datetime,
    recorder: RecorderGateway | None = None,
) -> int:
    """Termine les réunions dont le créneau est fini (en cours, ou jamais rejointes)."""
    due = db.scalars(
        select(MeetingRow).where(
            MeetingRow.state.in_([MeetingState.SCHEDULED, MeetingState.LIVE]),
            MeetingRow.ends_at <= now,
        )
    ).all()
    count = 0
    for meeting in due:
        if terminate_meeting(
            db, gateway, meeting, now=now, reason="slot_end", at=meeting.ends_at, recorder=recorder
        ):
            count += 1
    return count


def run_jobs(
    db: Session,
    gateway: RoomGateway,
    *,
    now: datetime,
    recorder: RecorderGateway | None = None,
) -> int:
    """Exécute les tâches `close_room` dues ; les échecs sont relancés, puis marqués `failed`.

    Si l'enregistrement est actif, il est arrêté **avant** de fermer la salle."""
    jobs = db.scalars(
        select(JobRow)
        .where(
            JobRow.kind == CLOSE_ROOM,
            JobRow.status.in_([JobStatus.PENDING, JobStatus.FAILED]),
            JobRow.run_at <= now,
        )
        .order_by(JobRow.run_at)
    ).all()
    for job in jobs:
        job.attempts += 1
        # Valider avant les appels réseau (arrêt d'Egress, fermeture de salle) : le verrou
        # d'écriture de SQLite ne doit pas rester tenu pendant qu'un serveur lent répond.
        db.commit()
        try:
            meeting = db.get(MeetingRow, job.meeting_id) if job.meeting_id else None
            if recorder is not None and meeting is not None:
                tracks.stop_for_meeting(db, recorder, meeting)
                recording.stop_for_meeting(db, recorder, meeting, now=now)
            gateway.delete_room(str(job.payload["room"]))
        except DomainError as error:
            job.last_error = error.message
            if job.attempts >= JOB_MAX_ATTEMPTS:
                job.status = JobStatus.ABANDONED
                logger.error(
                    "fermeture de salle abandonnée",
                    extra={"job": job.id, "meeting_id": job.meeting_id},
                )
            else:
                job.status = JobStatus.FAILED
                job.run_at = now + JOB_RETRY_DELAYS[job.attempts - 1]
        else:
            job.status = JobStatus.DONE
            job.last_error = None
        db.commit()
    return len(jobs)
