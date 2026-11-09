"""Politique de relance commune aux tâches de la table `jobs` (salle, enregistrement)."""

from __future__ import annotations

from datetime import timedelta

JOB_RETRY_DELAYS = (
    timedelta(seconds=5),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(hours=1),
)
JOB_MAX_ATTEMPTS = len(JOB_RETRY_DELAYS) + 1
