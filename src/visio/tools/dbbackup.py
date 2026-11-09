"""Sauvegarde / restauration de la base SQLite (étape 19).

    python -m visio.tools.dbbackup backup  /backups/visio-AAAAMMJJ.db
    python -m visio.tools.dbbackup restore /backups/visio-AAAAMMJJ.db   (API et worker ARRÊTÉS)

La sauvegarde utilise l'API de copie de SQLite : elle est cohérente même pendant une réunion
(mode WAL). La restauration vérifie le fichier (`integrity_check`) avant de remplacer la base.
Les enregistrements et transcriptions vivent sur le S3 d'Eduvia, pas dans cette base.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

from visio.config import ConfigurationError, get_settings


def db_path(url: str) -> Path:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database:
        msg = f"sauvegarde prévue pour SQLite uniquement ({url.split(':')[0]})"
        raise ValueError(msg)
    return Path(parsed.database)


def _check(path: Path) -> None:
    with sqlite3.connect(path) as con:
        result = con.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        msg = f"{path} : base corrompue ({result})"
        raise ValueError(msg)


def backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        msg = f"{target} existe déjà : choisis un autre nom"
        raise FileExistsError(msg)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    _check(target)


def restore(backup_file: Path, target: Path) -> None:
    _check(backup_file)
    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (
        "-wal",
        "-shm",
    ):  # journaux de l'ancienne base : ils ne vont plus avec le fichier
        Path(f"{target}{suffix}").unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{backup_file}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    _check(target)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] not in ("backup", "restore"):
        print(__doc__)
        return 2
    try:
        target = db_path(get_settings().database_url)
        if args[0] == "backup":
            backup(target, Path(args[1]))
        else:
            restore(Path(args[1]), target)
    except (ConfigurationError, ValueError, OSError, sqlite3.Error) as error:
        print(f"Échec : {error}", file=sys.stderr)
        return 1
    print(f"{args[0]} terminée : {args[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
