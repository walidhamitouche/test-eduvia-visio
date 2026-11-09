"""Routes de santé (besoin §7 « observabilité », étape 17b).

- `GET /health` : **public**, vivant ou non. Aucune dépendance sondée : c'est la sonde de
  Docker / Coolify, qui ne doit pas faire redémarrer l'API parce que le S3 est en panne.
- `GET /v1/health` : **clé d'API**. Base, LiveKit, S3 et file de tâches ; code 503 si quelque chose
  ne va pas, pour qu'une supervision (Uptime Kuma, etc.) alerte sans lire le corps.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from visio import __version__
from visio.api.auth import require_api_key
from visio.api.deps import AppClock, Objects, Rooms
from visio.api.errors import ErrorResponse
from visio.api.schemas import (
    DependencyHealthOut,
    HealthChecksOut,
    HealthDetailOut,
    JobsQueueOut,
    QueueHealthOut,
    WebhooksQueueOut,
)
from visio.domain.slot import to_paris
from visio.services import health as service

router = APIRouter(tags=["exploitation"])

detail = APIRouter(
    prefix="/v1",
    tags=["exploitation"],
    dependencies=[Depends(require_api_key)],
    responses={
        401: {"model": ErrorResponse, "description": "Clé d'API absente ou invalide."},
        403: {"model": ErrorResponse, "description": "HTTPS obligatoire (production)."},
    },
)

_UNHEALTHY: dict[int | str, dict[str, Any]] = {
    503: {
        "model": HealthDetailOut,
        "description": "`degraded` ou `down` : même corps que le 200, pour lire ce qui ne va pas.",
    }
}


@router.get("/health")
def health() -> dict[str, str]:
    """Le service répond. Rien de plus : voir `GET /v1/health` pour les dépendances."""
    return {"status": "ok"}


def _dependency(check: service.DependencyCheck) -> DependencyHealthOut:
    return DependencyHealthOut(
        status=check.status, latency_ms=check.latency_ms, detail=check.detail
    )


def _queue(report: service.QueueReport | None) -> QueueHealthOut | None:
    if report is None:
        return None
    return QueueHealthOut(
        status=report.status,
        jobs=JobsQueueOut(**asdict(report.jobs)),
        webhooks=WebhooksQueueOut(**asdict(report.webhooks)),
        artifacts_failed=report.artifacts_failed,
    )


@detail.get("/health", response_model=HealthDetailOut, responses=_UNHEALTHY)
async def health_detail(
    request: Request, response: Response, clock: AppClock, rooms: Rooms, objects: Objects
) -> HealthDetailOut:
    """État des dépendances (base, LiveKit, S3) et de la file de tâches.

    200 si tout va bien, **503** sinon (`degraded` ou `down`). Ne contient que des états, des
    compteurs et des durées.
    """
    report = await service.check_health(
        request.app.state.session_factory, rooms, objects, now=clock.now()
    )
    if not report.healthy:
        response.status_code = 503
    return HealthDetailOut(
        status=report.status,
        version=__version__,
        checked_at=to_paris(report.checked_at),
        checks=HealthChecksOut(
            database=_dependency(report.database),
            livekit=_dependency(report.livekit),
            storage=_dependency(report.storage),
        ),
        queue=_queue(report.queue),
    )
