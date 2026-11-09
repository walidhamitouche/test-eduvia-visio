"""Transcription d'une réunion : stockage, lecture, échec (besoin §5, étape 16a).

Une réunion a **au plus une** transcription (un artefact `transcript`). Elle est écrite sur le S3
en quatre fichiers qui partagent le même nom opaque :

    meetings/<id>/transcripts/<uuid>.json   (la référence, `artifact.storage_key`)
    meetings/<id>/transcripts/<uuid>.txt / .srt / .vtt

Ce partage de nom est voulu : `services.artifacts.purge_prefix` supprime les quatre d'un coup, et
la suppression par l'API (`DELETE /v1/meetings/{id}/transcript`) réutilise le mécanisme de
l'étape 15 sans rien de spécial.

Cycle d'un artefact : `begin` (ligne `pending`, clé réservée) -> `complete` (fichiers écrits, puis
ligne `ready`) ou `fail` (ligne `failed` + motif). `begin` et `complete` sont **idempotents** :
rejouer une tâche interrompue réutilise la même ligne et les mêmes noms, donc ne crée jamais de
doublon (étape 16c, « worker coupé en plein traitement »).

Les fichiers sont écrits dans l'ordre `txt`, `srt`, `vtt`, puis `json` **en dernier**, puis la
base : un lecteur ne voit jamais une transcription prête dont un fichier manque.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from visio.db.models import ArtifactKind, ArtifactRow, ArtifactStatus
from visio.domain import transcript as fmt
from visio.domain.errors import (
    ArtifactFileMissingError,
    ArtifactNotFoundError,
    ArtifactNotReadyError,
    StorageUnavailableError,
)
from visio.services import artifacts
from visio.services.storage import ObjectNotFoundError, ObjectStore, StorageError

logger = logging.getLogger("visio.transcripts")

DEFAULT_LANGUAGE = "fr"
CONTENT_TYPES = {
    "json": "application/json",
    "txt": "text/plain; charset=utf-8",
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
}


def current(db: Session, meeting_id: str) -> ArtifactRow | None:
    """La transcription de la réunion (la plus récente s'il y en avait plusieurs), ou `None`."""
    rows = artifacts.list_artifacts(db, meeting_id, ArtifactKind.TRANSCRIPT)
    return rows[-1] if rows else None


def key_for(row: ArtifactRow, extension: str) -> str:
    """Clé d'un des quatre fichiers, déduite de la clé de référence (`.json`)."""
    assert row.storage_key is not None
    return row.storage_key.rsplit(".", 1)[0] + "." + extension


# --- production (appelée par le worker, étape 16c) ---------------------------------------------


def begin(db: Session, meeting_id: str) -> ArtifactRow:
    """Ligne `pending` de la transcription, créée ou reprise. Idempotent.

    Une ligne existante (interrompue, échouée ou même prête) est **remise** à `pending` en gardant
    son nom : on la réécrit au lieu d'en ajouter une seconde.
    """
    row = current(db, meeting_id)
    if row is None:
        row = ArtifactRow(
            meeting_id=meeting_id,
            kind=ArtifactKind.TRANSCRIPT,
            status=ArtifactStatus.PENDING,
            storage_key=f"meetings/{meeting_id}/transcripts/{uuid.uuid4()}.json",
        )
        db.add(row)
    else:
        row.status = ArtifactStatus.PENDING
        row.error = None
        row.size_bytes = None
    db.commit()
    return row


def complete(
    db: Session,
    store: ObjectStore,
    row: ArtifactRow,
    segments: Sequence[fmt.Segment],
    *,
    language: str = DEFAULT_LANGUAGE,
    started_at: str | None = None,
    duration_seconds: float | None = None,
) -> ArtifactRow:
    """Écrit les quatre fichiers puis passe la ligne à `ready`. Idempotent (mêmes clés).

    `segments` doit déjà être fusionné (`domain.transcript.merge`). Une réunion sans parole
    donne une transcription **vide mais prête** : ce n'est pas une erreur.
    """
    document = fmt.to_document(
        segments, language=language, started_at=started_at, duration_seconds=duration_seconds
    )
    body = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        for extension in ("txt", "srt", "vtt"):
            text = fmt.render(segments, extension).encode("utf-8")
            store.put(key_for(row, extension), text, content_type=CONTENT_TYPES[extension])
        store.put(key_for(row, "json"), body, content_type=CONTENT_TYPES["json"])
    except StorageError as error:
        logger.warning("écriture de la transcription impossible : %s", error)
        msg = "le stockage ne répond pas, réessayer plus tard"
        raise StorageUnavailableError(msg) from None
    row.status = ArtifactStatus.READY
    row.size_bytes = len(body)
    row.error = None
    db.commit()
    logger.info(
        "transcription prête",
        extra={"meeting_id": row.meeting_id, "segments": len(segments)},
    )
    return row


def fail(db: Session, row: ArtifactRow, reason: str) -> ArtifactRow:
    """Marque la transcription en échec, avec un motif lisible."""
    row.status = ArtifactStatus.FAILED
    row.error = reason
    row.size_bytes = None
    db.commit()
    return row


# --- lecture (API) ---------------------------------------------------------------------------


def get_for_meeting(db: Session, meeting_id: str) -> ArtifactRow:
    """La transcription de la réunion, ou `ArtifactNotFoundError`."""
    row = current(db, meeting_id)
    if row is None:
        msg = "cette réunion n'a pas de transcription"
        raise ArtifactNotFoundError(msg)
    return row


def read_document(store: ObjectStore, row: ArtifactRow) -> dict[str, Any]:
    """Le document JSON d'une transcription **prête**."""
    raw = _read(store, row, "json")
    document: dict[str, Any] = json.loads(raw.decode("utf-8"))
    return document


def read_export(store: ObjectStore, row: ArtifactRow, extension: str) -> bytes:
    """Un des fichiers texte (`txt`, `srt`, `vtt`) d'une transcription **prête**."""
    return _read(store, row, extension)


def _read(store: ObjectStore, row: ArtifactRow, extension: str) -> bytes:
    if row.status is ArtifactStatus.PENDING:
        msg = "la transcription est en cours de production"
        raise ArtifactNotReadyError(msg)
    if row.status is ArtifactStatus.FAILED:
        msg = "la transcription a échoué : aucun fichier disponible"
        raise ArtifactNotReadyError(msg)
    try:
        return store.get(key_for(row, extension))
    except ObjectNotFoundError:
        logger.error("fichier de transcription absent du stockage", extra={"artifact": row.id})
        msg = "le fichier de cette transcription est introuvable dans le stockage"
        raise ArtifactFileMissingError(msg) from None
    except StorageError as error:
        logger.warning("lecture de la transcription impossible : %s", error)
        msg = "le stockage ne répond pas, réessayer plus tard"
        raise StorageUnavailableError(msg) from None


def delete_for_meeting(db: Session, store: ObjectStore, meeting_id: str) -> None:
    """Supprime définitivement la transcription (les quatre fichiers puis la ligne).

    Même règles que les enregistrements (`artifacts.delete_artifact`) : refusé tant qu'elle est
    en cours de production, S3 d'abord et base ensuite.
    """
    row = get_for_meeting(db, meeting_id)
    artifacts.delete_artifact(db, store, row.id, ArtifactKind.TRANSCRIPT)
