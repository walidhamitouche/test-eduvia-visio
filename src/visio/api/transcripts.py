"""Routes de la transcription (besoin §4 et §5, étape 16a).

- `GET /v1/meetings/{id}/transcript` : le JSON par segments, ou un export (`?format=txt|srt|vtt`) ;
- `DELETE /v1/meetings/{id}/transcript` : suppression définitive (S3 **et** base).

Même sécurité que les enregistrements : clé d'API, bucket privé, rien du S3 dans les réponses.
Les fichiers sont courts (du texte) : l'API les lit sur le S3 et les renvoie directement, sans
URL temporaire à émettre.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response

from visio.api.auth import require_api_key
from visio.api.deps import DbSession, Objects
from visio.api.errors import ErrorResponse
from visio.api.schemas import TranscriptOut, TranscriptSegmentOut, TranscriptSpeakerOut
from visio.db.models import ArtifactRow, ArtifactStatus
from visio.domain.slot import parse_iso8601, to_paris
from visio.services import meetings as meetings_service
from visio.services import transcripts as service

router = APIRouter(
    tags=["transcription"],
    dependencies=[Depends(require_api_key)],
    responses={
        401: {"model": ErrorResponse, "description": "Clé d'API absente ou invalide."},
        403: {"model": ErrorResponse, "description": "HTTPS obligatoire (production)."},
    },
)

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {
        "model": ErrorResponse,
        "description": "`meeting_not_found`, ou `artifact_not_found` (pas de transcription).",
    }
}
_STORAGE_DOWN: dict[int | str, dict[str, Any]] = {
    502: {"model": ErrorResponse, "description": "`storage_unavailable` : le S3 ne répond pas."}
}


def _present(row: ArtifactRow, document: dict[str, Any] | None) -> TranscriptOut:
    started_at = document.get("started_at") if document else None
    return TranscriptOut(
        id=row.id,
        meeting_id=row.meeting_id,
        status=row.status,
        error=row.error,
        created_at=to_paris(row.created_at),
        language=document["language"] if document else None,
        started_at=to_paris(parse_iso8601(started_at)) if started_at else None,
        duration_seconds=document["duration_seconds"] if document else None,
        speakers=[TranscriptSpeakerOut(**s) for s in document["speakers"]] if document else [],
        segments=[TranscriptSegmentOut(**s) for s in document["segments"]] if document else [],
    )


@router.get(
    "/v1/meetings/{meeting_id}/transcript",
    response_model=TranscriptOut,
    summary="Transcription d'une réunion",
    description="Par défaut, le JSON : segments `{start, end, speaker, external_id, text}` "
    "horodatés (secondes depuis le début de l'enregistrement) et attribués à la personne qui "
    "parle. Avec `?format=txt`, `srt` ou `vtt`, le même contenu en texte brut ou en sous-titres. "
    "Une transcription `pending` ou `failed` répond `200` avec son état en JSON (sans segments), "
    "mais `409 artifact_not_ready` pour un export. Une réunion sans parole donne une "
    "transcription prête et vide.",
    responses={
        200: {
            "description": "JSON (défaut), ou texte brut / SRT / VTT selon `format`.",
            "content": {
                "text/plain": {"schema": {"type": "string"}},
                "application/x-subrip": {"schema": {"type": "string"}},
                "text/vtt": {"schema": {"type": "string"}},
            },
        },
        409: {"model": ErrorResponse, "description": "`artifact_not_ready` (export demandé)."},
        **_NOT_FOUND,
        **_STORAGE_DOWN,
    },
)
def get_transcript(
    meeting_id: str,
    db: DbSession,
    store: Objects,
    format: Annotated[
        Literal["json", "txt", "srt", "vtt"], Query(description="Format de sortie.")
    ] = "json",
) -> Response | TranscriptOut:
    meeting = meetings_service.get_meeting(db, meeting_id)
    row = service.get_for_meeting(db, meeting.id)
    if format == "json":
        document = service.read_document(store, row) if row.status is ArtifactStatus.READY else None
        return _present(row, document)
    body = service.read_export(store, row, format)
    return Response(
        content=body,
        media_type=service.CONTENT_TYPES[format],
        headers={
            "Content-Disposition": f'attachment; filename="transcription-{row.id}.{format}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    "/v1/meetings/{meeting_id}/transcript",
    status_code=204,
    response_class=Response,
    summary="Supprimer la transcription",
    description="Suppression **définitive** : les fichiers (JSON, texte, SRT, VTT) sont retirés "
    "du S3 puis la ligne est supprimée en base. Refusée (`409`) tant que la transcription est "
    "en cours de production. Si le S3 ne répond pas, rien n'est supprimé en base : rejouer "
    "l'appel plus tard.",
    responses={
        **_NOT_FOUND,
        409: {"model": ErrorResponse, "description": "`artifact_not_ready` : encore en cours."},
        **_STORAGE_DOWN,
    },
)
def delete_transcript(meeting_id: str, db: DbSession, store: Objects) -> Response:
    meeting = meetings_service.get_meeting(db, meeting_id)
    service.delete_for_meeting(db, store, meeting.id)
    return Response(status_code=204)


__all__ = ["router"]
