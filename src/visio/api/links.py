"""Route `POST /v1/meetings/{id}/links` (besoin §4) : liens nominatifs.

Les liens ne sont renvoyés qu'à leur création (réunion, ou cette route) et jamais relus.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response

from visio.api.auth import require_api_key
from visio.api.deps import AppClock, AppSettings, DbSession
from visio.api.errors import ErrorResponse
from visio.api.schemas import CreateLinkIn, LinkOut
from visio.domain.roles import Role
from visio.domain.slot import to_paris
from visio.services import links as service
from visio.services.links import issue_link_url

router = APIRouter(
    prefix="/v1/meetings/{meeting_id}/links",
    tags=["liens"],
    dependencies=[Depends(require_api_key)],
    responses={
        401: {"model": ErrorResponse, "description": "Clé d'API absente ou invalide."},
        403: {"model": ErrorResponse, "description": "HTTPS obligatoire (production)."},
    },
)


@router.post(
    "",
    status_code=201,
    response_model=LinkOut,
    summary="Générer un lien nominatif",
    description="Crée un lien (participant ou organisateur) avec un nom pré-rempli et un "
    "identifiant externe. Le nom apparaît dans la visio, la présence et la transcription. "
    "Refusé si la réunion est annulée ou terminée. Au plus 10 liens organisateur actifs.",
    responses={
        404: {"model": ErrorResponse, "description": "`meeting_not_found`."},
        409: {
            "model": ErrorResponse,
            "description": "`meeting_cancelled`, `meeting_ended` ou `too_many_organizers`.",
        },
        422: {"model": ErrorResponse, "description": "`validation_error`."},
    },
)
def create_link(
    meeting_id: str,
    body: CreateLinkIn,
    response: Response,
    db: DbSession,
    clock: AppClock,
    settings: AppSettings,
) -> dict[str, Any]:
    link = service.create_link(
        db,
        meeting_id,
        role=Role(body.role),
        display_name=body.display_name,
        external_id=body.external_id,
        now=clock.now(),
    )
    return {
        "id": link.id,
        "role": link.role,
        "display_name": link.display_name,
        "external_id": link.external_id,
        "url": issue_link_url(settings, link),
        "created_at": to_paris(link.created_at),
    }
