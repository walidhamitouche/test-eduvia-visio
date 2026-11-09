"""Webhooks sortants signés vers Eduvia (besoin §6, étape 12).

- **Boîte d'envoi persistante** : l'événement est écrit en base dans la *même transaction* que le
  changement qu'il annonce (`emit`), puis livré par `deliver_due` (planificateur). Un redémarrage
  ne perd rien et reprend les relances là où elles en étaient.
- **Signature** : `X-Visio-Signature: v1=<hex>`, HMAC-SHA256 de `"<timestamp>.<corps>"` avec
  `WEBHOOK_SECRET`, `X-Visio-Timestamp` en secondes. Re-signé à chaque tentative (le délai de
  validité de 5 minutes de `verify_signature` s'applique à l'envoi, pas à la création).
- **Idempotence** : `X-Visio-Event-Id` (et `id` dans le corps) est le même à chaque tentative.
- **Relances** : 10 s, 1 min, 5 min, 30 min, 2 h après chaque échec, puis abandon marqué
  (`abandoned`, rejouable : `uv run task jobs-retry`, étape 17c). Tout statut hors 2xx est un échec.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, NamedTuple

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from visio.config import Settings
from visio.db.models import (
    MeetingRow,
    WebhookAttemptRow,
    WebhookDeliveryRow,
    WebhookStatus,
    new_id,
)
from visio.domain.slot import to_paris

logger = logging.getLogger("visio.webhooks")

RETRY_DELAYS = (
    timedelta(seconds=10),
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
)
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1
SIGNATURE_TOLERANCE_SECONDS = 300
BATCH_SIZE = 50

MEETING_STARTED = "meeting.started"
MEETING_ENDED = "meeting.ended"
PARTICIPANT_JOINED = "participant.joined"
PARTICIPANT_LEFT = "participant.left"
RECORDING_READY = "recording.ready"
RECORDING_FAILED = "recording.failed"
TRANSCRIPT_READY = "transcript.ready"
TRANSCRIPT_FAILED = "transcript.failed"


class SendResult(NamedTuple):
    status_code: int | None
    error: str | None


Sender = Callable[[str, dict[str, str], bytes], SendResult]


def http_sender(url: str, headers: dict[str, str], body: bytes) -> SendResult:
    """Envoi réel : pas de redirection suivie, délai de 10 s."""
    try:
        response = httpx.post(url, content=body, headers=headers, timeout=10.0)
    except httpx.HTTPError as error:
        return SendResult(None, f"{type(error).__name__}: {error}"[:500])
    return SendResult(response.status_code, None)


# --- signature -------------------------------------------------------------------------------


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return "v1=" + mac.hexdigest()


def verify_signature(
    secret: str,
    body: bytes,
    *,
    timestamp: str,
    signature: str,
    now: float | None = None,
    tolerance: int = SIGNATURE_TOLERANCE_SECONDS,
) -> bool:
    """Côté Eduvia : vrai si la signature est correcte **et** le timestamp récent (anti-rejeu)."""
    try:
        sent = int(timestamp)
    except ValueError:
        return False
    current = time.time() if now is None else now
    if abs(current - sent) > tolerance:
        return False
    return hmac.compare_digest(sign(secret, sent, body), signature)


# --- émission --------------------------------------------------------------------------------


def encode(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def emit(
    db: Session, meeting: MeetingRow, event_type: str, data: dict[str, Any], *, now: datetime
) -> WebhookDeliveryRow:
    """Met un événement dans la boîte d'envoi (sans valider la transaction en cours)."""
    event_id = new_id("evt")
    payload: dict[str, Any] = {
        "id": event_id,
        "type": event_type,
        "created_at": to_paris(now).isoformat(),
        "meeting": {
            "id": meeting.id,
            "title": meeting.title,
            "starts_at": to_paris(meeting.starts_at).isoformat(),
            "ends_at": to_paris(meeting.ends_at).isoformat(),
            "metadata": meeting.metadata_,
        },
        "data": data,
    }
    row = WebhookDeliveryRow(
        id=event_id,
        meeting_id=meeting.id,
        event_type=event_type,
        payload=payload,
        next_attempt_at=now,
        created_at=now,
    )
    db.add(row)
    return row


def deliver_due(db: Session, settings: Settings, sender: Sender, *, now: datetime) -> int:
    """Envoie les événements dus. Renvoie le nombre de tentatives faites."""
    due = db.scalars(
        select(WebhookDeliveryRow)
        .where(
            WebhookDeliveryRow.status == WebhookStatus.PENDING,
            WebhookDeliveryRow.next_attempt_at <= now,
        )
        .order_by(WebhookDeliveryRow.created_at)
        .limit(BATCH_SIZE)
    ).all()
    secret = settings.webhook_secret.get_secret_value()
    for row in due:
        _attempt(db, settings.webhook_url, secret, sender, row, now=now)
    return len(due)


def _attempt(
    db: Session, url: str, secret: str, sender: Sender, row: WebhookDeliveryRow, *, now: datetime
) -> None:
    body = encode(row.payload)
    timestamp = int(now.timestamp())
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "visio-webhooks/1",
        "X-Visio-Event-Id": row.id,
        "X-Visio-Event-Type": row.event_type,
        "X-Visio-Timestamp": str(timestamp),
        "X-Visio-Signature": sign(secret, timestamp, body),
    }
    started = time.monotonic()
    result = sender(url, headers, body)
    elapsed = int((time.monotonic() - started) * 1000)
    ok = result.status_code is not None and 200 <= result.status_code < 300
    error = None if ok else (result.error or f"réponse HTTP {result.status_code}")

    db.add(
        WebhookAttemptRow(
            delivery_id=row.id,
            attempted_at=now,
            status_code=result.status_code,
            error=error,
            duration_ms=elapsed,
        )
    )
    row.attempts += 1
    if ok:
        row.status = WebhookStatus.DELIVERED
        row.delivered_at = now
        row.last_error = None
    else:
        row.last_error = error
        if row.attempts >= MAX_ATTEMPTS:
            row.status = WebhookStatus.ABANDONED
            logger.error(
                "webhook abandonné",
                extra={"event_id": row.id, "meeting_id": row.meeting_id, "error": error},
            )
        else:
            row.next_attempt_at = now + RETRY_DELAYS[row.attempts - 1]
    db.commit()
