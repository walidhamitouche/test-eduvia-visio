"""Actions de la salle réservées à l'organisateur (étape 8) et salle d'attente (étape 9).

Chaque action est autorisée **ici, côté serveur**, à partir du lien de l'appelant (jamais sur la
foi de l'interface) :
1. `resolve_link` : signature, base, révocation (un jeton falsifié en « organisateur » échoue) ;
2. le rôle **en base** doit être organisateur (`organizer_required`, 403, sinon) ;
3. la réunion doit être ouverte (créneau) ;
4. la personne visée est cherchée **dans la salle** (`participant_not_found` sinon) ; un
   organisateur n'est jamais une cible (`target_is_organizer`) : plusieurs organisateurs sont
   égaux, aucun ne peut en évincer un autre.

Plusieurs organisateurs : chaque lien organisateur ouvre les mêmes droits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from visio.config import Settings
from visio.db.models import AccessRow, AccessStatus, LinkRow, MeetingRow
from visio.domain.errors import (
    MeetingCancelledError,
    MeetingEndedError,
    NotOpenYetError,
    OrganizerRequiredError,
    ParticipantNotFoundError,
    TargetIsOrganizerError,
)
from visio.domain.meeting import SlotStatus
from visio.domain.roles import Role
from visio.domain.slot import opens_at, status_at, to_paris
from visio.services import access
from visio.services.egress import RecorderGateway
from visio.services.links import resolve_link, revoke_link
from visio.services.livekit_tokens import room_name
from visio.services.meetings import end_meeting
from visio.services.rooms import RoomGateway, RoomParticipant

LOBBY_ACTIONS = ("admit", "deny")


@dataclass(frozen=True, slots=True)
class Caller:
    link: LinkRow
    meeting: MeetingRow
    room: str


def authorize(
    db: Session,
    settings: Settings,
    token: str,
    *,
    now: datetime,
    organizer_only: bool,
) -> Caller:
    """Vérifie le lien de l'appelant, son rôle si besoin, et que la réunion est ouverte."""
    resolved = resolve_link(db, settings, token)  # invalid_link / participant_excluded
    link, meeting = resolved.link, resolved.meeting
    if organizer_only and link.role is not Role.ORGANIZER:
        msg = "cette action est réservée aux organisateurs"
        raise OrganizerRequiredError(msg)
    domain = meeting.to_domain()
    status = status_at(domain, now)
    if status is SlotStatus.CANCELLED:
        msg = "la réunion est annulée"
        raise MeetingCancelledError(msg)
    if status is SlotStatus.ENDED:
        msg = "la réunion est terminée"
        raise MeetingEndedError(msg)
    if status is SlotStatus.NOT_OPEN_YET:
        opening = to_paris(opens_at(domain))
        msg = f"la réunion n'est pas encore ouverte (ouverture à {opening.isoformat()})"
        raise NotOpenYetError(msg, opens_at=opening.isoformat())
    room = room_name(settings.link_signing_secret.get_secret_value(), meeting.id)
    return Caller(link, meeting, room)


def _target(gateway: RoomGateway, caller: Caller, identity: str) -> RoomParticipant:
    for person in gateway.list_participants(caller.room):
        if person.identity == identity:
            if person.role == Role.ORGANIZER.value:
                msg = "un organisateur ne peut pas être modéré"
                raise TargetIsOrganizerError(msg)
            return person
    msg = "cette personne n'est pas dans la réunion"
    raise ParticipantNotFoundError(msg)


# --- modération (étape 8) --------------------------------------------------------------------


def mute_participant(
    db: Session,
    settings: Settings,
    gateway: RoomGateway,
    *,
    token: str,
    identity: str,
    source: str,
    now: datetime,
) -> int:
    """Coupe le micro, la caméra ou le partage d'écran d'un participant. Renvoie le nombre de
    pistes coupées. La personne peut rallumer son micro ou sa caméra elle-même (« couper » n'est
    pas « interdire »), l'organisateur peut recommencer."""
    caller = authorize(db, settings, token, now=now, organizer_only=True)
    person = _target(gateway, caller, identity)
    sources = {source, "screen_share_audio"} if source == "screen_share" else {source}
    muted = 0
    for track in person.tracks:
        if track.source in sources and not track.muted:
            gateway.mute_track(caller.room, identity, track.sid, muted=True)
            muted += 1
    return muted


