"""Pistes audio par participant, pour la transcription (étape 16b).

**Pourquoi une piste par personne.** Un enregistrement « comme à l'écran » mélange toutes les
voix : impossible d'en tirer qui parle sans deviner à l'oreille. Ici chaque micro est enregistré
**à part** ; le locuteur d'un fichier est donc connu avec certitude, et c'est le **nom et
l'`external_id` du lien** (jamais une reconnaissance de voix) qui le désigne.

Cycle, calqué sur l'enregistrement (`services.recording`) :
1. LiveKit prévient (`track_published`) qu'une personne publie son micro -> `handle_published`
   crée une ligne `pending` et une tâche `start_track` (seulement si `transcription: true`).
2. Le planificateur exécute la tâche (`run_jobs`, avec relances) : ordre « track » à Egress.
3. La piste se termine seule quand la personne la coupe ou quitte la salle ; `egress_ended`
   (`handle_egress_ended`) ou le filet du planificateur (`reconcile`) la passe à `ready`/`failed`.
4. Avant de fermer la salle, `stop_for_meeting` arrête les pistes encore ouvertes.
5. Le worker (16c) lit les pistes `ready`, puis `purge` supprime les fichiers de travail.

Une piste en échec n'est jamais fatale : la transcription se fait avec les autres.
Le partage d'écran (`SCREEN_SHARE_AUDIO`) n'est pas transcrit : seuls les micros le sont.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from visio.db.models import (
    ArtifactStatus,
    AudioTrackRow,
    JobRow,
    JobStatus,
    MeetingRow,
)
from visio.domain.errors import DomainError
from visio.domain.meeting import MeetingState
from visio.services import access
from visio.services.egress import EgressInfo, RecorderGateway, info_from_json
from visio.services.jobs import JOB_MAX_ATTEMPTS, JOB_RETRY_DELAYS
from visio.services.recording import (
    LOST_AFTER,
    REASON_LOST,
    REASON_NO_FILE,
    RECONCILE_GRACE,
    failure_reason,
)
from visio.services.storage import ObjectSizer, ObjectStore, StorageError

logger = logging.getLogger("visio.tracks")

START_TRACK = "start_track"

# `TrackType` / `TrackSource` du protocole LiveKit : le JSON des webhooks donne le nom ou le numéro.
_AUDIO = ("AUDIO", 0)
_MICROPHONE = ("MICROPHONE", 2)


def audio_key(meeting_id: str) -> str:
    """Chemin opaque d'un fichier de travail. Construit ici, jamais à partir d'une saisie."""
    return f"meetings/{meeting_id}/audio/{uuid.uuid4()}.ogg"


def audio_prefix(meeting_id: str) -> str:
    return f"meetings/{meeting_id}/audio/"


def _is(value: Any, accepted: tuple[str | int, ...]) -> bool:
    return (value.upper() if isinstance(value, str) else value) in accepted


# --- 1. publication d'une piste ------------------------------------------------------------------


def handle_published(db: Session, event: dict[str, Any], *, now: datetime) -> str:
    """Webhook LiveKit `track_published` (déjà authentifié). Renvoie ce qui a été fait."""
    # Import local : `presence` dépend de `lifecycle`, qui arrête les pistes (`stop_for_meeting`).
    from visio.services.presence import HUMAN_IDENTITY, meeting_for_room

    room = str((event.get("room") or {}).get("name", ""))
    person: dict[str, Any] = event.get("participant") or {}
    track: dict[str, Any] = event.get("track") or {}
    identity = str(person.get("identity", ""))
    sid = str(track.get("sid", ""))
    if not sid or not HUMAN_IDENTITY.match(identity):
        return "ignored"
    # Le JSON des webhooks omet les valeurs par défaut des énumérations : `AUDIO` vaut 0, donc une
    # piste audio arrive **sans** champ `type`. Absent = audio ; seule la source distingue le micro.
    kind = track.get("type", 0)
    if not _is(kind, _AUDIO) or not _is(track.get("source"), _MICROPHONE):
        logger.info(
            "piste ignorée (pas un micro)",
            extra={"type": str(kind), "source": str(track.get("source"))},
        )
        return "not-a-microphone"
    meeting = meeting_for_room(db, room)
    if meeting is None:
        return "unknown-room"
    if not meeting.transcription:
        return "transcription-off"
    if meeting.state in (MeetingState.ENDED, MeetingState.CANCELLED):
        return "meeting-over"
    entry = access.get_entry(db, meeting.id, identity)
    if entry is None or entry.status.value != "admitted":
        return "not-admitted"  # pas (ou plus) admise : on n'enregistre pas sa voix
    if db.scalar(select(AudioTrackRow.id).where(AudioTrackRow.track_sid == sid)):
        return "duplicate"  # webhook rejoué

    seconds = event.get("createdAt")
    published = datetime.fromtimestamp(float(seconds), UTC) if seconds else now
    row = AudioTrackRow(
        meeting_id=meeting.id,
        track_sid=sid,
        participant_identity=identity,
        display_name=entry.display_name,
        external_id=entry.external_id,
        storage_key=audio_key(meeting.id),
        started_at=published,
    )
    db.add(row)
    db.flush()
    db.add(
        JobRow(kind=START_TRACK, meeting_id=meeting.id, payload={"track_id": row.id}, run_at=now)
    )
    db.commit()
    return "queued"


# --- 2. démarrage ------------------------------------------------------------------------------


def run_jobs(db: Session, recorder: RecorderGateway, *, now: datetime) -> int:
    """Exécute les tâches `start_track` dues ; les échecs sont relancés puis abandonnés."""
    jobs = db.scalars(
        select(JobRow)
        .where(
            JobRow.kind == START_TRACK,
            JobRow.status.in_([JobStatus.PENDING, JobStatus.FAILED]),
            JobRow.run_at <= now,
        )
        .order_by(JobRow.run_at)
    ).all()
    for job in jobs:
        job.attempts += 1
        db.commit()  # jamais le verrou d'écriture de SQLite pendant un appel à Egress
        row = db.get(AudioTrackRow, str(job.payload.get("track_id", "")))
        try:
            _start(db, recorder, row)
        except DomainError as error:
            job.last_error = error.message
            if job.attempts >= JOB_MAX_ATTEMPTS:
                job.status = JobStatus.ABANDONED
                if row is not None:
                    _fail(row, f"démarrage impossible : {error.message}")
                logger.error(
                    "démarrage d'une piste audio abandonné",
                    extra={"job": job.id, "meeting_id": job.meeting_id},
                )
            else:
                job.status = JobStatus.FAILED
                job.run_at = now + JOB_RETRY_DELAYS[job.attempts - 1]
        else:
            job.status = JobStatus.DONE
            job.last_error = None
        db.commit()
    return len(jobs)


def _start(db: Session, recorder: RecorderGateway, row: AudioTrackRow | None) -> None:
    if row is None or row.egress_id is not None or row.status is not ArtifactStatus.PENDING:
        return  # supprimée, déjà démarrée, ou déjà tranchée
    meeting = db.get(MeetingRow, row.meeting_id)
    if meeting is None:
        return
    if meeting.state in (MeetingState.ENDED, MeetingState.CANCELLED) or not meeting.room_name:
        _fail(row, "la réunion était terminée avant le démarrage de la piste")
        return
    row.egress_id = recorder.start_track_audio(
        meeting.room_name, row.track_sid, row.storage_key
    ).egress_id


def _fail(row: AudioTrackRow, reason: str) -> None:
    row.status = ArtifactStatus.FAILED
    row.error = reason


# --- 3. issue ------------------------------------------------------------------------------------


def settle(row: AudioTrackRow, info: EgressInfo, storage: ObjectSizer) -> bool:
    """Passe la piste à `ready` ou `failed` d'après l'état final d'Egress. Sans valider.

    Idempotent. Si le S3 ne répond pas, la piste reste `pending` (le filet réessaiera)."""
    if row.status is not ArtifactStatus.PENDING or not info.is_final:
        return False
    if info.status != "complete":
        _fail(row, failure_reason(info))
        return True
    try:
        size = storage.size(row.storage_key)
        if size is None and info.files and info.files[0] != row.storage_key:
            # Egress a pu nommer le fichier autrement (extension, modèle de nom) : on le suit.
            size = storage.size(info.files[0])
            if size is not None:
                row.storage_key = info.files[0]
    except StorageError as error:
        logger.warning("taille d'une piste audio illisible : %s", error)
        return False
    if not size:
        _fail(row, REASON_NO_FILE)
        return True
    row.status = ArtifactStatus.READY
    row.size_bytes = size
    row.error = None
    if info.started_at is not None:
        row.started_at = info.started_at  # début réel du fichier, plus juste que la publication
    return True


def handle_egress_ended(
    db: Session, event: dict[str, Any], storage: ObjectSizer, *, now: datetime
) -> str:
    """Webhook `egress_ended` d'une piste (l'appelant a déjà vu que ce n'est pas l'enregistrement
    de la réunion). Renvoie ce qui a été fait."""
    raw = event.get("egressInfo") or event.get("egress_info")
    if not isinstance(raw, dict):
        return "ignored"
    info = info_from_json(raw)
    if not info.egress_id:
        return "ignored"
    row = db.scalar(select(AudioTrackRow).where(AudioTrackRow.egress_id == info.egress_id))
    if row is None:
        return "unknown-egress"
    changed = settle(row, info, storage)
    db.commit()
    return row.status.value if changed else "unchanged"


def reconcile(
    db: Session, recorder: RecorderGateway, storage: ObjectSizer, *, now: datetime
) -> int:
    """Filet : tranche les pistes `pending` d'une réunion terminée dont `egress_ended` n'est
    jamais arrivé. Un Egress injoignable n'est pas une issue : on réessaie au tour suivant."""
    rows = db.execute(
        select(AudioTrackRow, MeetingRow)
        .join(MeetingRow, MeetingRow.id == AudioTrackRow.meeting_id)
        .where(
            AudioTrackRow.status == ArtifactStatus.PENDING,
            AudioTrackRow.egress_id.is_not(None),
            MeetingRow.state == MeetingState.ENDED,
            MeetingRow.ended_at <= now - RECONCILE_GRACE,
        )
    ).all()
    settled = 0
    for row, meeting in rows:
        assert row.egress_id is not None
        try:
            infos = recorder.list(row.egress_id)
        except DomainError as error:
            logger.warning("Egress injoignable (filet des pistes) : %s", error.message)
            continue
        found = next((i for i in infos if i.egress_id == row.egress_id), None)
        if found is None:
            assert meeting.ended_at is not None
            if now - meeting.ended_at >= LOST_AFTER:
                _fail(row, REASON_LOST)
                settled += 1
        elif settle(row, found, storage):
            settled += 1
        db.commit()
    return settled


