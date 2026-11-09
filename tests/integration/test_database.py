"""Base SQLite : migrations, contraintes, fuseaux horaires (étape 3)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from visio.db import (
    Base,
    JobRow,
    LinkRow,
    MeetingRow,
    ParticipantSessionRow,
    create_db_engine,
    create_session_factory,
)
from visio.db.migrate import downgrade, upgrade
from visio.db.models import ArtifactKind, ArtifactRow, ArtifactStatus, JobStatus
from visio.domain.meeting import MeetingState
from visio.domain.roles import Role
from visio.domain.slot import can_join, parse_iso8601

TABLES = {
    "meetings",
    "links",
    "participant_sessions",
    "jobs",
    "artifacts",
    "access_entries",
    "chat_messages",
    "webhook_deliveries",
    "webhook_attempts",
    "audio_tracks",
}
PLUS_2 = timezone(timedelta(hours=2))


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'data' / 'visio.db'}"  # le dossier `data` n'existe pas encore


@pytest.fixture
def engine(db_url: str) -> Iterator[Engine]:
    upgrade(db_url)
    engine = create_db_engine(db_url)
    yield engine
    engine.dispose()  # Windows verrouille le fichier tant que le moteur est ouvert


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with create_session_factory(engine)() as db:
        yield db


def make_meeting(**overrides: object) -> MeetingRow:
    values: dict[str, object] = {
        "title": "Cours BTS MCO – Groupe A",
        "starts_at": parse_iso8601("2027-01-02T16:00:00+01:00"),
        "ends_at": parse_iso8601("2027-01-02T18:00:00+01:00"),
    }
    values.update(overrides)
    return MeetingRow(**values)


# --- Migrations -----------------------------------------------------------------------------


def test_migration_creates_the_database_file_and_every_table(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) - {"alembic_version"} == TABLES


def test_models_and_migration_are_in_sync(engine: Engine) -> None:
    """Si un modèle change sans migration, ce test échoue."""
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_migration_can_be_applied_twice_and_reverted(db_url: str) -> None:
    upgrade(db_url)
    upgrade(db_url)  # sans effet la seconde fois
    downgrade(db_url, "base")
    engine = create_db_engine(db_url)
    try:
        assert set(inspect(engine).get_table_names()) - {"alembic_version"} == set()
    finally:
        engine.dispose()


def test_engine_uses_wal_mode_and_foreign_keys(engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_engine_refuses_other_databases() -> None:
    with pytest.raises(ValueError, match="SQLite"):
        create_db_engine("postgresql://user:pass@localhost/visio")


# --- Réunions ---------------------------------------------------------------------------------


def test_meeting_defaults_and_domain_conversion(session: Session) -> None:
    meeting = make_meeting()
    session.add(meeting)
    session.commit()

    session.expire_all()
    saved = session.get(MeetingRow, meeting.id)
    assert saved is not None
    assert saved.id.startswith("mtg_")
    assert saved.state is MeetingState.SCHEDULED
    assert (saved.early_join_minutes, saved.waiting_room) == (0, False)
    assert (saved.recording, saved.transcription) == (False, False)
    assert saved.metadata_ == {}

    domain = saved.to_domain()
    assert domain.state is MeetingState.SCHEDULED
    assert not can_join(domain, parse_iso8601("2027-01-02T15:55:00+01:00"))
    assert can_join(domain, parse_iso8601("2027-01-02T16:00:00+01:00"))
    assert not can_join(domain, parse_iso8601("2027-01-02T18:00:00+01:00"))


def test_dates_come_back_as_the_same_instant_in_utc(session: Session) -> None:
    """Écrite en +02:00, relue en UTC : c'est le même instant, avec fuseau."""
    starts = datetime(2027, 7, 2, 16, 0, tzinfo=PLUS_2)
    meeting = make_meeting(starts_at=starts, ends_at=starts + timedelta(hours=2))
    session.add(meeting)
    session.commit()
    session.expire_all()

    saved = session.get(MeetingRow, meeting.id)
    assert saved is not None
    assert saved.starts_at == starts
    assert saved.starts_at == datetime(2027, 7, 2, 14, 0, tzinfo=UTC)
    assert saved.starts_at.utcoffset() == timedelta(0)  # jamais « naïve » à la relecture


def test_naive_dates_are_refused_on_write(session: Session) -> None:
    session.add(make_meeting(starts_at=datetime(2027, 1, 2, 16, 0)))
    with pytest.raises(StatementError, match="décalage"):
        session.commit()


