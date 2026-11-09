"""Télécharge les pistes audio d'une réunion pour les écouter (étape 16d, diagnostic).

    uv run task tracks-get                 # toutes les pistes encore en base
    uv run task tracks-get mtg_777f5e44...  # celles d'une réunion

Il faut que le worker tourne avec `TRANSCRIPTION_KEEP_AUDIO=true`, sinon les pistes sont supprimées
après la transcription. Écrit dans `data/tracks/<id de réunion>/<nom>-<id piste>.ogg` (dossier à
ne pas versionner : données personnelles). Lecture seule sur le S3 et la base.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select

from visio.config import ConfigurationError, get_settings
from visio.db import create_db_engine, create_session_factory
from visio.db.models import AudioTrackRow
from visio.services.storage import Storage, StorageError

DESTINATION = Path("data") / "tracks"


def file_name(display_name: str, storage_key: str) -> str:
    """`<nom sans caractères douteux>-<début de l'id de piste>.ogg`."""
    who = re.sub(r"[^A-Za-z0-9]+", "_", display_name).strip("_") or "inconnu"
    track_id = Path(storage_key).stem[:8]
    return f"{who}-{track_id}.ogg"


def main(argv: list[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    args = argv if argv is not None else []
    meeting_id = args[0] if args else None
    try:
        settings = get_settings()
    except ConfigurationError as error:
        out(str(error))
        return 1
    storage = Storage.from_settings(settings)
    engine = create_db_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as db:
            query = select(AudioTrackRow).order_by(AudioTrackRow.created_at)
            if meeting_id:
                query = query.where(AudioTrackRow.meeting_id == meeting_id)
            rows = list(db.scalars(query).all())
        if not rows:
            out("Aucune piste en base (supprimées après la transcription ? Voir KEEP_AUDIO).")
            return 1
        for row in rows:
            target = DESTINATION / row.meeting_id / file_name(row.display_name, row.storage_key)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.write_bytes(storage.get(row.storage_key))
            except StorageError as error:
                out(f"  {row.display_name} : S3 : {error}")
                continue
            out(f"  {row.display_name} -> {target.resolve()}")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
