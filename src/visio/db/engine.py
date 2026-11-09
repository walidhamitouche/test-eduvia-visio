"""Moteur SQLite : mode WAL, clés étrangères actives (besoin §7, DECISIONS.md §1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry


def _ensure_parent_directory(url: str) -> None:
    database = make_url(url).database
    if database and database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)


def _set_sqlite_pragmas(dbapi_connection: Any, _record: ConnectionPoolEntry) -> None:
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")  # lectures et écriture simultanées
    cursor.execute("PRAGMA foreign_keys=ON")  # désactivées par défaut dans SQLite
    cursor.execute("PRAGMA busy_timeout=5000")  # attend 5 s un verrou plutôt que d'échouer
    cursor.execute("PRAGMA synchronous=NORMAL")  # sûr avec WAL, plus rapide
    cursor.close()


def create_db_engine(url: str) -> Engine:
    """Crée le moteur (et le dossier du fichier SQLite si besoin)."""
    if not url.startswith("sqlite"):
        msg = "seul SQLite est pris en charge pour l'instant (DECISIONS.md §6 point 7)"
        raise ValueError(msg)
    _ensure_parent_directory(url)
    engine = create_engine(url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
