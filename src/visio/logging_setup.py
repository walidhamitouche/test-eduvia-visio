"""Logs structurés (étape 17a, besoin §7 « Observabilité »).

Une ligne de log = un objet JSON (production, lisible par Coolify, Loki, `jq`…) ou une ligne
de texte lisible (développement). Les champs de **corrélation** (`request_id`, `meeting_id`)
viennent d'un contexte (`contextvars`) rempli par le middleware HTTP, ou d'un
`extra={"meeting_id": ...}` posé par le code qui sait de quelle réunion il parle : retrouver tout
ce qui concerne une réunion se fait avec un seul filtre sur `meeting_id`.

Règle de sécurité : on ne journalise jamais un secret. Le formateur masque en dernier recours toute
valeur dont le nom de champ évoque un secret (`token`, `secret`, `password`, `authorization`,
`api_key`) ; la vraie protection reste de ne pas les passer au logger.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Literal

LogFormat = Literal["auto", "json", "text"]

# Champs de corrélation reconnus dans le contexte (et dans `extra=`).
CONTEXT_FIELDS = ("request_id", "meeting_id")

_context: ContextVar[Mapping[str, str] | None] = ContextVar("visio_log_context", default=None)

_MARKER = "_visio_handler"
_SECRET_NAME = re.compile(r"token|secret|password|authorization|api_?key", re.IGNORECASE)
_MASK = "***"

# Attributs standard d'un `LogRecord` : tout le reste vient d'un `extra=`.
_STANDARD = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
    | {"message", "asctime", "taskName"}
)
# Noms que les champs ajoutés par `extra=` ne doivent pas écraser.
_RESERVED = frozenset({"ts", "level", "logger", "message", "exception", *CONTEXT_FIELDS})


@contextmanager
def log_context(**values: str | None) -> Iterator[None]:
    """Ajoute des champs de corrélation à tous les logs émis dans le bloc (fils compris)."""
    merged = {**(_context.get() or {}), **{k: v for k, v in values.items() if v}}
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


def bind(**values: str | None) -> Any:
    """Variante sans bloc (middleware ASGI) : renvoie le jeton à passer à `unbind`."""
    return _context.set({**(_context.get() or {}), **{k: v for k, v in values.items() if v}})


def unbind(token: Any) -> None:
    _context.reset(token)


def current_context() -> Mapping[str, str]:
    return _context.get() or {}


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD or key.startswith("_"):
            continue
        name = f"extra_{key}" if key in _RESERVED and key not in CONTEXT_FIELDS else key
        fields[name] = _MASK if _SECRET_NAME.search(key) else value
    return fields


def _correlation(fields: dict[str, Any]) -> dict[str, Any]:
    """`extra=` l'emporte sur le contexte (le code qui logge connaît mieux la réunion)."""
    out = dict(_context.get() or {})
    for name in CONTEXT_FIELDS:
        if name in fields:
            out[name] = fields.pop(name)
    return out


def _timestamp(record: logging.LogRecord) -> str:
    moment = datetime.fromtimestamp(record.created, tz=UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class JsonFormatter(logging.Formatter):
    """Un objet JSON par ligne ; ne lève jamais (un log ne doit pas casser le service)."""

    def format(self, record: logging.LogRecord) -> str:
        fields = _extras(record)
        entry: dict[str, Any] = {
            "ts": _timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_correlation(fields),
            **fields,
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        try:
            return json.dumps(entry, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return json.dumps(
                {k: entry[k] for k in ("ts", "level", "logger", "message")}, ensure_ascii=False
            )


class TextFormatter(logging.Formatter):
    """Ligne lisible pour le développement : `heure NIVEAU logger [meeting_id] message k=v`."""

    def format(self, record: logging.LogRecord) -> str:
        fields = _extras(record)
        correlation = _correlation(fields)
        tags = " ".join(f"{k}={v}" for k, v in correlation.items())
        suffix = " ".join(f"{k}={v}" for k, v in fields.items())
        line = f"{_timestamp(record)} {record.levelname:<7} {record.name} "
        line += f"[{tags}] " if tags else ""
        line += record.getMessage() + (f" {suffix}" if suffix else "")
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def resolve_format(fmt: LogFormat, app_env: str) -> Literal["json", "text"]:
    if fmt == "auto":
        return "json" if app_env == "production" else "text"
    return fmt


def configure_logging(
    level: str = "INFO", fmt: LogFormat = "auto", app_env: str = "development"
) -> None:
    """Installe (ou remplace) le gestionnaire de logs du service. Idempotent.

    Ne touche qu'à **son** gestionnaire : ceux d'autres outils (pytest, par exemple) restent.
    Le journal d'accès d'uvicorn est coupé : il imprime l'URL brute, donc les jetons des routes
    `/j/<jeton>` et `/v1/downloads/<jeton>`. Le middleware HTTP le remplace (route générique)."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _MARKER, False)]:
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    setattr(handler, _MARKER, True)
    handler.setFormatter(
        JsonFormatter() if resolve_format(fmt, app_env) == "json" else TextFormatter()
    )
    root.addHandler(handler)
    root.setLevel(level.upper())

    # httpx / httpcore (et leurs successeurs httpx2 / httpcore2, tirés par le lot transcription)
    # impriment l'URL complète de chaque appel sortant (webhook d'Eduvia, serveur de médias) :
    # une URL peut porter un secret, on ne garde que les avertissements.
    for name in ("httpx", "httpcore", "httpx2", "httpcore2"):
        logging.getLogger(name).setLevel(logging.WARNING)

    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        # Les messages d'uvicorn passent par notre format (même sortie, même niveau).
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True
