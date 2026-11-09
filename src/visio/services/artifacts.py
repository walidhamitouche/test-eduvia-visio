"""Artefacts d'une réunion (enregistrements, transcriptions) : lister, télécharger, supprimer
(besoin §5, étape 15). Aucune dépendance à FastAPI : session, stockage et heure sont passés en
paramètres, ce qui permet de tout tester avec une horloge simulée.

**Téléchargement par le proxy de l'API, jamais par une URL S3.** Le bucket est privé. Une URL de
téléchargement (`download_tokens`) pointe sur l'API, qui à **chaque** requête relit l'artefact en
base, vérifie qu'il est prêt, puis diffuse le fichier depuis le S3. Le besoin exige qu'« un
artefact supprimé ne soit plus récupérable par aucune URL, y compris une URL temporaire déjà
émise » : une URL S3 présignée vit jusqu'à son expiration quoi qu'on fasse, alors qu'ici la
suppression de la ligne en base suffit à tuer toutes les URL émises, à la seconde.

**Suppression** (`delete_artifact`), dans cet ordre :
1. l'artefact doit exister, être du bon type et ne plus être `pending` ;
2. on supprime du S3 le fichier **et tout ce qui porte le même chemin opaque** (manifeste
   d'Egress, segments non fusionnés, anciennes versions d'un bucket versionné), puis on vérifie
   que le fichier a bien disparu ;
3. seulement alors, on supprime la ligne en base.
Si le S3 échoue, la base n'est pas touchée : l'appel peut être rejoué (le S3 répond 204 pour un
objet déjà absent) et on ne se retrouve jamais avec un fichier orphelin dont plus rien ne parle.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from visio.config import Settings
from visio.db.models import ArtifactKind, ArtifactRow, ArtifactStatus
from visio.domain.byte_range import parse_range
from visio.domain.errors import (
    ArtifactDeletedError,
    ArtifactFileMissingError,
    ArtifactNotFoundError,
    ArtifactNotReadyError,
    StorageUnavailableError,
)
from visio.services import download_tokens
from visio.services.download_tokens import DownloadGrant
from visio.services.storage import ObjectNotFoundError, ObjectStore, StorageError, StoredRange

logger = logging.getLogger("visio.artifacts")

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# Type de contenu selon l'extension de la clé d'objet (la clé est décidée par nous).
CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".srt": "application/x-subrip",
    ".vtt": "text/vtt",
}
FILE_LABELS = {ArtifactKind.RECORDING: "enregistrement", ArtifactKind.TRANSCRIPT: "transcription"}


# --- lecture ---------------------------------------------------------------------------------


def list_artifacts(db: Session, meeting_id: str, kind: ArtifactKind) -> list[ArtifactRow]:
    """Les artefacts d'un type pour une réunion, du plus ancien au plus récent."""
    return list(
        db.scalars(
            select(ArtifactRow)
            .where(ArtifactRow.meeting_id == meeting_id, ArtifactRow.kind == kind)
            .order_by(ArtifactRow.created_at, ArtifactRow.id)
        ).all()
    )


def get_artifact(db: Session, artifact_id: str, kind: ArtifactKind) -> ArtifactRow:
    """L'artefact, ou `ArtifactNotFoundError`. Un identifiant d'un autre type est « introuvable »
    (un identifiant de transcription ne désigne pas un enregistrement)."""
    artifact = db.get(ArtifactRow, artifact_id)
    if artifact is None or artifact.kind is not kind:
        msg = "aucun artefact avec cet identifiant"
        raise ArtifactNotFoundError(msg)
    return artifact


def is_downloadable(artifact: ArtifactRow) -> bool:
    return artifact.status is ArtifactStatus.READY and bool(artifact.storage_key)


