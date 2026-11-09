"""Rejoindre une réunion (besoin §2, §3 et §6, étape 6) : du lien au jeton LiveKit.

À chaque appel, dans cet ordre, **sans rien créer tant qu'une vérification échoue** :
1. le lien : signature, base de données, rôle, révocation (`resolve_link`) ;
2. le créneau (`status_at`) : annulée, terminée ou pas encore ouverte → refus distinct ;
3. le nom : un lien générique exige un nom saisi ;
4. l'accès (`services.access`) : une personne exclue est refusée ; avec la salle d'attente, un
   participant non admis **n'obtient aucun jeton** (il relit sa demande toutes les 2 s) ;
5. seulement alors, un jeton LiveKit de courte durée. La salle LiveKit est créée par LiveKit à
   la première connexion (nom opaque, voir `livekit_tokens.room_name`) : sans jeton émis ici,
   personne ne peut en ouvrir une.

Passer la réunion en `live` et enregistrer la présence se fait à la réception des webhooks
LiveKit (`services.presence`) : émettre un jeton n'est pas se connecter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visio.config import Settings
from visio.db.models import AccessStatus, LinkRow, MeetingRow, ParticipantSessionRow
from visio.domain.errors import (
    MeetingCancelledError,
    MeetingEndedError,
    MeetingFullError,
    NotOpenYetError,
)
from visio.domain.meeting import SlotStatus
from visio.domain.roles import Role
from visio.domain.slot import closes_at, opens_at, seconds_until_end, status_at, to_paris
from visio.services.access import request_access, resolve_identity
from visio.services.links import resolve_link
from visio.services.livekit_tokens import (
    client_ws_url,
    issue_livekit_token,
    livekit_expiry,
    room_name,
)


@dataclass(frozen=True, slots=True)
class JoinResult:
    # `admitted` : jeton émis. `waiting` / `denied` : salle d'attente, aucun jeton.
    status: AccessStatus
    livekit_url: str | None
    livekit_token: str | None
    expires_at: datetime | None
    seconds_until_end: int
    client_id: str | None  # seulement sur le lien générique
    display_name: str
    external_id: str | None
    role: Role
    meeting: MeetingRow
    link: LinkRow


@dataclass(frozen=True, slots=True)
class PreviewResult:
    status: SlotStatus
    now: datetime
    opens_at: datetime
    meeting: MeetingRow
    link: LinkRow


def preview_link(db: Session, settings: Settings, *, token: str, now: datetime) -> PreviewResult:
    """Ce que voit quelqu'un qui ouvre un lien, **sans émettre de jeton LiveKit** : l'état de la
    réunion (pas encore ouverte, ouverte, terminée, annulée), l'heure d'ouverture, et si le nom
    est à saisir. Sert aux écrans d'attente et de pré-jointure ; seul `join_meeting` ouvre l'accès.
    Un lien invalide ou révoqué est refusé comme pour `join_meeting`."""
    resolved = resolve_link(db, settings, token)  # invalid_link / participant_excluded
    domain_meeting = resolved.meeting.to_domain()
    return PreviewResult(
        status=status_at(domain_meeting, now),
        now=now,
        opens_at=opens_at(domain_meeting),
        meeting=resolved.meeting,
        link=resolved.link,
    )


def join_meeting(
    db: Session,
    settings: Settings,
    *,
    token: str,
    display_name: str | None,
    client_id: str | None,
    now: datetime,
) -> JoinResult:
    resolved = resolve_link(db, settings, token)  # invalid_link / participant_excluded
    link, meeting = resolved.link, resolved.meeting

    domain_meeting = meeting.to_domain()
    status = status_at(domain_meeting, now)
    if status is SlotStatus.CANCELLED:
        msg = "la réunion est annulée"
        raise MeetingCancelledError(msg)
    if status is SlotStatus.ENDED:
        msg = "la réunion est terminée"
        raise MeetingEndedError(msg)
    if status is SlotStatus.NOT_OPEN_YET:
        opening = to_paris(opens_at(domain_meeting))
        msg = f"la réunion n'est pas encore ouverte (ouverture à {opening.isoformat()})"
        raise NotOpenYetError(msg, opens_at=opening.isoformat())

    # Lien nominatif : nom et identité sont ceux du lien (un `display_name` envoyé en plus est
    # ignoré). Lien générique : le nom saisi est obligatoire, l'identité vient du `client_id`.
    who = resolve_identity(
        settings, link, display_name=display_name, client_id=client_id, create_client_id=True
    )
    name = who.display_name
    link_secret = settings.link_signing_secret.get_secret_value()
    room = room_name(link_secret, meeting.id)
    if meeting.room_name != room:
        meeting.room_name = room  # retrouver la réunion depuis un webhook LiveKit
        db.commit()
    entry = request_access(db, meeting, link, who, now=now)  # participant_excluded
    if entry.status is not AccessStatus.ADMITTED:
        return JoinResult(
            status=entry.status,
            livekit_url=None,
            livekit_token=None,
            expires_at=None,
            seconds_until_end=seconds_until_end(domain_meeting, now),
            client_id=who.client_id,
            display_name=name,
            external_id=link.external_id,
            role=link.role,
            meeting=meeting,
            link=link,
        )

    cap = settings.max_participants_per_meeting
    if cap > 0 and link.role is Role.PARTICIPANT:
        connected = db.scalar(
            select(func.count())
            .select_from(ParticipantSessionRow)
            .where(
                ParticipantSessionRow.meeting_id == meeting.id,
                ParticipantSessionRow.role == Role.PARTICIPANT,
                ParticipantSessionRow.left_at.is_(None),
                ParticipantSessionRow.participant_identity != who.identity,
            )
        )
        if (connected or 0) >= cap:
            msg = f"la réunion a atteint son plafond de {cap} participants"
            raise MeetingFullError(msg)

    expires_at = livekit_expiry(now, closes_at(domain_meeting))
    livekit_token = issue_livekit_token(
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret.get_secret_value(),
        room=room,
        identity=who.identity,
        name=name,
        role=link.role,
        external_id=link.external_id,
        issued_at=now,
        expires_at=expires_at,
    )
    return JoinResult(
        status=AccessStatus.ADMITTED,
        livekit_url=client_ws_url(settings.livekit_url),
        livekit_token=livekit_token,
        expires_at=expires_at,
        seconds_until_end=seconds_until_end(domain_meeting, now),
        client_id=who.client_id,
        display_name=name,
        external_id=link.external_id,
        role=link.role,
        meeting=meeting,
        link=link,
    )
