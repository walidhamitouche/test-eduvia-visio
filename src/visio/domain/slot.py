"""Règle centrale du créneau (besoin §2) : logique pure, horloge injectée.

Règles :
- Une réunion est ouvrable de `starts_at - early_join_minutes` (inclus) jusqu'à sa fin (exclue).
- La fin est `ends_at`, ou l'heure de clôture anticipée si elle est antérieure.
- À l'heure de fin pile, c'est fini : 17:59:59 accepté, 18:00:00 refusé.
- Annulée ou terminée : refusée, quelle que soit l'heure.
- Toutes les dates portent un décalage horaire (ISO 8601). Les calculs se font sur des instants
  absolus : un créneau qui chevauche un changement d'heure garde sa vraie durée.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from visio.domain.errors import InvalidSlotError
from visio.domain.meeting import Meeting, MeetingState, SlotStatus

PARIS = ZoneInfo("Europe/Paris")

# Décisions du 2026-11-02 (DECISIONS.md §6 point 4) : plafond de 12 h, `invalid_slot` au-delà.
MAX_DURATION = timedelta(hours=12)
# Plafond de la marge d'ouverture anticipée (valeur proposée à l'étape 3, à confirmer).
MAX_EARLY_JOIN_MINUTES = 60


def _has_offset(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def parse_iso8601(value: str) -> datetime:
    """Lit une date ISO 8601 **avec décalage** (`+01:00`, `Z`...) et la renvoie en UTC.

    Une date sans décalage est refusée : « 16:00 » sans fuseau est ambigu.
    """
    if not isinstance(value, str):
        msg = "la date doit être une chaîne ISO 8601"
        raise InvalidSlotError(msg)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        msg = f"date ISO 8601 invalide : {value!r}"
        raise InvalidSlotError(msg) from None
    if not _has_offset(parsed):
        msg = f"la date doit comporter un décalage horaire (ex. +01:00) : {value!r}"
        raise InvalidSlotError(msg)
    return parsed.astimezone(UTC)


def to_paris(value: datetime) -> datetime:
    """Convertit un instant en heure de Paris (affichage de l'interface)."""
    if not _has_offset(value):
        msg = "la date doit comporter un décalage horaire"
        raise InvalidSlotError(msg)
    return value.astimezone(PARIS)


def validate_slot(starts_at: datetime, ends_at: datetime, early_join_minutes: int = 0) -> None:
    """Refuse un créneau incohérent en levant `InvalidSlotError` (code `invalid_slot`)."""
    if not _has_offset(starts_at) or not _has_offset(ends_at):
        msg = "starts_at et ends_at doivent comporter un décalage horaire"
        raise InvalidSlotError(msg)
    if ends_at <= starts_at:
        msg = "ends_at doit être strictement postérieur à starts_at"
        raise InvalidSlotError(msg)
    if ends_at - starts_at > MAX_DURATION:
        hours = int(MAX_DURATION.total_seconds() // 3600)
        msg = f"la durée d'une réunion ne peut pas dépasser {hours} h"
        raise InvalidSlotError(msg)
    if isinstance(early_join_minutes, bool) or not isinstance(early_join_minutes, int):
        msg = "early_join_minutes doit être un entier"
        raise InvalidSlotError(msg)
    if not 0 <= early_join_minutes <= MAX_EARLY_JOIN_MINUTES:
        msg = f"early_join_minutes doit être compris entre 0 et {MAX_EARLY_JOIN_MINUTES}"
        raise InvalidSlotError(msg)


def opens_at(meeting: Meeting) -> datetime:
    """Premier instant où l'on peut rejoindre (début moins la marge d'ouverture anticipée)."""
    return meeting.starts_at - timedelta(minutes=meeting.early_join_minutes)


def closes_at(meeting: Meeting) -> datetime:
    """Instant de coupure : `ends_at`, ou la clôture anticipée si elle est plus tôt."""
    if meeting.ended_at is not None and meeting.ended_at < meeting.ends_at:
        return meeting.ended_at
    return meeting.ends_at


def status_at(meeting: Meeting, now: datetime) -> SlotStatus:
    """Statut de la réunion à l'instant `now` (l'état annulé et terminé priment sur l'heure)."""
    if meeting.state is MeetingState.CANCELLED:
        return SlotStatus.CANCELLED
    if meeting.state is MeetingState.ENDED or now >= closes_at(meeting):
        return SlotStatus.ENDED
    if now < opens_at(meeting):
        return SlotStatus.NOT_OPEN_YET
    return SlotStatus.OPEN


def lifecycle_status(meeting: Meeting, now: datetime) -> MeetingState:
    """État de la réunion **tel que l'API le montre** : planifiée ou en cours dont l'heure de
    fin est passée est déjà `ended`, même si le planificateur (étape 10) ne l'a pas encore
    enregistré. Les autres états sont ceux de la base."""
    if meeting.state in (MeetingState.SCHEDULED, MeetingState.LIVE) and now >= closes_at(meeting):
        return MeetingState.ENDED
    return meeting.state


def can_join(meeting: Meeting, now: datetime) -> bool:
    return status_at(meeting, now) is SlotStatus.OPEN


def seconds_until_end(meeting: Meeting, now: datetime) -> int:
    """Secondes restantes avant la coupure, arrondies à la seconde supérieure.

    Vaut 0 si et seulement si la réunion est terminée ou annulée : tant qu'elle n'est pas
    coupée, il reste au moins 1 s (utile pour les avertissements de fin à 5 min et 1 min).
    """
    if status_at(meeting, now) in (SlotStatus.ENDED, SlotStatus.CANCELLED):
        return 0
    return math.ceil((closes_at(meeting) - now).total_seconds())


def has_started(meeting: Meeting, now: datetime) -> bool:
    """Une réunion « a commencé » dès que quelqu'un s'est connecté (état `live`), qu'elle est
    terminée, ou que l'heure de début est passée. Sert à `PATCH` et `DELETE` (409 ensuite)."""
    return meeting.state in (MeetingState.LIVE, MeetingState.ENDED) or now >= meeting.starts_at


def can_modify(meeting: Meeting, now: datetime) -> bool:
    """Modification ou annulation autorisées : planifiée et pas encore commencée."""
    return meeting.state is MeetingState.SCHEDULED and not has_started(meeting, now)
