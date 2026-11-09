"""Santé du service (étape 17b) : ce que l'exploitation veut savoir en un appel.

Deux niveaux, deux routes (`api/health.py`) :

- `GET /health` : public, **vivant ou non**. Ne touche aucune dépendance, pour qu'une sonde
  Docker / Coolify ne redémarre pas l'API parce que le S3 est en panne (redémarrer ne le
  réparerait pas).
- `GET /v1/health` : derrière la clé d'API. Sonde la base, LiveKit et le S3, et lit l'état de la
  file de tâches. Le détail ne contient **que** des états, des compteurs et des durées : jamais
  une adresse, un nom de bucket, une clé ni le texte d'une exception (qui est seulement consigné).

Les sondes tournent en parallèle, dans des fils, chacune bornée par `CHECK_TIMEOUT_SECONDS` : un
S3 qui ne répond pas ne doit pas bloquer la sonde de santé (le client `boto3` attend jusqu'à
plusieurs dizaines de secondes sans cela).

Statut global : `down` si la base est injoignable (l'API ne peut rien faire), `degraded` si une
autre dépendance est en panne ou si la file de tâches est en souffrance, sinon `ok`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from visio.db.models import (
    ArtifactRow,
    ArtifactStatus,
    JobRow,
    JobStatus,
    WebhookDeliveryRow,
    WebhookStatus,
)
from visio.domain.errors import DomainError
from visio.services.recording import MERGE_RECORDING
from visio.services.rooms import RoomGateway
from visio.services.storage import ObjectStore, StorageError
from visio.services.transcription import TRANSCRIBE

logger = logging.getLogger("visio.health")

CHECK_TIMEOUT_SECONDS = 3.0

# Une tâche « due » (son heure est passée) qui n'a pas été prise en charge depuis ce délai : le
# planificateur ne tourne plus ou est bloqué. Il tourne toutes les secondes.
LATE_AFTER = timedelta(minutes=2)
# Tâches longues par nature : leur attente normale dépasse LATE_AFTER. La fusion d'un
# enregistrement d'une journée prend des minutes ; la transcription tourne dans un processus à
# part et un délai de « quelques heures » est accepté par le besoin (§5).
LATE_AFTER_BY_KIND: dict[str, timedelta] = {
    MERGE_RECORDING: timedelta(minutes=30),
    TRANSCRIBE: timedelta(hours=3),
}

Status = Literal["ok", "degraded", "down"]

DETAIL_UNREACHABLE = "injoignable"
DETAIL_TIMEOUT = "délai dépassé"


@dataclass(frozen=True, slots=True)
class DependencyCheck:
    status: Literal["ok", "down"]
    latency_ms: float
    detail: str | None = None  # texte fixe (voir DETAIL_*), jamais le message d'une exception


@dataclass(frozen=True, slots=True)
class JobsQueue:
    pending: int  # en attente (y compris celles dont l'heure n'est pas venue)
    failed: int  # un essai a échoué, le prochain est programmé
    abandoned: int  # tous les essais ont échoué : plus personne ne s'en occupe
    oldest_due_age_seconds: int | None  # la plus ancienne tâche due non prise en charge
    late: bool


@dataclass(frozen=True, slots=True)
class WebhooksQueue:
    pending: int
    abandoned: int
    oldest_due_age_seconds: int | None
    late: bool


@dataclass(frozen=True, slots=True)
class QueueReport:
    status: Literal["ok", "degraded"]
    jobs: JobsQueue
    webhooks: WebhooksQueue
    artifacts_failed: int  # information : un enregistrement vide est un échec « normal »


@dataclass(frozen=True, slots=True)
class HealthReport:
    status: Status
    checked_at: datetime
    database: DependencyCheck
    livekit: DependencyCheck
    storage: DependencyCheck
    queue: QueueReport | None  # None : illisible (base en panne)

    @property
    def healthy(self) -> bool:
        return self.status == "ok"


# --- 1. file de tâches -------------------------------------------------------------------------


def _age_seconds(now: datetime, since: datetime) -> int:
    return max(0, int((now - since).total_seconds()))


def jobs_queue(db: Session, now: datetime) -> JobsQueue:
    counts = {
        status: count
        for status, count in db.execute(
            select(JobRow.status, func.count())
            .where(JobRow.status.in_([JobStatus.PENDING, JobStatus.FAILED, JobStatus.ABANDONED]))
            .group_by(JobRow.status)
        ).all()
    }
    # Les tâches « dues » : même critère que les exécuteurs (en attente ou à reprendre,
    # heure passée).
    due = db.execute(
        select(JobRow.kind, func.min(JobRow.run_at))
        .where(JobRow.status.in_([JobStatus.PENDING, JobStatus.FAILED]), JobRow.run_at <= now)
        .group_by(JobRow.kind)
    ).all()
    ages = [(kind, _age_seconds(now, since)) for kind, since in due if since is not None]
    late = any(age > LATE_AFTER_BY_KIND.get(kind, LATE_AFTER).total_seconds() for kind, age in ages)
    return JobsQueue(
        pending=counts.get(JobStatus.PENDING, 0),
        failed=counts.get(JobStatus.FAILED, 0),
        abandoned=counts.get(JobStatus.ABANDONED, 0),
        oldest_due_age_seconds=max((age for _kind, age in ages), default=None),
        late=late,
    )


def webhooks_queue(db: Session, now: datetime) -> WebhooksQueue:
    counts = {
        status: count
        for status, count in db.execute(
            select(WebhookDeliveryRow.status, func.count())
            .where(WebhookDeliveryRow.status.in_([WebhookStatus.PENDING, WebhookStatus.ABANDONED]))
            .group_by(WebhookDeliveryRow.status)
        ).all()
    }
    oldest_due = db.scalar(
        select(func.min(WebhookDeliveryRow.next_attempt_at)).where(
            WebhookDeliveryRow.status == WebhookStatus.PENDING,
            WebhookDeliveryRow.next_attempt_at <= now,
        )
    )
    age = None if oldest_due is None else _age_seconds(now, oldest_due)
    return WebhooksQueue(
        pending=counts.get(WebhookStatus.PENDING, 0),
        abandoned=counts.get(WebhookStatus.ABANDONED, 0),
        oldest_due_age_seconds=age,
        late=age is not None and age > LATE_AFTER.total_seconds(),
    )


def queue_report(db: Session, now: datetime) -> QueueReport:
    jobs = jobs_queue(db, now)
    webhooks = webhooks_queue(db, now)
    artifacts_failed = (
        db.scalar(
            select(func.count())
            .select_from(ArtifactRow)
            .where(ArtifactRow.status == ArtifactStatus.FAILED)
        )
        or 0
    )
    suffering = jobs.abandoned > 0 or webhooks.abandoned > 0 or jobs.late or webhooks.late
    return QueueReport(
        status="degraded" if suffering else "ok",
        jobs=jobs,
        webhooks=webhooks,
        artifacts_failed=artifacts_failed,
    )


# --- 2. sondes ---------------------------------------------------------------------------------


async def check_dependency(name: str, probe: Callable[[], object]) -> DependencyCheck:
    """Exécute `probe` dans un fil, au plus `CHECK_TIMEOUT_SECONDS`. Ne lève jamais."""
    started = time.perf_counter()
    try:
        await asyncio.wait_for(asyncio.to_thread(probe), timeout=CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        _log_failure(name, "timeout", None)
        return DependencyCheck("down", _elapsed_ms(started), DETAIL_TIMEOUT)
    except Exception as error:
        # Le message n'est consigné que pour nos propres erreurs, déjà écrites sans secret ;
        # celui d'une bibliothèque (SQLAlchemy, boto3) peut contenir une adresse.
        reason = str(error) if isinstance(error, DomainError | StorageError) else None
        _log_failure(name, type(error).__name__, reason)
        return DependencyCheck("down", _elapsed_ms(started), DETAIL_UNREACHABLE)
    return DependencyCheck("ok", _elapsed_ms(started))


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _log_failure(name: str, error_type: str, reason: str | None) -> None:
    fields: dict[str, str] = {"dependency": name, "error_type": error_type}
    if reason:
        fields["reason"] = reason
    logger.warning("sonde de santé en échec", extra=fields)


async def _load_queue(session_factory: sessionmaker[Session], now: datetime) -> QueueReport | None:
    def read() -> QueueReport:
        with session_factory() as db:
            return queue_report(db, now)

    try:
        return await asyncio.wait_for(asyncio.to_thread(read), timeout=CHECK_TIMEOUT_SECONDS)
    except Exception as error:  # TimeoutError compris : la file est alors « illisible »
        logger.warning("état de la file illisible", extra={"error_type": type(error).__name__})
        return None


def overall_status(
    database: DependencyCheck,
    livekit: DependencyCheck,
    storage: DependencyCheck,
    queue: QueueReport | None,
) -> Status:
    if database.status == "down":
        return "down"
    if livekit.status == "down" or storage.status == "down":
        return "degraded"
    if queue is None or queue.status == "degraded":
        return "degraded"
    return "ok"


async def check_health(
    session_factory: sessionmaker[Session],
    rooms: RoomGateway,
    storage: ObjectStore,
    *,
    now: datetime,
) -> HealthReport:
    def ping_database() -> None:
        with session_factory() as db:
            db.execute(text("SELECT 1"))

    database, livekit, store, queue = await asyncio.gather(
        check_dependency("database", ping_database),
        check_dependency("livekit", rooms.ping),
        check_dependency("storage", storage.ping),
        _load_queue(session_factory, now),
    )
    return HealthReport(
        status=overall_status(database, livekit, store, queue),
        checked_at=now,
        database=database,
        livekit=livekit,
        storage=store,
        queue=queue,
    )
