"""Helpers shared by chain adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from bip.core.exceptions import BIPError


class ParseError(BIPError):
    """An RPC result did not have the expected structure. ``field`` names the bad member."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field


def obj(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ParseError(field, "expected an object")
    return value  # pyright: ignore[reportUnknownVariableType]


def arr(value: object, field: str, *, max_len: int = 100_000) -> list[object]:
    if not isinstance(value, list):
        raise ParseError(field, "expected an array")
    if len(value) > max_len:  # pyright: ignore[reportUnknownArgumentType]
        raise ParseError(field, f"array longer than {max_len}")
    return value  # pyright: ignore[reportUnknownVariableType]


def text(value: object, field: str, *, max_len: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > max_len:
        raise ParseError(field, "expected a non-empty string")
    return value


SQL_INT_MAX = 2**63 - 1
"""Largest value SQLite INTEGER columns can hold; positions above it are rejected as malformed."""
MAX_TIMESTAMP = 253_402_300_799
"""9999-12-31T23:59:59Z, the largest second Python datetimes can represent."""


def integer(value: object, field: str, *, minimum: int = 0, maximum: int = SQL_INT_MAX) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParseError(field, "expected an integer")
    if not minimum <= value <= maximum:
        raise ParseError(field, "integer out of range")
    return value


def unix_time(seconds: int, field: str) -> datetime:
    """Convert unix seconds to an aware UTC datetime, rejecting out-of-range values."""
    if not 0 <= seconds <= MAX_TIMESTAMP:
        raise ParseError(field, "timestamp out of range")
    return datetime.fromtimestamp(seconds, tz=UTC)


@dataclass(frozen=True, slots=True)
class SourceInfo:
    """Where a parsed record came from (feeds Provenance)."""

    provider_id: str
    method: str
    collected_at: datetime
    raw_sha256: str | None