def issue_download(
    settings: Settings, artifact: ArtifactRow, *, now: datetime
) -> DownloadGrant | None:
    """URL de téléchargement (15 min) d'un artefact prêt ; `None` s'il n'y a rien à télécharger
    (en cours de production ou échoué)."""
    if not is_downloadable(artifact):
        return None
    return download_tokens.issue(settings, artifact.id, now=now)


# --- téléchargement --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DownloadPlan:
    """Ce qui sera servi, décidé avant d'ouvrir le moindre flux : statut, octets, en-têtes."""

    artifact_id: str
    key: str
    size: int  # taille totale du fichier
    start: int
    end: int  # inclus ; `start - 1` pour un fichier vide
    partial: bool  # vrai : 206 + Content-Range
    content_type: str
    filename: str
    etag: str

    @property
    def status_code(self) -> int:
        return 206 if self.partial else 200

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def _file_facts(artifact: ArtifactRow) -> tuple[str, str]:
    """Type de contenu et nom de fichier proposé (ASCII : l'identifiant d'artefact est opaque)."""
    assert artifact.storage_key is not None
    name = artifact.storage_key.rsplit("/", 1)[-1]
    extension = "." + name.rsplit(".", 1)[1].lower() if "." in name else ""
    content_type = CONTENT_TYPES.get(extension, "application/octet-stream")
    return content_type, f"{FILE_LABELS[artifact.kind]}-{artifact.id}{extension}"


def prepare_download(
    db: Session,
    store: ObjectStore,
    settings: Settings,
    token: str,
    *,
    now: datetime,
    range_header: str | None = None,
    if_range: str | None = None,
) -> DownloadPlan:
    """Vérifie une URL de téléchargement et décide quoi servir. À appeler à **chaque** requête.

    1. jeton authentique et non périmé (sans base de données : une URL inventée ne coûte rien) ;
    2. l'artefact existe encore (sinon il a été supprimé : `410`) et il est prêt ;
    3. son fichier est sur le S3 (la taille vient du S3, pas de la base) ;
    4. la plage demandée (`Range`) est lue. `If-Range` doit reprendre l'ETag de l'artefact,
       sinon la plage est ignorée et le fichier entier est servi (RFC 9110 §13.1.5).
    """
    artifact_id = download_tokens.verify(settings, token, now=now)
    artifact = db.get(ArtifactRow, artifact_id)
    if artifact is None:
        msg = "cet artefact a été supprimé : l'URL n'est plus utilisable"
        raise ArtifactDeletedError(msg)
    if not is_downloadable(artifact):
        msg = "cet artefact n'est pas (ou plus) disponible au téléchargement"
        raise ArtifactNotReadyError(msg)
    assert artifact.storage_key is not None
    try:
        size = store.size(artifact.storage_key)
    except StorageError as error:
        logger.warning("stockage illisible pour un téléchargement : %s", error)
        msg = "le stockage ne répond pas, réessayer plus tard"
        raise StorageUnavailableError(msg) from None
    if size is None:
        # La base dit « prêt » mais le fichier n'y est plus : incident d'exploitation, à voir.
        logger.error("fichier d'artefact absent du stockage", extra={"artifact": artifact.id})
        msg = "le fichier de cet artefact est introuvable dans le stockage"
        raise ArtifactFileMissingError(msg)

    etag = f'"{artifact.id}"'  # le contenu d'un artefact ne change jamais : ETag fort
    wanted = range_header if if_range is None or if_range.strip() == etag else None
    byte_range = parse_range(wanted, size)
    content_type, filename = _file_facts(artifact)
    return DownloadPlan(
        artifact_id=artifact.id,
        key=artifact.storage_key,
        size=size,
        start=0 if byte_range is None else byte_range.start,
        end=size - 1 if byte_range is None else byte_range.end,
        partial=byte_range is not None,
        content_type=content_type,
        filename=filename,
        etag=etag,
    )


