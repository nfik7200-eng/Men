"""Helpers shared by chain adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

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


def integer(value: object, field: str, *, minimum: int = 0, maximum: int = 2**64 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParseError(field, "expected an integer")
    if not minimum <= value <= maximum:
        raise ParseError(field, "integer out of range")
    return value


@dataclass(frozen=True, slots=True)
class SourceInfo:
    """Where a parsed record came from (feeds Provenance)."""

    provider_id: str
    method: str
    collected_at: datetime
    raw_sha256: str | None
