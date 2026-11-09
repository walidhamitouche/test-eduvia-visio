"""Worker de transcription (étape 16c) : pistes audio -> segments par personne -> fusion -> 16a.

Cycle :
1. `enqueue_due` (planificateur de l'API) crée **une** tâche `transcribe` par réunion terminée
   avec `transcription: true`. Idempotent, et rattrape tout après un redémarrage.
2. `run_jobs` (processus worker, `uv run task transcriber`) exécute les tâches dues :
   attend que les pistes soient tranchées, transcrit chaque piste `ready` avec le moteur,
   attribue la parole au **nom et à l'`external_id` du lien** (jamais à la voix), replace les
   segments sur la chronologie de la réunion, fusionne, écrit via `services.transcripts`,
   annonce `transcript.ready`, puis supprime les fichiers audio de travail.
3. Échec : relances (`JOB_RETRY_DELAYS`), puis `transcript.failed` avec le motif.

Reprise après coupure : la tâche reste due tant qu'elle n'est pas terminée ; `transcripts.begin`
/ `complete` réutilisent la même ligne et les mêmes noms de fichiers : aucun doublon. Le drapeau
`written` évite de refaire (et de ré-annoncer) une transcription déjà écrite si seul le
nettoyage audio avait échoué.

Aucun appel réseau sortant : le moteur est local (`whisper_engine`), le S3 est interne.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from visio.db.models import ArtifactRow, JobRow, JobStatus, MeetingRow
from visio.domain import transcript as fmt
from visio.domain.errors import DomainError
from visio.domain.meeting import MeetingState
from visio.domain.slot import to_paris
from visio.services import tracks, transcripts, webhooks
from visio.services.jobs import JOB_MAX_ATTEMPTS, JOB_RETRY_DELAYS
from visio.services.storage import ObjectStore, StorageError

logger = logging.getLogger("visio.transcription")

TRANSCRIBE = "transcribe"
LANGUAGE = "fr"
WAIT_FOR_TRACKS = timedelta(seconds=10)
MAX_WAIT_FOR_TRACKS = timedelta(hours=2)  # au-delà, on transcrit les pistes déjà prêtes


class TranscriptionEngineError(Exception):
    """Le moteur n'a pas pu transcrire (modèle absent, fichier illisible…)."""


@dataclass(frozen=True, slots=True)
class RawSegment:
    """Ce que dit le moteur : temps en secondes **dans le fichier audio**, sans locuteur."""

    start: float
    end: float
    text: str


class Transcriber(Protocol):
    def transcribe(self, audio: Path, *, language: str) -> list[RawSegment]: ...


@dataclass
class FakeTranscriber:
    """Faux moteur pour les tests : le contenu du fichier audio désigne les segments à rendre."""

    by_content: dict[bytes, list[RawSegment]] = field(default_factory=dict)
    fail: bool = False
    calls: int = 0

    def transcribe(self, audio: Path, *, language: str) -> list[RawSegment]:
        self.calls += 1
        if self.fail:
            msg = "moteur de test en panne"
            raise TranscriptionEngineError(msg)
        return list(self.by_content.get(audio.read_bytes(), []))


# --- 1. création des tâches ----------------------------------------------------------------------


def enqueue_due(db: Session, *, now: datetime) -> int:
    """Une tâche `transcribe` pour chaque réunion terminée qui en a besoin et n'en a pas encore."""
    meetings = db.scalars(
        select(MeetingRow).where(
            MeetingRow.state == MeetingState.ENDED,
            MeetingRow.transcription.is_(True),
            ~exists().where(JobRow.kind == TRANSCRIBE, JobRow.meeting_id == MeetingRow.id),
        )
    ).all()
    for meeting in meetings:
        db.add(JobRow(kind=TRANSCRIBE, meeting_id=meeting.id, payload={}, run_at=now))
    if meetings:
        db.commit()
    return len(meetings)


# --- 2. exécution --------------------------------------------------------------------------------


def run_jobs(
    db: Session,
    store: ObjectStore,
    engine: Transcriber,
    *,
    now: datetime,
    keep_audio: bool = False,
) -> int:
    """Exécute les tâches `transcribe` dues. Renvoie le nombre de tâches examinées.

    `keep_audio` (diagnostic) : ne supprime pas les pistes audio après la transcription."""
    jobs = db.scalars(
        select(JobRow)
        .where(
            JobRow.kind == TRANSCRIBE,
            JobRow.status.in_([JobStatus.PENDING, JobStatus.FAILED]),
            JobRow.run_at <= now,
        )
        .order_by(JobRow.run_at)
    ).all()
    for job in jobs:
        _run(db, store, engine, job, now=now, keep_audio=keep_audio)
    return len(jobs)


