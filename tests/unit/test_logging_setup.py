"""Format des logs (étape 17a) : JSON valide, corrélation, secrets masqués, jamais d'exception."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

import pytest

from visio import logging_setup
from visio.logging_setup import JsonFormatter, TextFormatter, log_context


def make_record(message: str = "bonjour", **extra: Any) -> logging.LogRecord:
    record = logging.LogRecord("visio.test", logging.INFO, __file__, 1, message, (), None)
    for key, value in extra.items():
        record.__dict__[key] = value
    return record


def render(record: logging.LogRecord) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(JsonFormatter().format(record))
    return parsed


def test_a_log_line_is_one_valid_json_object() -> None:
    entry = render(make_record("réunion créée"))
    assert entry["message"] == "réunion créée"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "visio.test"
    assert entry["ts"].endswith("Z")  # UTC, ISO 8601 avec millisecondes


def test_accents_are_kept_and_the_line_has_no_line_break() -> None:
    line = JsonFormatter().format(make_record("échec\nsur deux lignes"))
    assert "\n" not in line
    assert "échec" in line


def test_extra_fields_are_added_as_is() -> None:
    entry = render(make_record(job="job_1", segments=3))
    assert entry["job"] == "job_1"
    assert entry["segments"] == 3


def test_the_context_adds_correlation_fields() -> None:
    with log_context(request_id="req_abc", meeting_id="mtg_1"):
        entry = render(make_record())
    assert entry["request_id"] == "req_abc"
    assert entry["meeting_id"] == "mtg_1"
    assert "meeting_id" not in render(make_record())  # le contexte ne fuit pas hors du bloc


def test_nested_contexts_add_up_and_an_explicit_extra_wins() -> None:
    with log_context(request_id="req_abc"), log_context(meeting_id="mtg_1"):
        assert render(make_record())["request_id"] == "req_abc"
        assert render(make_record(meeting_id="mtg_2"))["meeting_id"] == "mtg_2"


def test_a_standard_field_cannot_be_overwritten_by_an_extra() -> None:
    entry = render(make_record("vrai message", level="FAUX", logger="faux", ts="faux"))
    assert entry["message"] == "vrai message"
    assert entry["level"] == "INFO"
    assert entry["extra_level"] == "FAUX"


@pytest.mark.parametrize(
    "name", ["token", "api_key", "Authorization", "webhook_secret", "password"]
)
def test_secret_looking_fields_are_masked(name: str) -> None:
    entry = render(make_record(**{name: "valeur-sensible"}))
    assert entry[name] == "***"
    assert "valeur-sensible" not in json.dumps(entry)


def test_an_exception_is_logged_as_a_string_field() -> None:
    try:
        raise ValueError("boum")
    except ValueError:
        record = logging.LogRecord(
            "visio.test", logging.ERROR, __file__, 1, "échec", (), sys.exc_info()
        )
    entry = render(record)
    assert "ValueError: boum" in entry["exception"]


def test_a_value_that_is_not_serialisable_never_breaks_logging() -> None:
    class Weird:
        def __str__(self) -> str:
            return "bizarre"

    assert render(make_record(objet=Weird()))["objet"] == "bizarre"
    circular: list[Any] = []
    circular.append(circular)
    entry = render(make_record("tient bon", boucle=circular))  # repli : champs de base seulement
    assert entry["message"] == "tient bon"


def test_the_text_format_shows_the_correlation_ids() -> None:
    with log_context(meeting_id="mtg_1"):
        line = TextFormatter().format(make_record("bonjour", job="job_1"))
    assert "[meeting_id=mtg_1]" in line
    assert "bonjour" in line
    assert "job=job_1" in line


@pytest.mark.parametrize(
    ("fmt", "env", "expected"),
    [
        ("auto", "production", "json"),
        ("auto", "development", "text"),
        ("json", "development", "json"),
        ("text", "production", "text"),
    ],
)
def test_the_format_defaults_to_json_in_production(fmt: Any, env: str, expected: str) -> None:
    assert logging_setup.resolve_format(fmt, env) == expected


def test_configure_logging_is_idempotent_and_keeps_foreign_handlers() -> None:
    root = logging.getLogger()
    foreign = logging.NullHandler()
    root.addHandler(foreign)
    before_level = root.level
    try:
        logging_setup.configure_logging("INFO", "json", "production")
        logging_setup.configure_logging("WARNING", "text", "development")
        ours = [h for h in root.handlers if getattr(h, "_visio_handler", False)]
        assert len(ours) == 1
        assert isinstance(ours[0].formatter, TextFormatter)
        assert foreign in root.handlers
        assert root.level == logging.WARNING
        assert logging.getLogger("uvicorn.access").disabled  # il imprimerait les jetons des URL
    finally:
        for handler in [h for h in root.handlers if getattr(h, "_visio_handler", False)]:
            root.removeHandler(handler)
        root.removeHandler(foreign)
        root.setLevel(before_level)
        logging.getLogger("uvicorn.access").disabled = False
