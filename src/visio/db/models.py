"""Modèle de données (étape 3). Les tables `participant_sessions`, `jobs` et `artifacts`
sont préparées : leur usage arrive aux étapes 10, 11, 12, 14 et 16."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from visio.db.base import Base
from visio.db.types import UTCDateTime, string_enum
from visio.domain.meeting import Meeting, MeetingState
from visio.domain.roles import Role
from visio.domain.slot import MAX_EARLY_JOIN_MINUTES


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    ABANDONED = "abandoned"


class AccessStatus(StrEnum):
    """Décision d'accès d'une personne à une réunion (salle d'attente, exclusion)."""

    WAITING = "waiting"
    ADMITTED = "admitted"
    DENIED = "denied"
    EXCLUDED = "excluded"


class WebhookStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    ABANDONED = "abandoned"


class ArtifactKind(StrEnum):
    RECORDING = "recording"
    TRANSCRIPT = "transcript"


class ArtifactStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


def new_id(prefix: str) -> str:
    """Identifiant opaque : 128 bits aléatoires (`secrets`, pas UUID4 qui n'en a que 122).

    Pour un lien, c'est le `jti` du jeton ; il n'a aucun rapport avec l'id de la réunion.
    """
    return f"{prefix}_{secrets.token_hex(16)}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class MeetingRow(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="slot_is_ordered"),
        CheckConstraint(
            f"early_join_minutes BETWEEN 0 AND {MAX_EARLY_JOIN_MINUTES}", name="early_join_range"
        ),
        Index("ix_meetings_state_ends_at", "state", "ends_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("mtg"))
    title: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    early_join_minutes: Mapped[int] = mapped_column(Integer, default=0)
    waiting_room: Mapped[bool] = mapped_column(Boolean, default=False)
    recording: Mapped[bool] = mapped_column(Boolean, default=False)
    transcription: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[MeetingState] = mapped_column(
        string_enum(MeetingState, "meeting_state"), default=MeetingState.SCHEDULED
    )
    # Nom de la salle côté serveur de médias (opaque, voir `livekit_tokens.room_name`) : permet
    # de retrouver la réunion depuis un webhook LiveKit. Posé à la première entrée.
    room_name: Mapped[str | None] = mapped_column(String(60), default=None, index=True)
    # Première connexion (webhook `meeting.started`) et clôture anticipée éventuelle.
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    # Libre, renvoyé tel quel par l'API et les webhooks. `metadata` est réservé par SQLAlchemy.
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    links: Mapped[list[LinkRow]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan", passive_deletes=True
    )

    def to_domain(self) -> Meeting:
        """Version « logique pure » de la réunion, utilisée par la règle du créneau."""
        return Meeting(
            id=self.id,
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            early_join_minutes=self.early_join_minutes,
            state=self.state,
            ended_at=self.ended_at,
        )


class LinkRow(Base):
    """Un lien = un jeton. `id` est le `jti` du jeton ; la base reste la source de vérité."""

    __tablename__ = "links"
    __table_args__ = (Index("ix_links_meeting_id", "meeting_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("lnk"))
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    role: Mapped[Role] = mapped_column(string_enum(Role, "link_role"))
    # Renseignés pour un lien nominatif ; vides pour le lien participant générique.
    display_name: Mapped[str | None] = mapped_column(String(200), default=None)
    external_id: Mapped[str | None] = mapped_column(String(200), default=None)
    # Liste de révocation : un lien révoqué est refusé (exclusion définitive, étape 8).
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    meeting: Mapped[MeetingRow] = relationship(back_populates="links")


class ParticipantSessionRow(Base):
    """Une présence (entrée → sortie) d'une personne dans une réunion (étape 11)."""

    __tablename__ = "participant_sessions"
    __table_args__ = (Index("ix_participant_sessions_meeting_id", "meeting_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ses"))
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    link_id: Mapped[str | None] = mapped_column(
        ForeignKey("links.id", ondelete="SET NULL"), default=None
    )
    # Identité côté LiveKit : distingue deux personnes qui utilisent le même lien générique.
    participant_identity: Mapped[str] = mapped_column(String(100))
    # Identifiant de connexion côté serveur de médias : distingue deux connexions successives
    # (ou simultanées) d'une même identité.
    participant_sid: Mapped[str | None] = mapped_column(String(100), default=None)
    display_name: Mapped[str] = mapped_column(String(200))
    external_id: Mapped[str | None] = mapped_column(String(200), default=None)
    role: Mapped[Role] = mapped_column(string_enum(Role, "session_role"))
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime)
    left_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)


class JobRow(Base):
    """File de tâches persistante : fin de créneau, webhooks, enregistrement, transcription."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_run_at", "status", "run_at"),
        Index("ix_jobs_meeting_id", "meeting_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("job"))
    kind: Mapped[str] = mapped_column(String(50))
    meeting_id: Mapped[str | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), default=None
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[JobStatus] = mapped_column(
        string_enum(JobStatus, "job_status"), default=JobStatus.PENDING
    )
    run_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ArtifactRow(Base):
    """Enregistrement ou transcription stocké sur S3 (étapes 14 à 16)."""

    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_meeting_id", "meeting_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("art"))
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    kind: Mapped[ArtifactKind] = mapped_column(string_enum(ArtifactKind, "artifact_kind"))
    status: Mapped[ArtifactStatus] = mapped_column(
        string_enum(ArtifactStatus, "artifact_status"), default=ArtifactStatus.PENDING
    )
    # Chemin opaque dans le bucket, ex. meetings/<id>/recordings/<uuid>.mp4
    storage_key: Mapped[str | None] = mapped_column(String(500), default=None)
    # Identifiant de l'enregistrement côté Egress (étape 14) : sert à l'arrêter et à suivre sa fin.
    egress_id: Mapped[str | None] = mapped_column(String(100), default=None, index=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AudioTrackRow(Base):
    """Piste audio d'**un** participant, enregistrée à part pour la transcription (étape 16b).

    Une ligne par piste micro publiée (une personne qui se reconnecte en a plusieurs). Le nom et
    l'`external_id` sont ceux du lien, figés à la publication : c'est ce qui attribue la parole
    au bon locuteur, sans jamais deviner à partir de la voix. Fichiers de travail : supprimés
    après la transcription (`services.tracks.purge`)."""

    __tablename__ = "audio_tracks"
    __table_args__ = (
        UniqueConstraint("track_sid", name="uq_audio_tracks_sid"),
        Index("ix_audio_tracks_meeting_id", "meeting_id"),
        Index("ix_audio_tracks_egress_id", "egress_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("trk"))
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    track_sid: Mapped[str] = mapped_column(String(100))
    participant_identity: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(200))
    external_id: Mapped[str | None] = mapped_column(String(200), default=None)
    status: Mapped[ArtifactStatus] = mapped_column(
        string_enum(ArtifactStatus, "audio_track_status"), default=ArtifactStatus.PENDING
    )
    # Chemin opaque : meetings/<id>/audio/<uuid>.ogg
    storage_key: Mapped[str] = mapped_column(String(500))
    egress_id: Mapped[str | None] = mapped_column(String(100), default=None)
    # Instant où le fichier commence (publication de la piste, puis début réel annoncé par
    # Egress) : sert à replacer les phrases sur la chronologie de la réunion.
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    size_bytes: Mapped[int | None] = mapped_column(Integer, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AccessRow(Base):
    """Qui a le droit d'entrer, et sous quel nom : une ligne par personne et par réunion
    (identité opaque, voir `livekit_tokens`). Porte la salle d'attente (étape 9), l'exclusion
    définitive (étape 8) et le nom de l'auteur des messages (étape 11)."""

    __tablename__ = "access_entries"
    __table_args__ = (
        UniqueConstraint("meeting_id", "participant_identity", name="uq_access_identity"),
        Index("ix_access_entries_meeting_id", "meeting_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("acc"))
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    link_id: Mapped[str | None] = mapped_column(
        ForeignKey("links.id", ondelete="SET NULL"), default=None
    )
    participant_identity: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(200))
    external_id: Mapped[str | None] = mapped_column(String(200), default=None)
    role: Mapped[Role] = mapped_column(string_enum(Role, "access_role"))
    status: Mapped[AccessStatus] = mapped_column(
        string_enum(AccessStatus, "access_status"), default=AccessStatus.ADMITTED
    )
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # Dernier signe de vie d'une personne en attente (elle relit sa demande toutes les 2 s) :
    # une demande qui n'est plus relue n'est plus affichée à l'organisateur.
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    decided_by: Mapped[str | None] = mapped_column(String(100), default=None)


class ChatMessageRow(Base):
    """Message de la discussion, conservé côté serveur (exportable par API)."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_meeting_id", "meeting_id", "sent_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("msg"))
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    participant_identity: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(200))
    external_id: Mapped[str | None] = mapped_column(String(200), default=None)
    role: Mapped[Role] = mapped_column(string_enum(Role, "chat_role"))
    text: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class WebhookDeliveryRow(Base):
    """Un événement à livrer à Eduvia (file d'envoi persistante, étape 12). Écrit dans la même
    transaction que le changement qu'il annonce : jamais d'événement perdu ni inventé."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_status_next", "status", "next_attempt_at"),
        Index("ix_webhook_deliveries_meeting_id", "meeting_id"),
    )

    # Identifiant de l'événement : le même à chaque tentative (idempotence côté Eduvia).
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("evt"))
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[WebhookStatus] = mapped_column(
        string_enum(WebhookStatus, "webhook_status"), default=WebhookStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)


class WebhookAttemptRow(Base):
    """Journal des tentatives d'envoi d'un webhook."""

    __tablename__ = "webhook_attempts"
    __table_args__ = (Index("ix_webhook_attempts_delivery_id", "delivery_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("att"))
    delivery_id: Mapped[str] = mapped_column(
        ForeignKey("webhook_deliveries.id", ondelete="CASCADE")
    )
    attempted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    status_code: Mapped[int | None] = mapped_column(Integer, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
