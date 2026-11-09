"""Enregistrement des réunions (étape 14a) : démarrer à la première entrée, arrêter à la fin.

- **Démarrage** : `schedule_start` est appelé quand la réunion passe à `live` (`meeting.started`),
  dans la même transaction. Il crée l'artefact `recording` (statut `pending`, chemin S3 opaque
  `meetings/<id>/recordings/<uuid>.mp4`) et une tâche `start_recording`. La tâche est exécutée par
  le planificateur, avec relances : un Egress indisponible un instant ne perd pas l'enregistrement,
  et un redémarrage de l'API reprend les tâches en attente.
- **Une seule fois** : si un essai a réussi côté Egress mais que la réponse s'est perdue, l'essai
  suivant retrouve l'enregistrement actif de la salle au lieu d'en démarrer un second.
- **Arrêt** : `stop_for_meeting` est appelé par la tâche `close_room`, **avant** `DeleteRoom`
  (fermer la salle d'abord interromprait Egress brutalement). Si l'arrêt échoue, la salle est
  quand même fermée : la fin du créneau prime (DECISIONS.md, point 83).

- **Issue (14b)** : l'artefact reste `pending` après l'arrêt, jusqu'à ce qu'on constate la fin
  d'Egress. Deux chemins mènent au même `settle`, idempotent : l'événement LiveKit `egress_ended`
  (`handle_egress_ended`, rapide) et le filet du planificateur (`reconcile`, qui interroge
  `ListEgress` pour les artefacts `pending` d'une réunion terminée : webhook perdu, redémarrage).
  `complete` + objet présent sur le S3 -> `ready` (taille lue sur le S3) ; sinon `failed` avec un
  motif lisible. Chaque issue écrit `recording.ready` / `recording.failed` dans la boîte d'envoi,
  dans la même transaction que le changement d'état.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from visio.db.models import (
    ArtifactKind,
    ArtifactRow,
    ArtifactStatus,
    JobRow,
    JobStatus,
    MeetingRow,
)
from visio.domain.errors import DomainError
from visio.domain.meeting import MeetingState
from visio.services import segments, webhooks
from visio.services.egress import EgressInfo, RecorderGateway, info_from_json
from visio.services.jobs import JOB_MAX_ATTEMPTS, JOB_RETRY_DELAYS
from visio.services.storage import ObjectSizer, StorageError

logger = logging.getLogger("visio.recording")

START_RECORDING = "start_recording"
MERGE_RECORDING = "merge_recording"  # 14d : fusion des segments en un MP4

# Filet de sécurité : on n'interroge Egress qu'une fois la réunion finie depuis un moment (le
# webhook `egress_ended` arrive normalement en quelques secondes) ; sans nouvelle d'un
# enregistrement au bout de `LOST_AFTER`, il est déclaré perdu.
RECONCILE_GRACE = timedelta(seconds=20)
LOST_AFTER = timedelta(minutes=15)

# Motifs lisibles (envoyés dans `recording.failed`). « Start signal not received » : Egress n'a
# jamais reçu de piste à enregistrer (point 95 de DECISIONS.md).
REASON_NO_TRACK = "aucune piste audio ou vidéo n'a été publiée : rien à enregistrer"
REASON_NO_FILE = "Egress annonce la fin de l'enregistrement mais le fichier est absent du stockage"
REASON_LOST = "Egress n'a donné aucune nouvelle de l'enregistrement après la fin de la réunion"


def recording_key(meeting_id: str) -> str:
    """Chemin opaque dans le bucket. Construit ici, jamais à partir d'une saisie."""
    return f"meetings/{meeting_id}/recordings/{uuid.uuid4()}.mp4"


def schedule_start(db: Session, meeting: MeetingRow, *, now: datetime) -> ArtifactRow | None:
    """Prépare l'enregistrement d'une réunion qui vient de démarrer. Sans valider.

    Ne fait rien si l'option `recording` est désactivée (aucun artefact, aucune tâche), ni si
    l'enregistrement a déjà été préparé (rejeu).
    """
    if not meeting.recording:
        return None
    existing = db.scalar(
        select(ArtifactRow).where(
            ArtifactRow.meeting_id == meeting.id, ArtifactRow.kind == ArtifactKind.RECORDING
        )
    )
    if existing is not None:
        return None
    artifact = ArtifactRow(
        meeting_id=meeting.id,
        kind=ArtifactKind.RECORDING,
        status=ArtifactStatus.PENDING,
        storage_key=recording_key(meeting.id),
    )
    db.add(artifact)
    db.flush()  # l'identifiant de l'artefact est nécessaire à la tâche
    db.add(
        JobRow(
            kind=START_RECORDING,
            meeting_id=meeting.id,
            payload={"artifact_id": artifact.id},
            run_at=now,
        )
    )
    return artifact


def _fail(
    db: Session, meeting: MeetingRow | None, artifact: ArtifactRow, reason: str, *, now: datetime
) -> None:
    """Marque l'artefact `failed` et annonce `recording.failed`. Sans valider."""
    artifact.status = ArtifactStatus.FAILED
    artifact.error = reason
    if meeting is not None:
        webhooks.emit(
            db,
            meeting,
            webhooks.RECORDING_FAILED,
            {"recording_id": artifact.id, "reason": reason},
            now=now,
        )


def run_jobs(db: Session, recorder: RecorderGateway, *, now: datetime) -> int:
    """Exécute les tâches `start_recording` dues ; les échecs sont relancés puis abandonnés."""
    jobs = db.scalars(
        select(JobRow)
        .where(
            JobRow.kind == START_RECORDING,
            JobRow.status.in_([JobStatus.PENDING, JobStatus.FAILED]),
            JobRow.run_at <= now,
        )
        .order_by(JobRow.run_at)
    ).all()
    for job in jobs:
        job.attempts += 1
        # Valider AVANT l'appel à Egress : sinon le verrou d'écriture de SQLite reste tenu pendant
        # tout l'appel (jusqu'à 10 s si Egress est lent ou arrêté) et chaque écriture de l'API
        # échoue en « database is locked ». Compter l'essai d'avance est aussi plus juste.
        db.commit()
        artifact = db.get(ArtifactRow, str(job.payload.get("artifact_id", "")))
        try:
            _start(db, recorder, job, artifact, now=now)
        except DomainError as error:
            job.last_error = error.message
            if job.attempts >= JOB_MAX_ATTEMPTS:
                job.status = JobStatus.ABANDONED
                if artifact is not None:
                    _fail(
                        db,
                        db.get(MeetingRow, artifact.meeting_id),
                        artifact,
                        f"démarrage impossible : {error.message}",
                        now=now,
                    )
                logger.error(
                    "démarrage de l'enregistrement abandonné",
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


def _start(
    db: Session,
    recorder: RecorderGateway,
    job: JobRow,
    artifact: ArtifactRow | None,
    *,
    now: datetime,
) -> None:
    meeting = db.get(MeetingRow, job.meeting_id) if job.meeting_id else None
    if artifact is None or meeting is None:
        return  # rien à enregistrer (réunion supprimée)
    if artifact.egress_id is not None:
        return  # déjà démarré
    if meeting.state in (MeetingState.ENDED, MeetingState.CANCELLED) or not meeting.room_name:
        _fail(
            db,
            meeting,
            artifact,
            "la réunion était terminée avant le démarrage de l'enregistrement",
            now=now,
        )
        return
    if job.attempts > 1:
        # Un essai précédent a peut-être abouti sans que nous le sachions (réponse perdue).
        for info in recorder.list(room_name=meeting.room_name, active=True):
            artifact.egress_id = info.egress_id
            return
    assert artifact.storage_key is not None
    info = recorder.start_room_composite(meeting.room_name, artifact.storage_key)
    artifact.egress_id = info.egress_id


def stop_for_meeting(
    db: Session, recorder: RecorderGateway, meeting: MeetingRow, *, now: datetime
) -> int:
    """Arrête les enregistrements de la réunion. Sans valider ; n'échoue jamais.

    - tâche `start_recording` encore en attente : annulée, artefact marqué `failed` ;
    - enregistrement démarré : ordre d'arrêt (l'artefact reste `pending` jusqu'à `settle`).
    Renvoie le nombre d'ordres d'arrêt donnés avec succès.
    """
    artifacts = db.scalars(
        select(ArtifactRow).where(
            ArtifactRow.meeting_id == meeting.id,
            ArtifactRow.kind == ArtifactKind.RECORDING,
            ArtifactRow.status == ArtifactStatus.PENDING,
        )
    ).all()
    stopped = 0
    for artifact in artifacts:
        if artifact.egress_id is None:
            _cancel_start_jobs(db, meeting, artifact, now=now)
            continue
        try:
            recorder.stop(artifact.egress_id)
        except DomainError as error:
            if _already_finished(recorder, artifact.egress_id):
                # Normal : Egress s'arrête seul quand le dernier participant quitte la salle.
                logger.info(
                    "enregistrement déjà terminé, rien à arrêter", extra={"meeting_id": meeting.id}
                )
            else:
                logger.warning(
                    "arrêt de l'enregistrement impossible : %s",
                    error.message,
                    extra={"meeting_id": meeting.id},
                )
        else:
            stopped += 1
    return stopped


def _already_finished(recorder: RecorderGateway, egress_id: str) -> bool:
    try:
        return any(i.is_final for i in recorder.list(egress_id))
    except DomainError:
        return False


def _cancel_start_jobs(
    db: Session, meeting: MeetingRow, artifact: ArtifactRow, *, now: datetime
) -> None:
    jobs: list[JobRow] = list(
        db.scalars(
            select(JobRow).where(
                JobRow.kind == START_RECORDING,
                JobRow.status.in_([JobStatus.PENDING, JobStatus.FAILED]),
            )
        ).all()
    )
    payload_key: Any = artifact.id
    for job in jobs:
        if job.payload.get("artifact_id") == payload_key:
            job.status = JobStatus.DONE
            job.last_error = "annulée : la réunion s'est terminée avant le démarrage"
    _fail(
        db,
        meeting,
        artifact,
        "la réunion s'est terminée avant le démarrage de l'enregistrement",
        now=now,
    )


# --- issue de l'enregistrement (14b) -----------------------------------------------------------


def failure_reason(info: EgressInfo) -> str:
    """Traduit l'état final d'Egress en un motif lisible pour Eduvia."""
    error = info.error.strip()
    if "start signal not received" in error.lower():
        return REASON_NO_TRACK
    if info.status == "limit_reached":
        return "limite de durée ou de taille atteinte" + (f" ({error[:200]})" if error else "")
    if info.status == "aborted":
        return "enregistrement interrompu" + (f" : {error[:200]}" if error else "")
    return f"Egress a échoué : {error[:300]}" if error else "Egress a échoué sans donner de motif"


def settle(
    db: Session,
    meeting: MeetingRow,
    artifact: ArtifactRow,
    info: EgressInfo,
    storage: ObjectSizer,
    *,
    now: datetime,
) -> bool:
    """Passe l'artefact à `ready` ou `failed` d'après l'état final d'Egress. Sans valider.

    Idempotent : un artefact déjà tranché n'est pas touché (webhook rejoué, filet + webhook).
    Renvoie vrai si l'artefact a changé d'état. Si le S3 ne répond pas, l'artefact reste
    `pending` : le filet réessaiera au tour suivant.
    """
    if artifact.status is not ArtifactStatus.PENDING or not info.is_final:
        return False
    if info.status != "complete":
        # Segments déjà sur le S3 (Egress tombé en route) : on garde ce qui a été enregistré
        # plutôt que de tout perdre ; sans segment, c'est un vrai échec.
        if _queue_merge(db, meeting, artifact, storage, info, now=now):
            return False
        _fail(db, meeting, artifact, failure_reason(info), now=now)
        return True
    assert artifact.storage_key is not None
    try:
        size = storage.size(artifact.storage_key)
    except StorageError as error:
        logger.warning(
            "taille de l'enregistrement illisible : %s", error, extra={"meeting_id": meeting.id}
        )
        return False
    if size is None or size == 0:
        if size is None and _queue_merge(db, meeting, artifact, storage, info, now=now):
            return False  # enregistrement en segments : la fusion donnera le MP4 (run_merge_jobs)
        _fail(db, meeting, artifact, REASON_NO_FILE, now=now)
        return True
    artifact.status = ArtifactStatus.READY
    artifact.size_bytes = size
    artifact.error = None
    webhooks.emit(
        db,
        meeting,
        webhooks.RECORDING_READY,
        {
            "recording_id": artifact.id,
            "size_bytes": size,
            "duration_seconds": info.duration_seconds,
        },
        now=now,
    )
    return True


def _queue_merge(
    db: Session,
    meeting: MeetingRow,
    artifact: ArtifactRow,
    storage: ObjectSizer,
    info: EgressInfo,
    *,
    now: datetime,
) -> bool:
    """Vrai si l'enregistrement est en segments (tâche de fusion créée ou déjà en cours)."""
    if not hasattr(storage, "list_objects"):
        return False
    already = db.scalars(
        select(JobRow).where(JobRow.kind == MERGE_RECORDING, JobRow.meeting_id == meeting.id)
    ).all()
    if any(job.payload.get("artifact_id") == artifact.id for job in already):
        return True
    assert artifact.storage_key is not None
    try:
        present = segments.has_segments(storage, artifact.storage_key)  # type: ignore[arg-type]
    except StorageError as error:
        logger.warning("segments illisibles : %s", error, extra={"meeting_id": meeting.id})
        return True  # S3 en panne : on réessaie au tour suivant, sans déclarer d'échec
    if not present:
        return False
    db.add(
        JobRow(
            kind=MERGE_RECORDING,
            meeting_id=meeting.id,
            payload={"artifact_id": artifact.id, "duration_seconds": info.duration_seconds},
            run_at=now,
        )
    )
    return True


def run_merge_jobs(db: Session, merger: Callable[[str], int], *, now: datetime) -> int:
    """Fusionne les segments des enregistrements terminés (long : tourne dans sa propre boucle).
    `merger(clé)` renvoie la taille du MP4. Les échecs sont relancés puis abandonnés."""
    jobs = db.scalars(
        select(JobRow)
        .where(
            JobRow.kind == MERGE_RECORDING,
            JobRow.status.in_([JobStatus.PENDING, JobStatus.FAILED]),
            JobRow.run_at <= now,
        )
        .order_by(JobRow.run_at)
    ).all()
    for job in jobs:
        job.attempts += 1
        db.commit()  # ne jamais garder le verrou SQLite pendant la fusion
        artifact = db.get(ArtifactRow, str(job.payload.get("artifact_id", "")))
        if artifact is None or artifact.status is not ArtifactStatus.PENDING:
            job.status = JobStatus.DONE
            db.commit()
            continue
        meeting = db.get(MeetingRow, artifact.meeting_id)
        assert artifact.storage_key is not None
        try:
            size = merger(artifact.storage_key)
        except (segments.MergeError, StorageError) as error:
            job.last_error = str(error)
            if job.attempts >= JOB_MAX_ATTEMPTS:
                job.status = JobStatus.ABANDONED
                _fail(db, meeting, artifact, f"fusion des segments impossible : {error}", now=now)
                logger.error(
                    "fusion abandonnée", extra={"job": job.id, "meeting_id": job.meeting_id}
                )
            else:
                job.status = JobStatus.FAILED
                job.run_at = now + JOB_RETRY_DELAYS[job.attempts - 1]
        else:
            artifact.status = ArtifactStatus.READY
            artifact.size_bytes = size
            artifact.error = None
            job.status = JobStatus.DONE
            job.last_error = None
            if meeting is not None:
                webhooks.emit(
                    db,
                    meeting,
                    webhooks.RECORDING_READY,
                    {
                        "recording_id": artifact.id,
                        "size_bytes": size,
                        "duration_seconds": job.payload.get("duration_seconds"),
                    },
                    now=now,
                )
        db.commit()
    return len(jobs)


def handle_egress_ended(
    db: Session, event: dict[str, Any], storage: ObjectSizer, *, now: datetime
) -> str:
    """Webhook LiveKit `egress_ended` (déjà authentifié). Renvoie ce qui a été fait."""
    raw = event.get("egressInfo") or event.get("egress_info")
    if not isinstance(raw, dict):
        return "ignored"
    info = info_from_json(raw)
    if not info.egress_id:
        return "ignored"
    artifact = db.scalar(select(ArtifactRow).where(ArtifactRow.egress_id == info.egress_id))
    if artifact is None:
        return "unknown-egress"  # pas le nôtre, ou pas encore enregistré : le filet rattrapera
    meeting = db.get(MeetingRow, artifact.meeting_id)
    if meeting is None:
        return "unknown-meeting"
    changed = settle(db, meeting, artifact, info, storage, now=now)
    db.commit()
    return artifact.status.value if changed else "unchanged"


def reconcile(
    db: Session, recorder: RecorderGateway, storage: ObjectSizer, *, now: datetime
) -> int:
    """Filet de sécurité : tranche les enregistrements `pending` d'une réunion terminée dont
    l'événement `egress_ended` n'est jamais arrivé (webhook perdu, API redémarrée, S3 en panne
    au moment voulu). Un Egress injoignable n'est pas une issue : on réessaie au tour suivant.
    Renvoie le nombre d'artefacts tranchés."""
    rows = db.execute(
        select(ArtifactRow, MeetingRow)
        .join(MeetingRow, MeetingRow.id == ArtifactRow.meeting_id)
        .where(
            ArtifactRow.kind == ArtifactKind.RECORDING,
            ArtifactRow.status == ArtifactStatus.PENDING,
            ArtifactRow.egress_id.is_not(None),
            MeetingRow.state == MeetingState.ENDED,
            MeetingRow.ended_at <= now - RECONCILE_GRACE,
        )
    ).all()
    settled = 0
    for artifact, meeting in rows:
        assert artifact.egress_id is not None
        try:
            infos = recorder.list(artifact.egress_id)
        except DomainError as error:
            logger.warning(
                "Egress injoignable (filet) : %s", error.message, extra={"meeting_id": meeting.id}
            )
            continue
        found = next((i for i in infos if i.egress_id == artifact.egress_id), None)
        if found is None:
            assert meeting.ended_at is not None
            if now - meeting.ended_at >= LOST_AFTER:
                _fail(db, meeting, artifact, REASON_LOST, now=now)
                settled += 1
        elif settle(db, meeting, artifact, found, storage, now=now):
            settled += 1
        db.commit()
    return settled
