"""Column types that enforce BIP storage conventions."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware datetime stored as an ISO-8601 UTC string (``...+00:00``).

    Naive datetimes are rejected rather than guessed. ISO strings in UTC sort correctly as text,
    so ordering and range queries work in SQLite.
    """

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetimes cannot be stored; use timezone-aware UTC")
        return value.astimezone(UTC).isoformat(timespec="microseconds")

    def process_result_value(self, value: str | None, dialect: Dialect) -> datetime | None:
        del dialect
        if value is None:
            return None
        return datetime.fromisoformat(value).astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)
