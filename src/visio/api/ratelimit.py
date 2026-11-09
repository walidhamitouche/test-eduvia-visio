"""Limitation de débit des routes publiques (`/v1/join`), par adresse IP, fenêtre glissante d'1 min.

En mémoire (une seule instance de l'API, voir DECISIONS.md). Derrière Coolify l'IP vient de
`--proxy-headers` (uvicorn)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from visio.api.errors import ApiError

WINDOW_SECONDS = 60.0
_MAX_KEYS = 50_000


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, now: float) -> bool:
        with self._lock:
            if len(self._hits) > _MAX_KEYS:  # garde-fou mémoire
                self._hits.clear()
            hits = self._hits[key]
            while hits and now - hits[0] > WINDOW_SECONDS:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True


_limiter = RateLimiter()


def limit_requests(request: Request) -> None:
    limit = request.app.state.settings.join_rate_limit_per_minute
    if limit <= 0:
        return
    client = request.client.host if request.client else "inconnu"
    if not _limiter.allow(client, limit, time.monotonic()):
        raise ApiError(
            429,
            "rate_limited",
            "trop de requêtes : réessayer dans une minute",
            headers={"Retry-After": "60"},
        )
