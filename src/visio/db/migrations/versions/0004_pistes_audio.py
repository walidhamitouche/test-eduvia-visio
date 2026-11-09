"""pistes audio par participant

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05 22:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import visio.db.types

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audio_tracks",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("meeting_id", sa.String(length=40), nullable=False),
        sa.Column("track_sid", sa.String(length=100), nullable=False),
        sa.Column("participant_identity", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "ready",
                "failed",
                name="audio_track_status",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("egress_id", sa.String(length=100), nullable=True),
        sa.Column("started_at", visio.db.types.UTCDateTime(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", visio.db.types.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            ["meetings.id"],
            name=op.f("fk_audio_tracks_meeting_id_meetings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audio_tracks")),
        sa.UniqueConstraint("track_sid", name="uq_audio_tracks_sid"),
    )
    with op.batch_alter_table("audio_tracks", schema=None) as batch_op:
        batch_op.create_index("ix_audio_tracks_meeting_id", ["meeting_id"], unique=False)
        batch_op.create_index("ix_audio_tracks_egress_id", ["egress_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("audio_tracks", schema=None) as batch_op:
        batch_op.drop_index("ix_audio_tracks_egress_id")
        batch_op.drop_index("ix_audio_tracks_meeting_id")
    op.drop_table("audio_tracks")
