"""OpenAPI des webhooks sortants (étape 21b) : complète, valide, et fidèle à ce que Visio émet."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from openapi_spec_validator import validate

from visio.api import webhook_docs
from visio.services import webhooks

ROOT = Path(__file__).resolve().parents[2]


def _spec() -> dict[str, Any]:
    app = FastAPI(webhooks=webhook_docs.router)
    webhook_docs.install(app)
    return app.openapi()


def _emitted_types() -> set[str]:
    return {
        value
        for name, value in vars(webhooks).items()
        if name.isupper() and isinstance(value, str) and re.fullmatch(r"[a-z]+\.[a-z]+", value)
    }


def test_every_emitted_event_type_is_documented_and_nothing_more() -> None:
    assert set(_spec()["webhooks"]) == _emitted_types()


def test_spec_is_valid_openapi_31() -> None:
    spec = _spec()
    validate(spec)
    assert spec["openapi"].startswith("3.1")


def test_each_webhook_is_a_post_with_signature_headers_and_only_2xx() -> None:
    for name, item in _spec()["webhooks"].items():
        operation = item["post"]
        headers = {p["name"] for p in operation["parameters"]}
        assert headers == {"X-Visio-Event-Id", "X-Visio-Timestamp", "X-Visio-Signature"}, name
        assert set(operation["responses"]) == {"200"}, name
        assert operation["summary"] and operation["description"], name


def test_documented_example_is_a_valid_event() -> None:
    text = (ROOT / "docs" / "WEBHOOKS.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)
    payload = json.loads(blocks[0].replace("…", "x"))
    event = webhook_docs.ParticipantLeftEvent.model_validate(payload)
    assert event.data.duration_seconds == 3530
    assert event.meeting.metadata == {"eduvia_session_id": 4242}
