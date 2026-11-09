"""Applique les migrations : `uv run task db-upgrade` (URL lue dans la configuration chiffrée)."""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from visio.db.engine import _ensure_parent_directory

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def make_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    # `%` est un caractère spécial pour ConfigParser : on le double.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def upgrade(url: str, revision: str = "head") -> None:
    _ensure_parent_directory(url)
    command.upgrade(make_config(url), revision)


def downgrade(url: str, revision: str) -> None:
    command.downgrade(make_config(url), revision)


def main() -> int:
    from visio.config import ConfigurationError, get_settings

    try:
        url = get_settings().database_url
    except ConfigurationError as error:
        print(error, file=sys.stderr)
        return 1
    upgrade(url)
    print("Base de données à jour.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
