"""Temps de présence (émargement, besoin §5) : logique pure.

Règle : le temps de présence d'une personne est la durée de l'**union** de ses intervalles de
connexion. Deux connexions qui se chevauchent (deux onglets, reconnexion avant la détection de
la sortie précédente) ne comptent qu'une fois ; une connexion sans sortie connue court
jusqu'à `until` (maintenant, ou la fin de la réunion si elle est passée).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime


def merge_intervals(
    intervals: Iterable[tuple[datetime, datetime | None]], *, until: datetime
) -> list[tuple[datetime, datetime]]:
    """Intervalles fermés à `until`, tronqués à `until`, sans les vides, fusionnés et triés."""
    closed: list[tuple[datetime, datetime]] = []
    for start, end in intervals:
        stop = min(end, until) if end is not None else until
        if stop > start:
            closed.append((start, stop))
    closed.sort()
    merged: list[tuple[datetime, datetime]] = []
    for start, stop in closed:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], stop))
        else:
            merged.append((start, stop))
    return merged


def presence_seconds(
    intervals: Iterable[tuple[datetime, datetime | None]], *, until: datetime
) -> int:
    """Durée totale en secondes entières (arrondie à la seconde inférieure)."""
    total = sum(
        (stop - start).total_seconds() for start, stop in merge_intervals(intervals, until=until)
    )
    return int(total)