def open_download(store: ObjectStore, plan: DownloadPlan) -> StoredRange | None:
    """Ouvre le flux du fichier **avant** d'envoyer les en-têtes : si le S3 refuse, la réponse
    est une vraie erreur JSON et non un téléchargement qui se coupe en route. `None` : fichier
    vide, rien à lire."""
    if plan.length <= 0:
        return None
    try:
        return store.open_range(plan.key, plan.start, plan.end)
    except ObjectNotFoundError:
        # Supprimé entre la vérification et l'ouverture, ou perdu : rien à servir dans les deux cas.
        logger.warning("fichier d'artefact disparu pendant le téléchargement")
        msg = "le fichier de cet artefact est introuvable dans le stockage"
        raise ArtifactFileMissingError(msg) from None
    except StorageError as error:
        logger.warning("ouverture du fichier d'artefact impossible : %s", error)
        msg = "le stockage ne répond pas, réessayer plus tard"
        raise StorageUnavailableError(msg) from None


# --- suppression -----------------------------------------------------------------------------


def purge_prefix(key: str) -> str:
    """Préfixe qui regroupe tout ce qui appartient à l'artefact dans le bucket.

    Un chemin opaque `meetings/<id>/recordings/<uuid>.mp4` donne `meetings/<id>/recordings/<uuid>` :
    il couvre le fichier, le manifeste qu'Egress écrit à côté (`<uuid>.mp4.json`) et les segments
    d'un enregistrement fractionné (`<uuid>/seg_00001.ts`). Un UUID a toujours la même longueur : le
    préfixe d'un artefact ne peut donc être celui d'un autre.

    Garde-fou : si le nom du fichier n'est pas un UUID (clé inattendue), on ne supprime que la clé
    exacte, jamais un préfixe qui pourrait déborder sur d'autres artefacts.
    """
    directory, _, name = key.rpartition("/")
    stem = name.rsplit(".", 1)[0]
    if directory and _UUID.fullmatch(stem):
        return f"{directory}/{stem}"
    return key


def delete_artifact(db: Session, store: ObjectStore, artifact_id: str, kind: ArtifactKind) -> None:
    """Supprime définitivement un artefact : le S3 d'abord, la base ensuite (voir l'en-tête).

    - inexistant ou d'un autre type -> `ArtifactNotFoundError` ;
    - `pending` -> `ArtifactNotReadyError` : Egress ou la fusion écrit encore dans le stockage,
      supprimer maintenant laisserait un fichier orphelin. L'état se règle tout seul
      (`ready` ou `failed`) ; on peut alors supprimer ;
    - `ready` ou `failed` (un échec peut avoir laissé des segments) -> supprimé ;
    - S3 injoignable ou suppression non confirmée -> `StorageUnavailableError`, base intacte.
    """
    artifact = get_artifact(db, artifact_id, kind)
    if artifact.status is ArtifactStatus.PENDING:
        msg = "l'artefact est encore en cours de production : attendre son état final"
        raise ArtifactNotReadyError(msg)
    meeting_id, key = artifact.meeting_id, artifact.storage_key  # lus avant la suppression
    removed = 0
    if key:
        try:
            removed = store.purge(purge_prefix(key))
            still_there = store.size(key) is not None
        except StorageError as error:
            logger.warning("suppression d'artefact impossible : %s", error)
            msg = "le stockage ne répond pas : rien n'a été supprimé en base, réessayer plus tard"
            raise StorageUnavailableError(msg) from None
        if still_there:
            logger.error(
                "suppression non confirmée par le stockage", extra={"artifact": artifact.id}
            )
            msg = "le stockage n'a pas confirmé la suppression : l'artefact est conservé"
            raise StorageUnavailableError(msg)
    db.delete(artifact)
    db.commit()
    # Trace d'audit (RGPD) : quoi, de quelle réunion, combien d'objets. Jamais de clé ni d'URL.
    logger.info(
        "artefact supprimé",
        extra={
            "artifact": artifact_id,
            "meeting_id": meeting_id,
            "kind": kind.value,
            "objects": removed,
        },
    )
