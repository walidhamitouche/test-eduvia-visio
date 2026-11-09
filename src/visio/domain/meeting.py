"""Réunion telle que la voit la règle du créneau : des données, aucune base, aucune horloge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MeetingState(StrEnum):
    """État enregistré (changé par des événements : première connexion, clôture, annulation)."""

    SCHEDULED = "scheduled"
    LIVE = "live"
    ENDED = "ended"
    CANCELLED = "cancelled"


class SlotStatus(StrEnum):
    """Statut déduit de l'état **et** de l'heure : ce que voit quelqu'un qui ouvre un lien."""

    NOT_OPEN_YET = "not_open_yet"
    OPEN = "open"
    ENDED = "ended"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Meeting:
    id: str
    starts_at: datetime
    ends_at: datetime
    early_join_minutes: int = 0
    state: MeetingState = MeetingState.SCHEDULED
    # Heure de clôture anticipée (organisateur ou API). `None` tant que non clôturée.
    ended_at: datetime | None = None
