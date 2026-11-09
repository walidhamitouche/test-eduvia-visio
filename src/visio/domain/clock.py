"""Horloge injectable : la règle du créneau ne lit jamais l'heure système elle-même."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Source de l'heure courante (toujours avec fuseau)."""

    def now(self) -> datetime: ...


class SystemClock:
    """Horloge réelle, en UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Horloge simulée pour les tests : l'heure ne bouge que si on la déplace."""

    def __init__(self, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            msg = "l'heure simulée doit avoir un décalage horaire"
            raise ValueError(msg)
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            msg = "l'heure simulée doit avoir un décalage horaire"
            raise ValueError(msg)
        self._now = now