# --- 4. arrêt avant la fermeture de la salle -----------------------------------------------------


def stop_for_meeting(db: Session, recorder: RecorderGateway, meeting: MeetingRow) -> int:
    """Arrête les pistes encore ouvertes (avant `DeleteRoom`, pour qu'Egress termine proprement
    ses fichiers). Sans valider ; n'échoue jamais. Une piste dont le démarrage n'a pas eu lieu est
    annulée. Renvoie le nombre d'ordres d'arrêt donnés."""
    rows = db.scalars(
        select(AudioTrackRow).where(
            AudioTrackRow.meeting_id == meeting.id,
            AudioTrackRow.status == ArtifactStatus.PENDING,
        )
    ).all()
    stopped = 0
    for row in rows:
        if row.egress_id is None:
            _cancel(db, row)
            continue
        try:
            recorder.stop(row.egress_id)
        except DomainError:
            logger.info("piste déjà terminée ou arrêt impossible", extra={"meeting_id": meeting.id})
        else:
            stopped += 1
    return stopped


def _cancel(db: Session, row: AudioTrackRow) -> None:
    for job in db.scalars(
        select(JobRow).where(
            JobRow.kind == START_TRACK,
            JobRow.status.in_([JobStatus.PENDING, JobStatus.FAILED]),
        )
    ):
        if job.payload.get("track_id") == row.id:
            job.status = JobStatus.DONE
            job.last_error = "annulée : la réunion s'est terminée avant le démarrage"
    _fail(row, "la réunion s'est terminée avant le démarrage de la piste")


