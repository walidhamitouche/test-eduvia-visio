"""Santé (étape 17b) : règles pures du statut global et sondes bornées dans le temps."""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from visio.domain.errors import MediaServerError
from visio.services import health
from visio.services.health import (
    DependencyCheck,
    JobsQueue,
    QueueReport,
    WebhooksQueue,
    overall_status,
)
from visio.services.storage import StorageError

UP = DependencyCheck("ok", 1.0)
DOWN = DependencyCheck("down", 1.0, "injoignable")


def queue(status: str = "ok") -> QueueReport:
    return QueueReport(
        status="degraded" if status == "degraded" else "ok",
        jobs=JobsQueue(0, 0, 0, None, False),
        webhooks=WebhooksQueue(0, 0, None, False),
        artifacts_failed=0,
    )


@pytest.mark.parametrize(
    ("database", "livekit", "storage", "queue_report", "expected"),
    [
        (UP, UP, UP, queue(), "ok"),
        (UP, DOWN, UP, queue(), "degraded"),
        (UP, UP, DOWN, queue(), "degraded"),
        (UP, DOWN, DOWN, queue(), "degraded"),
        (UP, UP, UP, queue("degraded"), "degraded"),
        (UP, UP, UP, None, "degraded"),  # file illisible alors que la base répond
        (DOWN, UP, UP, None, "down"),
        (DOWN, DOWN, DOWN, None, "down"),
        (DOWN, UP, UP, queue(), "down"),  # la base prime sur tout
    ],
)
def test_the_overall_status(
    database: DependencyCheck,
    livekit: DependencyCheck,
    storage: DependencyCheck,
    queue_report: QueueReport | None,
    expected: str,
) -> None:
    assert overall_status(database, livekit, storage, queue_report) == expected


def probe(name: str, fn: object) -> DependencyCheck:
    return asyncio.run(health.check_dependency(name, fn))  # type: ignore[arg-type]


def test_a_working_probe_is_ok() -> None:
    result = probe("x", lambda: None)
    assert result.status == "ok"
    assert result.detail is None


@pytest.mark.parametrize(
    "error",
    [
        MediaServerError("le serveur de médias ne répond pas (ConnectError)"),
        StorageError("S3 injoignable : EndpointConnectionError"),
        RuntimeError("postgres://user:motdepasse@hote/base"),
    ],
)
def test_a_failing_probe_never_exposes_the_error_text(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    def failing() -> None:
        raise error

    caplog.set_level(logging.WARNING)
    result = probe("x", failing)
    assert result == DependencyCheck("down", result.latency_ms, "injoignable")
    assert "motdepasse" not in repr(result)
    logged = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "motdepasse" not in logged  # le message d'une exception inconnue n'est pas consigné
    assert type(error).__name__ in logged


def test_our_own_errors_are_logged_with_their_message(caplog: pytest.LogCaptureFixture) -> None:
    def failing() -> None:
        raise StorageError("bucket introuvable")

    caplog.set_level(logging.WARNING)
    probe("storage", failing)
    assert "bucket introuvable" in " ".join(str(r.__dict__) for r in caplog.records)


def test_a_probe_that_hangs_is_cut_at_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.05)

    async def timed() -> tuple[DependencyCheck, float]:
        # Mesuré dans la coroutine : `asyncio.run` attend ensuite la fin du fil qui dort.
        started = time.perf_counter()
        result = await health.check_dependency("x", lambda: time.sleep(0.5))
        return result, time.perf_counter() - started

    result, elapsed = asyncio.run(timed())
    assert elapsed < 0.4
    assert result.status == "down"
    assert result.detail == "délai dépassé"