def remove_participant(
    db: Session,
    settings: Settings,
    gateway: RoomGateway,
    *,
    token: str,
    identity: str,
    now: datetime,
) -> None:
    """Exclut définitivement une personne : inscrite d'abord (elle ne peut plus revenir, même
    si l'ordre de déconnexion échoue), puis déconnectée.

    Lien nominatif : le lien est révoqué (liste de révocation). Lien générique : seule l'identité
    de la personne est exclue (révoquer le lien exclurait tout le monde)."""
    caller = authorize(db, settings, token, now=now, organizer_only=True)
    person = _target(gateway, caller, identity)
    entry = access.get_entry(db, caller.meeting.id, identity)
    if entry is None:  # entrée sans passer par /join : on garde au moins la trace
        entry = AccessRow(
            meeting_id=caller.meeting.id,
            participant_identity=identity,
            display_name=person.name or "?",
            role=Role.PARTICIPANT,
            status=AccessStatus.EXCLUDED,
        )
        db.add(entry)
    entry.status = AccessStatus.EXCLUDED
    entry.decided_at = now
    entry.decided_by = caller.link.id
    if entry.link_id is not None:
        link = db.get(LinkRow, entry.link_id)
        if link is not None and link.display_name is not None:
            revoke_link(db, link.id, now=now)
    db.commit()
    gateway.remove_participant(caller.room, identity)


def end_for_everyone(
    db: Session,
    settings: Settings,
    gateway: RoomGateway,
    *,
    token: str,
    now: datetime,
    recorder: RecorderGateway | None = None,
) -> None:
    """« Terminer la réunion » par un organisateur : coupure immédiate pour tout le monde."""
    caller = authorize(db, settings, token, now=now, organizer_only=True)
    end_meeting(
        db, caller.meeting.id, now=now, gateway=gateway, recorder=recorder, reason="organizer"
    )


# --- salle d'attente (étape 9) ---------------------------------------------------------------


def list_lobby(db: Session, settings: Settings, *, token: str, now: datetime) -> list[AccessRow]:
    caller = authorize(db, settings, token, now=now, organizer_only=True)
    return access.waiting_entries(db, caller.meeting.id, now=now)


def decide_lobby(
    db: Session,
    settings: Settings,
    *,
    token: str,
    ids: list[str],
    action: str,
    now: datetime,
) -> int:
    """Admet ou refuse des demandes précises. Renvoie le nombre de demandes modifiées.

    Un refus est définitif tant qu'aucun organisateur ne réadmet la personne : elle ne peut pas
    redemander. Une personne exclue ne peut pas être réadmise."""
    assert action in LOBBY_ACTIONS
    caller = authorize(db, settings, token, now=now, organizer_only=True)
    target = AccessStatus.ADMITTED if action == "admit" else AccessStatus.DENIED
    changed = 0
    for entry_id in ids:
        entry = db.get(AccessRow, entry_id)
        if entry is None or entry.meeting_id != caller.meeting.id:
            msg = "demande inconnue"
            raise ParticipantNotFoundError(msg)
        if entry.status is AccessStatus.EXCLUDED or entry.role is Role.ORGANIZER:
            msg = "demande inconnue"
            raise ParticipantNotFoundError(msg)
        if entry.status is not target:
            entry.status = target
            entry.decided_at = now
            entry.decided_by = caller.link.id
            changed += 1
    db.commit()
    return changed


def admit_all(db: Session, settings: Settings, *, token: str, now: datetime) -> int:
    """« Tout admettre » : toutes les personnes qui attendent encore (pas les refusées)."""
    caller = authorize(db, settings, token, now=now, organizer_only=True)
    count = 0
    for entry in access.waiting_entries(db, caller.meeting.id, now=now):
        if entry.status is AccessStatus.WAITING:
            entry.status = AccessStatus.ADMITTED
            entry.decided_at = now
            entry.decided_by = caller.link.id
            count += 1
    db.commit()
    return count