# --- 5. lecture et nettoyage (worker de l'étape 16c) ---------------------------------------------


def ready_tracks(db: Session, meeting_id: str) -> list[AudioTrackRow]:
    """Les pistes utilisables, dans l'ordre où elles ont commencé."""
    return list(
        db.scalars(
            select(AudioTrackRow)
            .where(
                AudioTrackRow.meeting_id == meeting_id,
                AudioTrackRow.status == ArtifactStatus.READY,
            )
            .order_by(AudioTrackRow.started_at, AudioTrackRow.id)
        ).all()
    )


def offset_seconds(row: AudioTrackRow, origin: datetime) -> float:
    """Décalage du début de la piste sur la chronologie (`origin`), jamais négatif."""
    return max((row.started_at - origin).total_seconds(), 0.0)


def pending_tracks(db: Session, meeting_id: str) -> int:
    """Nombre de pistes pas encore tranchées : le worker attend qu'il n'y en ait plus."""
    return len(
        db.scalars(
            select(AudioTrackRow.id).where(
                AudioTrackRow.meeting_id == meeting_id,
                AudioTrackRow.status == ArtifactStatus.PENDING,
            )
        ).all()
    )


def purge(db: Session, store: ObjectStore, meeting_id: str) -> int:
    """Supprime les fichiers de travail de la réunion (S3 d'abord, base ensuite), une fois la
    transcription écrite. `StorageError` si le S3 ne répond pas : rien n'est supprimé en base,
    l'appel est rejouable."""
    removed = store.purge(audio_prefix(meeting_id))
    for row in db.scalars(select(AudioTrackRow).where(AudioTrackRow.meeting_id == meeting_id)):
        db.delete(row)
    db.commit()
    logger.info("pistes audio supprimées", extra={"meeting_id": meeting_id, "objects": removed})
    return removed
