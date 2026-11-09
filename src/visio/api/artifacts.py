"""Routes des enregistrements (besoin §4 et §5, étape 15).

- `GET /v1/meetings/{id}/recordings` : la liste, avec une URL de téléchargement (15 min) par
  enregistrement prêt ;
- `GET /v1/recordings/{id}` : un enregistrement, avec une URL fraîche (pratique après le
  webhook `recording.ready`, qui donne l'identifiant) ;
- `DELETE /v1/recordings/{id}` : suppression définitive (fichier S3 **et** base) ;
- `GET /v1/downloads/{jeton}` : le téléchargement lui-même, **sans clé d'API** (c'est le jeton
  qui fait foi, comme un lien de visio), diffusé par l'API depuis le S3 privé.

Les routes de la transcription sont dans `api/transcripts.py` (étape 16a).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from visio.api.auth import require_api_key, require_https
from visio.api.deps import AppClock, AppSettings, DbSession, Objects
from visio.api.errors import ErrorResponse
from visio.api.schemas import RecordingOut, RecordingsOut
from visio.config import Settings
from visio.db.models import ArtifactKind, ArtifactRow
from visio.domain.slot import to_paris
from visio.services import artifacts as service
from visio.services import download_tokens
from visio.services import meetings as meetings_service

logger = logging.getLogger("visio.artifacts")

router = APIRouter(
    tags=["enregistrements"],
    dependencies=[Depends(require_api_key)],
    responses={
        401: {"model": ErrorResponse, "description": "Clé d'API absente ou invalide."},
        403: {"model": ErrorResponse, "description": "HTTPS obligatoire (production)."},
    },
)

# Le téléchargement est public : le jeton de l'URL fait foi (HTTPS tout de même obligatoire).
downloads = APIRouter(tags=["enregistrements"], dependencies=[Depends(require_https)])

_RECORDING_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "`artifact_not_found`."}
}
_STORAGE_DOWN: dict[int | str, dict[str, Any]] = {
    502: {"model": ErrorResponse, "description": "`storage_unavailable` : le S3 ne répond pas."}
}


def _present(settings: Settings, artifact: ArtifactRow, now: datetime) -> RecordingOut:
    grant = service.issue_download(settings, artifact, now=now)
    return RecordingOut(
        id=artifact.id,
        meeting_id=artifact.meeting_id,
        status=artifact.status,
        size_bytes=artifact.size_bytes,
        error=artifact.error,
        created_at=to_paris(artifact.created_at),
        download_url=None if grant is None else download_tokens.download_url(settings, grant),
        download_url_expires_at=None if grant is None else to_paris(grant.expires_at),
    )


@router.get(
    "/v1/meetings/{meeting_id}/recordings",
    response_model=RecordingsOut,
    summary="Enregistrements d'une réunion",
    description="Liste les enregistrements (du plus ancien au plus récent). Chacun qui est prêt "
    "porte une `download_url` **valable 15 minutes au plus**, servie par l'API et non par le "
    "S3 ; en redemander une à chaque besoin. Un enregistrement `pending` (en cours) ou `failed` "
    "n'a pas d'URL. Liste vide si la réunion n'est pas enregistrée ou n'a pas démarré.",
    responses={404: {"model": ErrorResponse, "description": "`meeting_not_found`."}},
)
def list_recordings(
    meeting_id: str, db: DbSession, clock: AppClock, settings: AppSettings
) -> RecordingsOut:
    meeting = meetings_service.get_meeting(db, meeting_id)
    now = clock.now()
    rows = service.list_artifacts(db, meeting.id, ArtifactKind.RECORDING)
    return RecordingsOut(
        meeting_id=meeting.id, recordings=[_present(settings, row, now) for row in rows]
    )


@router.get(
    "/v1/recordings/{recording_id}",
    response_model=RecordingOut,
    summary="Un enregistrement",
    description="Même contenu qu'un élément de la liste, avec une URL de téléchargement fraîche "
    "s'il est prêt. Pratique après le webhook `recording.ready`, qui donne `recording_id`.",
    responses=_RECORDING_NOT_FOUND,
)
def get_recording(
    recording_id: str, db: DbSession, clock: AppClock, settings: AppSettings
) -> RecordingOut:
    artifact = service.get_artifact(db, recording_id, ArtifactKind.RECORDING)
    return _present(settings, artifact, clock.now())


@router.delete(
    "/v1/recordings/{recording_id}",
    status_code=204,
    response_class=Response,
    summary="Supprimer un enregistrement",
    description="Suppression **définitive** : le fichier est retiré du S3 (avec le manifeste et "
    "les éventuels segments qui l'accompagnent, et toutes les versions d'un bucket versionné) "
    "puis la ligne est supprimée en base. Toute URL de téléchargement déjà émise répond alors "
    "`410 artifact_deleted`. Refusé tant que l'enregistrement est `pending`. Si le S3 ne répond "
    "pas, rien n'est supprimé en base : rejouer l'appel plus tard.",
    responses={
        **_RECORDING_NOT_FOUND,
        409: {"model": ErrorResponse, "description": "`artifact_not_ready` : encore en cours."},
        **_STORAGE_DOWN,
    },
)
def delete_recording(recording_id: str, db: DbSession, store: Objects) -> Response:
    service.delete_artifact(db, store, recording_id, ArtifactKind.RECORDING)
    return Response(status_code=204)


# --- téléchargement --------------------------------------------------------------------------

_DOWNLOAD_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Le fichier entier.",
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    },
    206: {
        "description": "Une plage d'octets (en-tête `Range`), avec `Content-Range`.",
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    },
    404: {
        "model": ErrorResponse,
        "description": "`invalid_download_link` (URL inventée ou modifiée) ou "
        "`artifact_file_missing`.",
    },
    409: {"model": ErrorResponse, "description": "`artifact_not_ready`."},
    410: {
        "model": ErrorResponse,
        "description": "`download_link_expired` (plus de 15 min) ou `artifact_deleted`.",
    },
    416: {"model": ErrorResponse, "description": "`range_not_satisfiable` (avec `Content-Range`)."},
    **_STORAGE_DOWN,
}


def _headers(plan: service.DownloadPlan) -> dict[str, str]:
    headers = {
        "Content-Length": str(max(plan.length, 0)),
        "Content-Disposition": f'attachment; filename="{plan.filename}"',
        "Accept-Ranges": "bytes",
        "ETag": plan.etag,
        # Le jeton est dans l'URL : rien ne doit être gardé en cache ni transmis en `Referer`.
        "Cache-Control": "private, no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }
    if plan.partial:
        headers["Content-Range"] = f"bytes {plan.start}-{plan.end}/{plan.size}"
    return headers


@downloads.get(
    "/v1/downloads/{token}",
    response_class=StreamingResponse,
    summary="Télécharger un artefact",
    description="Sert le fichier d'une URL de téléchargement émise par l'API (15 minutes au "
    "plus). **Aucune clé d'API** : le jeton de l'URL fait foi. Gère `Range` (reprise d'un "
    "téléchargement interrompu, lecture vidéo) sur une plage simple. L'artefact est relu en base "
    "à chaque requête : une URL émise avant la suppression ne fonctionne plus (`410`). Un "
    "téléchargement déjà commencé quand la suppression a lieu peut aller à son terme.",
    responses=_DOWNLOAD_RESPONSES,
)
def download(
    token: str,
    request: Request,
    db: DbSession,
    clock: AppClock,
    settings: AppSettings,
    store: Objects,
) -> StreamingResponse:
    plan = service.prepare_download(
        db,
        store,
        settings,
        token,
        now=clock.now(),
        range_header=request.headers.get("range"),
        if_range=request.headers.get("if-range"),
    )
    stream = service.open_download(store, plan)
    logger.info(
        "téléchargement d'artefact",
        extra={"artifact": plan.artifact_id, "status": plan.status_code, "bytes": plan.length},
    )
    return StreamingResponse(
        iter(()) if stream is None else stream.chunks(),
        status_code=plan.status_code,
        media_type=plan.content_type,
        headers=_headers(plan),
    )


@downloads.head("/v1/downloads/{token}", include_in_schema=False)
def download_head(
    token: str,
    request: Request,
    db: DbSession,
    clock: AppClock,
    settings: AppSettings,
    store: Objects,
) -> Response:
    """Mêmes vérifications et mêmes en-têtes que `GET`, sans corps ni lecture du fichier."""
    plan = service.prepare_download(
        db,
        store,
        settings,
        token,
        now=clock.now(),
        range_header=request.headers.get("range"),
        if_range=request.headers.get("if-range"),
    )
    return Response(
        status_code=plan.status_code, media_type=plan.content_type, headers=_headers(plan)
    )
