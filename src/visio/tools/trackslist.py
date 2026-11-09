"""Liste les pistes audio par participant (étape 16b, vérification manuelle).

    uv run task tracks-list

Pour chaque piste : la personne (nom et `external_id` du lien), l'état, la taille réellement
présente sur le S3 et, si elle n'est pas prête, pourquoi. Lecture seule.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from sqlalchemy import select

from visio.config import ConfigurationError, get_settings
from visio.db import create_db_engine, create_session_factory
from visio.db.models import AudioTrackRow
from visio.services.storage import Storage, StorageError
from visio.tools.recordings import format_size


def render(row: AudioTrackRow, s3_size: int | None) -> list[str]:
    who = row.display_name + (f" ({row.external_id})" if row.external_id else "")
    size = "absent du S3" if s3_size is None else format_size(s3_size)
    lines = [
        f"  {who} : {row.status.value}, {size}  [{row.meeting_id}]",
        f"      {row.storage_key}",
    ]
    if row.error:
        lines.append(f"      erreur : {row.error}")
    elif row.egress_id is None:
        lines.append("      Egress pas encore démarré (tâche en attente)")
    return lines


def main(argv: list[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    del argv
    try:
        settings = get_settings()
    except ConfigurationError as error:
        out(str(error))
        return 1
    storage = Storage.from_settings(settings)
    engine = create_db_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as db:
            rows = list(db.scalars(select(AudioTrackRow).order_by(AudioTrackRow.created_at)).all())
            out(f"{len(rows)} piste(s) audio en base :")
            for row in rows:
                try:
                    size = storage.size(row.storage_key)
                except StorageError as error:
                    out(f"S3 : {error}")
                    return 1
                for line in render(row, size):
                    out(line)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
