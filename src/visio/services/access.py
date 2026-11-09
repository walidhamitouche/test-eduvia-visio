"""Qui peut entrer, sous quel nom (étapes 8 et 9) : identités, salle d'attente, exclusion.

Une ligne `access_entries` par personne et par réunion. Elle est créée à la première demande
d'entrée (`POST /v1/join`) et sert ensuite de **source de vérité** :
- `admitted` : peut obtenir un jeton de salle ;
- `waiting` : attend l'organisateur (salle d'attente) — **aucun jeton** n'est émis ;
- `denied` : refusée par un organisateur — pas de jeton tant qu'aucun organisateur ne la réadmet ;
- `excluded` : exclue définitivement — refusée à chaque demande (`participant_excluded`).

Un lien nominatif exclu est en plus révoqué (liste de révocation, étape 5). Sur le lien
générique, partagé, c'est l'identité de la personne (dérivée de son `client_id`) qui est exclue.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visio.config import Settings
from visio.db.models import AccessRow, AccessStatus, LinkRow, MeetingRow
from visio.domain.errors import DisplayNameRequiredError, LinkRevokedError, NotAdmittedError
from visio.domain.roles import Role
from visio.services.livekit_tokens import generic_identity, named_identity

# Une demande d'entrée est relue toutes les 2 s par le navigateur de la personne : au-delà de ce
# délai sans nouvelles, elle a fermé la page et n'est plus montrée à l'organisateur.
LOBBY_FRESH_FOR = timedelta(seconds=20)


@dataclass(frozen=True, slots=True)
class Identity:
    identity: str
    display_name: str
    client_id: str | None  # seulement sur le lien générique (renvoyé au navigateur)


def resolve_identity(
    settings: Settings,
    link: LinkRow,
    *,
    display_name: str | None,
    client_id: str | None,
    create_client_id: bool,
) -> Identity:
    """Identité opaque de la personne qui utilise ce lien (voir `livekit_tokens`).

    Lien nominatif : stable, celle du lien. Lien générique : dérivée du `client_id` de son
    navigateur ; `create_client_id` autorise à en créer un (entrée), sinon il est exigé (chat).
    """
    secret = settings.link_signing_secret.get_secret_value()
    if link.display_name is not None:
        return Identity(named_identity(secret, link.id), link.display_name, None)
    if display_name is None:
        msg = "ce lien est générique : display_name est obligatoire"
        raise DisplayNameRequiredError(msg)
    if client_id is None:
        if not create_client_id:
            msg = "client_id est obligatoire sur un lien générique"
            raise NotAdmittedError(msg)
        client_id = secrets.token_hex(16)
    return Identity(generic_identity(secret, link.id, client_id), display_name, client_id)


def normalize_name(name: str) -> str:
    """Nom comparable : sans accents ni casse, espaces réduits."""
    plain = unicodedata.normalize("NFKD", name)
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plain).strip().casefold()


def _name_is_excluded(db: Session, meeting_id: str, name: str) -> bool:
    """Un nom exclu d'un lien générique reste exclu : sans compte, c'est le seul recours contre
    quelqu'un qui vide son navigateur ou ouvre une fenêtre privée (le nom est visible de tous)."""
    wanted = normalize_name(name)
    excluded = db.scalars(
        select(AccessRow.display_name).where(
            AccessRow.meeting_id == meeting_id,
            AccessRow.status == AccessStatus.EXCLUDED,
            AccessRow.external_id.is_(None),
        )
    ).all()
    return any(normalize_name(n) == wanted for n in excluded)


def get_entry(db: Session, meeting_id: str, identity: str) -> AccessRow | None:
    return db.scalar(
        select(AccessRow).where(
            AccessRow.meeting_id == meeting_id, AccessRow.participant_identity == identity
        )
    )


def request_access(
    db: Session, meeting: MeetingRow, link: LinkRow, who: Identity, *, now: datetime
) -> AccessRow:
    """Crée ou rafraîchit la demande d'entrée. Refuse (403) une personne exclue.

    Premier passage : `admitted` d'office, sauf un participant d'une réunion à salle d'attente
    (`waiting`). Les organisateurs entrent toujours directement.
    """
    entry = get_entry(db, meeting.id, who.identity)
    # Contrôle du nom à CHAQUE passage sur lien générique (pas seulement au premier) : un navigateur
    # déjà connu (cookie) qui change de nom ne doit pas contourner l'exclusion d'un nom.
    if link.display_name is None and _name_is_excluded(db, meeting.id, who.display_name):
        msg = "vous avez été exclu de cette réunion"
        raise LinkRevokedError(msg)
    if entry is None:
        waits = meeting.waiting_room and link.role is Role.PARTICIPANT
        entry = AccessRow(
            meeting_id=meeting.id,
            link_id=link.id,
            participant_identity=who.identity,
            display_name=who.display_name,
            external_id=link.external_id,
            role=link.role,
            status=AccessStatus.WAITING if waits else AccessStatus.ADMITTED,
            requested_at=now,
            last_seen_at=now,
        )
        db.add(entry)
        try:
            db.commit()
        except IntegrityError:  # deux demandes simultanées de la même personne
            db.rollback()
            entry = get_entry(db, meeting.id, who.identity)
            if entry is None:
                raise
    if entry.status is AccessStatus.EXCLUDED:
        msg = "vous avez été exclu de cette réunion"
        raise LinkRevokedError(msg)
    entry.last_seen_at = now
    if entry.display_name != who.display_name and link.display_name is None:
        entry.display_name = who.display_name  # lien générique : le nom saisi fait foi
    db.commit()
    return entry


def waiting_entries(db: Session, meeting_id: str, *, now: datetime) -> list[AccessRow]:
    """Demandes à traiter : en attente et encore relues, puis refusées (réadmissibles)."""
    rows = db.scalars(
        select(AccessRow)
        .where(
            AccessRow.meeting_id == meeting_id,
            AccessRow.status.in_([AccessStatus.WAITING, AccessStatus.DENIED]),
        )
        .order_by(AccessRow.requested_at)
    ).all()
    return [
        row
        for row in rows
        if row.status is AccessStatus.DENIED or now - row.last_seen_at <= LOBBY_FRESH_FOR
    ]
