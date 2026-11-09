"""Télécharge les enregistrements du S3 de dev pour les ouvrir (étape 14a, vérification manuelle).

    uv run task recordings-get                 # tous les enregistrements
    uv run task recordings-get mtg_777f5e44...  # ceux d'une réunion

Écrit dans `data/recordings/<id de réunion>/` (dossier ignoré par git) et ne modifie ni le S3 ni
la base. Seuls les fichiers `.mp4` sont téléchargés : les petits `.json` qu'Egress écrit à côté
sont des manifestes, pas des enregistrements.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from visio.config import ConfigurationError, get_settings
from visio.services.storage import Storage, StorageError

PREFIX = "meetings/"
DESTINATION = Path("data") / "recordings"


def select(objects: list[tuple[str, int]], meeting_id: str | None) -> list[tuple[str, int]]:
    """Les `.mp4` de `meetings/<id>/recordings/`, de la réunion demandée si elle est donnée."""
    wanted = f"{PREFIX}{meeting_id}/recordings/" if meeting_id else None
    return sorted(
        (key, size)
        for key, size in objects
        if "/recordings/" in key
        and key.endswith(".mp4")
        and (wanted is None or key.startswith(wanted))
    )


def local_path(key: str, root: Path = DESTINATION) -> Path:
    """`meetings/<id>/recordings/<fichier>` -> `<root>/<id>/<fichier>`."""
    parts = key.split("/")
    return root / parts[1] / parts[-1]


def main(argv: list[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    args = argv if argv is not None else []
    meeting_id = args[0] if args else None
    try:
        settings = get_settings()
    except ConfigurationError as error:
        out(str(error))
        return 1
    storage = Storage.from_settings(settings)
    try:
        found = select(storage.list_objects(PREFIX), meeting_id)
        if not found:
            out("Aucun enregistrement .mp4 trouvé sur le S3.")
            return 1
        for key, size in found:
            target = local_path(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(storage.get(key))
            out(f"  {size / 1024 / 1024:6.1f} Mo  ->  {target.resolve()}")
    except StorageError as error:
        out(f"S3 : {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
