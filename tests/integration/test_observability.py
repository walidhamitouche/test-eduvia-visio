"""Observabilité HTTP (étape 17a) : identifiant de requête, journal d'accès sans secret."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

ACCESS = "visio.access"


def service_text(caplog: pytest.LogCaptureFixture) -> str:
    """Tout ce que le **service** a journalisé (pas le client de test httpx, qui imprime l'URL)."""
    return "\n".join(
        f"{r.getMessage()} {r.__dict__}" for r in caplog.records if r.name.startswith("visio")
    )


def access_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == ACCESS]


def test_every_response_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/health")
    assert response.headers["x-request-id"].startswith("req_")


def test_a_clean_request_id_from_the_caller_is_kept(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "eduvia-7f3a9c21"})
    assert response.headers["x-request-id"] == "eduvia-7f3a9c21"


@pytest.mark.parametrize(
    "bad", ["court", "a b c d e f g h", "x" * 65, "idé-é-é-é-é", "<script>alert(1)</script>"]
)
def test_a_suspicious_request_id_is_replaced(client: TestClient, bad: str) -> None:
    try:
        response = client.get("/health", headers={"X-Request-ID": bad})
    except UnicodeEncodeError:  # le client refuse d'envoyer cet en-tête : rien à vérifier
        return
    assert response.headers["x-request-id"] != bad
    assert response.headers["x-request-id"].startswith("req_")


def test_the_access_log_uses_the_route_template_never_the_raw_url(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=ACCESS)
    secret = "jeton-secret-" + "z" * 30
    client.get(f"/j/{secret}?autre=valeur-secrete")
    (record,) = access_records(caplog)
    assert record.__dict__["route"] == "/j/{token}"
    assert record.__dict__["method"] == "GET"
    assert secret not in service_text(caplog)
    assert "valeur-secrete" not in service_text(caplog)


def test_an_invented_url_is_logged_without_its_path(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=ACCESS)
    response = client.get("/une-url-inventee/avec-un-secret-123")
    assert response.status_code == 404
    (record,) = access_records(caplog)
    assert record.__dict__["route"] == "(route inconnue)"
    assert record.__dict__["status"] == 404
    assert "avec-un-secret-123" not in service_text(caplog)


def test_the_api_key_never_appears_in_the_logs(
    client: TestClient, auth: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client.get("/v1/meetings/mtg_" + "0" * 32, headers=auth)
    secret = auth["Authorization"].split()[1]
    assert secret not in service_text(caplog)


def test_health_probes_stay_out_of_the_info_level(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=ACCESS)
    client.get("/health")
    assert access_records(caplog) == []
    caplog.set_level(logging.DEBUG, logger=ACCESS)
    client.get("/health")
    assert len(access_records(caplog)) == 1


def test_a_meeting_request_is_logged_with_its_meeting_id_and_request_id(
    client: TestClient,
    auth: dict[str, str],
    create: Callable[..., dict[str, Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    meeting_id = create()["id"]
    caplog.set_level(logging.INFO, logger=ACCESS)
    caplog.clear()  # la création de la réunion a aussi été journalisée
    response = client.get(f"/v1/meetings/{meeting_id}", headers=auth)
    (record,) = access_records(caplog)
    assert record.__dict__["meeting_id"] == meeting_id
    assert response.headers["x-request-id"].startswith("req_")
    assert record.__dict__["route"] == "/v1/meetings/{meeting_id}"
    assert record.__dict__["status"] == 200
    assert record.__dict__["duration_ms"] >= 0


def test_logs_written_during_a_request_carry_its_ids(
    client: TestClient,
    auth: dict[str, str],
    create: Callable[..., dict[str, Any]],
) -> None:
    """Le JSON est formaté à l'émission, dans le contexte de la requête : il porte ses ids."""
    from visio.logging_setup import JsonFormatter

    lines: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(JsonFormatter().format(record))

    handler = Capture()
    logger = logging.getLogger(ACCESS)
    logger.addHandler(handler)
    try:
        meeting_id = create()["id"]
        lines.clear()
        response = client.get(f"/v1/meetings/{meeting_id}", headers=auth)
    finally:
        logger.removeHandler(handler)
    (line,) = [json.loads(x) for x in lines]
    assert line["meeting_id"] == meeting_id
    assert line["request_id"] == response.headers["x-request-id"]
    assert line["route"] == "/v1/meetings/{meeting_id}"


def test_a_server_error_is_logged_as_an_error_and_returns_the_request_id(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    def boom() -> None:
        raise RuntimeError("panne")

    client.app.add_api_route("/boom", boom)  # type: ignore[attr-defined]

    caplog.set_level(logging.INFO)
    with TestClient(client.app, raise_server_exceptions=False) as tolerant:
        response = tolerant.get("/boom")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    (record,) = access_records(caplog)
    assert record.levelno == logging.ERROR
    assert record.__dict__["status"] == 500
