"""artefact egress_id

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-04 22:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("egress_id", sa.String(length=100), nullable=True))
        batch_op.create_index(batch_op.f("ix_artifacts_egress_id"), ["egress_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_artifacts_egress_id"))
        batch_op.drop_column("egress_id")
