"""Environnement Alembic : l'URL vient de la configuration passée par `visio.db.migrate`."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

import visio.db.models  # noqa: F401  (enregistre les tables dans Base.metadata)
from visio.db.base import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # `render_as_batch` : SQLite ne sait pas modifier une table, Alembic la recrée.
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