def test_metadata_is_returned_exactly_as_stored(session: Session) -> None:
    metadata = {
        "eduvia_session_id": 1234,
        "tags": ["bts", "mco"],
        "nested": {"ok": True, "n": None},
    }
    meeting = make_meeting(metadata_=metadata)
    session.add(meeting)
    session.commit()
    session.expire_all()

    saved = session.get(MeetingRow, meeting.id)
    assert saved is not None
    assert saved.metadata_ == metadata


def test_database_refuses_an_inconsistent_slot(session: Session) -> None:
    """Dernier filet de sécurité : même sans passer par l'API, la base refuse un créneau inversé."""
    starts = parse_iso8601("2027-01-02T16:00:00+01:00")
    session.add(make_meeting(starts_at=starts, ends_at=starts))
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.parametrize("early", [-1, 61])
def test_database_refuses_an_early_join_out_of_range(session: Session, early: int) -> None:
    session.add(make_meeting(early_join_minutes=early))
    with pytest.raises(IntegrityError):
        session.commit()


def test_database_refuses_an_unknown_state(session: Session) -> None:
    session.add(make_meeting())
    session.commit()
    with pytest.raises(IntegrityError):
        session.execute(text("UPDATE meetings SET state = 'paused'"))


def test_meeting_ids_are_unique_and_opaque(session: Session) -> None:
    meetings = [make_meeting() for _ in range(50)]
    session.add_all(meetings)
    session.commit()
    ids = {meeting.id for meeting in meetings}
    assert len(ids) == 50
    assert all(len(meeting_id) > 30 for meeting_id in ids)


# --- Liens, présence, jobs, artefacts -----------------------------------------------------------


def test_links_belong_to_a_meeting_and_follow_its_deletion(session: Session) -> None:
    meeting = make_meeting()
    meeting.links = [
        LinkRow(role=Role.ORGANIZER, display_name="Marie Durand", external_id="staff_42"),
        LinkRow(role=Role.PARTICIPANT),
    ]
    session.add(meeting)
    session.commit()

    organizer, generic = sorted(meeting.links, key=lambda link: link.role)
    assert organizer.external_id == "staff_42"
    assert generic.display_name is None and generic.revoked_at is None
    assert organizer.id.startswith("lnk_")

    session.delete(meeting)
    session.commit()
    assert session.query(LinkRow).count() == 0


def test_link_to_unknown_meeting_is_refused(session: Session) -> None:
    session.add(LinkRow(meeting_id="mtg_inconnue", role=Role.PARTICIPANT))
    with pytest.raises(IntegrityError):
        session.commit()


def test_participant_sessions_keep_each_entry_and_exit(session: Session) -> None:
    meeting = make_meeting()
    session.add(meeting)
    session.flush()
    joined = parse_iso8601("2027-01-02T16:02:00+01:00")
    session.add_all(
        [
            ParticipantSessionRow(
                meeting_id=meeting.id,
                participant_identity="p1",
                display_name="Léa Martin",
                external_id="stu_7",
                role=Role.PARTICIPANT,
                joined_at=joined,
                left_at=joined + timedelta(minutes=10),
            ),
            ParticipantSessionRow(
                meeting_id=meeting.id,
                participant_identity="p1",
                display_name="Léa Martin",
                external_id="stu_7",
                role=Role.PARTICIPANT,
                joined_at=joined + timedelta(minutes=15),
            ),
        ]
    )
    session.commit()
    rows = session.query(ParticipantSessionRow).order_by(ParticipantSessionRow.joined_at).all()
    assert [row.left_at is None for row in rows] == [False, True]


def test_job_defaults(session: Session) -> None:
    job = JobRow(kind="end_meeting", payload={"meeting_id": "mtg_x"})
    session.add(job)
    session.commit()
    session.expire_all()

    saved = session.get(JobRow, job.id)
    assert saved is not None
    assert saved.status is JobStatus.PENDING
    assert saved.attempts == 0
    assert saved.payload == {"meeting_id": "mtg_x"}
    assert saved.run_at.utcoffset() == timedelta(0)


def test_artifact_is_attached_to_a_meeting(session: Session) -> None:
    meeting = make_meeting()
    session.add(meeting)
    session.flush()
    artifact = ArtifactRow(meeting_id=meeting.id, kind=ArtifactKind.RECORDING)
    session.add(artifact)
    session.commit()

    assert artifact.status is ArtifactStatus.PENDING
    assert artifact.storage_key is None

    session.delete(meeting)
    session.commit()
    assert session.query(ArtifactRow).count() == 0
