"""Processus worker de transcription (étape 16c) : `uv run task transcriber`.

Tourne à part de l'API (le calcul est lourd). Il n'a besoin que de la base, du S3 interne et du
modèle local : **aucun accès à Internet** (en Docker : réseau sans sortie). Un seul worker à la fois
(SQLite) : deux workers se disputeraient les mêmes tâches.
"""

from __future__ import annotations

import logging
import sys
import time

from sqlalchemy.orm import Session, sessionmaker

from visio.config import ConfigurationError, Settings, get_settings
from visio.db import create_db_engine, create_session_factory
from visio.domain.clock import Clock, SystemClock
from visio.logging_setup import configure_logging
from visio.services import transcription
from visio.services.storage import ObjectStore, Storage
from visio.services.whisper_engine import WhisperTranscriber

logger = logging.getLogger("visio.transcriber")


def run_once(
    session_factory: sessionmaker[Session],
    store: ObjectStore,
    engine: transcription.Transcriber,
    clock: Clock,
    *,
    keep_audio: bool = False,
) -> int:
    with session_factory() as db:
        try:
            return transcription.run_jobs(db, store, engine, now=clock.now(), keep_audio=keep_audio)
        except Exception:
            db.rollback()
            logger.exception("tour du worker de transcription en erreur")
            return 0


def run_forever(settings: Settings, engine: transcription.Transcriber | None = None) -> None:
    store = Storage.from_settings(settings)
    db_engine = create_db_engine(settings.database_url)
    factory = create_session_factory(db_engine)
    asr = engine or WhisperTranscriber.from_settings(settings)
    clock = SystemClock()
    logger.info("worker de transcription démarré (modèle %s)", settings.transcription_model)
    if settings.transcription_keep_audio:
        logger.warning(
            "TRANSCRIPTION_KEEP_AUDIO actif : les pistes audio sont conservées (diagnostic)"
        )
    try:
        while True:
            run_once(factory, store, asr, clock, keep_audio=settings.transcription_keep_audio)
            time.sleep(settings.transcription_poll_seconds)
    except KeyboardInterrupt:
        logger.info("worker de transcription arrêté")
    finally:
        db_engine.dispose()


def main() -> int:
    configure_logging()  # avant la lecture de la config : ses erreurs sont aussi journalisées
    try:
        settings = get_settings()
    except ConfigurationError as error:
        print(error)
        return 1
    configure_logging(settings.log_level, settings.log_format, settings.app_env)
    run_forever(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