def _run(
    db: Session,
    store: ObjectStore,
    engine: Transcriber,
    job: JobRow,
    *,
    now: datetime,
    keep_audio: bool = False,
) -> None:
    meeting = db.get(MeetingRow, job.meeting_id) if job.meeting_id else None
    if meeting is None:
        job.status = JobStatus.DONE
        db.commit()
        return
    written = bool(job.payload.get("written"))
    if not written and _must_wait(db, meeting, now):
        job.run_at = now + WAIT_FOR_TRACKS  # une attente n'est pas un essai
        db.commit()
        return
    job.attempts += 1
    db.commit()  # jamais le verrou d'écriture de SQLite pendant le calcul (plusieurs minutes)
    row: ArtifactRow | None = None
    try:
        if not written:
            row = transcripts.begin(db, meeting.id)
            _produce(db, store, engine, meeting, row, now=now)
            job.payload = {**job.payload, "written": True}
            db.commit()
        if not keep_audio:
            tracks.purge(db, store, meeting.id)
    except (TranscriptionEngineError, DomainError, StorageError) as error:
        db.rollback()
        _on_error(db, job, meeting, row, error, now=now, written=written or _is_written(job))
    else:
        job.status = JobStatus.DONE
        job.last_error = None
        db.commit()


def _is_written(job: JobRow) -> bool:
    return bool(job.payload.get("written"))


def _must_wait(db: Session, meeting: MeetingRow, now: datetime) -> bool:
    if not tracks.pending_tracks(db, meeting.id):
        return False
    ended = meeting.ended_at
    return ended is None or now - ended < MAX_WAIT_FOR_TRACKS


def _produce(
    db: Session,
    store: ObjectStore,
    engine: Transcriber,
    meeting: MeetingRow,
    row: ArtifactRow,
    *,
    now: datetime,
) -> None:
    """Transcrit chaque piste prête, fusionne, écrit (16a) et annonce `transcript.ready`."""
    ready = tracks.ready_tracks(db, meeting.id)
    origin = meeting.started_at or (ready[0].started_at if ready else None)
    streams: list[list[fmt.Segment]] = []
    with tempfile.TemporaryDirectory(prefix="visio-transcribe-") as folder:
        for track in ready:
            assert origin is not None
            audio = Path(folder) / f"{track.id}.ogg"
            audio.write_bytes(store.get(track.storage_key))
            raw = engine.transcribe(audio, language=LANGUAGE)
            audio.unlink()
            shift = tracks.offset_seconds(track, origin)
            streams.append(
                [
                    fmt.Segment(
                        start=piece.start + shift,
                        end=piece.end + shift,
                        speaker=track.display_name,  # le nom du lien, jamais la voix
                        external_id=track.external_id,
                        text=piece.text,
                    )
                    for piece in raw
                ]
            )
    merged = fmt.merge(streams)
    duration = None
    if origin is not None and meeting.ended_at is not None:
        duration = max((meeting.ended_at - origin).total_seconds(), 0.0)
    transcripts.complete(
        db,
        store,
        row,
        merged,
        language=LANGUAGE,
        started_at=to_paris(origin).isoformat() if origin is not None else None,
        duration_seconds=duration,
    )
    webhooks.emit(
        db,
        meeting,
        webhooks.TRANSCRIPT_READY,
        {
            "transcript_id": row.id,
            "language": LANGUAGE,
            "segments": len(merged),
            "speakers": len(fmt.speakers(merged)),
            "duration_seconds": duration,
        },
        now=now,
    )
    db.commit()


def _on_error(
    db: Session,
    job: JobRow,
    meeting: MeetingRow,
    row: ArtifactRow | None,
    error: Exception,
    *,
    now: datetime,
    written: bool,
) -> None:
    message = getattr(error, "message", None) or str(error)
    job.last_error = message
    if job.attempts >= JOB_MAX_ATTEMPTS:
        job.status = JobStatus.ABANDONED
        logger.error("transcription abandonnée", extra={"job": job.id, "meeting_id": meeting.id})
        if (
            not written and row is not None
        ):  # transcription déjà écrite : on ne la déclare pas en échec
            reason = f"transcription impossible : {message}"
            transcripts.fail(db, row, reason)
            webhooks.emit(
                db,
                meeting,
                webhooks.TRANSCRIPT_FAILED,
                {"transcript_id": row.id, "reason": reason},
                now=now,
            )
    else:
        job.status = JobStatus.FAILED
        job.run_at = now + JOB_RETRY_DELAYS[job.attempts - 1]
    db.commit()
