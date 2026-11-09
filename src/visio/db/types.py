"""Types SQLAlchemy partagés."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, Dialect, Enum, TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Date avec fuseau, stockée en UTC.

    SQLite n'a pas de type « date avec fuseau » : sans ce garde-fou, un `+02:00` serait
    enregistré tel quel et relu comme une date sans fuseau. Ici, une date sans décalage est
    refusée à l'écriture, et toute lecture renvoie un instant UTC.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "date sans décalage horaire refusée (ISO 8601 avec décalage exigé)"
            raise ValueError(msg)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def string_enum(enum_class: type[StrEnum], name: str) -> Enum:
    """Énumération stockée en texte avec contrainte CHECK (pas de type natif, portable)."""
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )
