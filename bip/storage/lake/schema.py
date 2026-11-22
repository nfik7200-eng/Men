"""Explicit, versioned table schemas for lake files and strict row validation.

Rows are validated before anything is encoded: unknown columns, missing required values and
type mismatches are refused, a missing nullable value becomes ``None`` (never zero, §2.8), and
nothing is coerced (``True`` is not an integer, ``1`` is not a float, NaN is not "missing").
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from bip.core.exceptions import RowValidationError

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1
_MAX_REPORTED_ERRORS = 10


class ColumnType(StrEnum):
    STRING = "string"
    INT64 = "int64"
    FLOAT64 = "float64"
    BOOL = "bool"
    BINARY = "binary"
    TIMESTAMP = "timestamp"
    """Timezone-aware instant, stored in UTC with microsecond precision."""


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    type: ColumnType
    nullable: bool = True


def _check_value(column: Column, value: object) -> object:
    """Return the stored form of ``value`` or raise ``ValueError`` with a value-free reason."""
    kind = column.type
    if kind is ColumnType.STRING:
        if not isinstance(value, str):
            raise ValueError("expected a string")
        return value
    if kind is ColumnType.INT64:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("expected an integer")
        if not INT64_MIN <= value <= INT64_MAX:
            raise ValueError("integer outside the int64 range")
        return value
    if kind is ColumnType.FLOAT64:
        if not isinstance(value, float):
            raise ValueError("expected a float")
        if not math.isfinite(value):
            raise ValueError("expected a finite float (use NULL for missing)")
        return value
    if kind is ColumnType.BOOL:
        if not isinstance(value, bool):
            raise ValueError("expected a boolean")
        return value
    if kind is ColumnType.BINARY:
        if not isinstance(value, bytes):
            raise ValueError("expected bytes")
        return value
    if not isinstance(value, datetime):
        raise ValueError("expected a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("expected a timezone-aware datetime")
    return value.astimezone(UTC)


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


@dataclass(frozen=True, slots=True)
class TableSchema:
    dataset: str
    version: int
    columns: tuple[Column, ...]
    sort_key: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not NAME_RE.match(self.dataset):
            raise ValueError("dataset must match [a-z][a-z0-9_]*")
        if self.version < 1:
            raise ValueError("schema version must be >= 1")
        if not self.columns:
            raise ValueError("a schema needs at least one column")
        names = [c.name for c in self.columns]
        for name in names:
            if not NAME_RE.match(name):
                raise ValueError(f"column name {name!r} must match [a-z][a-z0-9_]*")
        if len(set(names)) != len(names):
            raise ValueError("duplicate column names")
        by_name = {c.name: c for c in self.columns}
        if len(set(self.sort_key)) != len(self.sort_key):
            raise ValueError("duplicate sort key columns")
        for key in self.sort_key:
            if key not in by_name:
                raise ValueError(f"sort key column {key!r} is not in the schema")
            if by_name[key].nullable:
                raise ValueError(f"sort key column {key!r} must be non-nullable")

    @property
    def fingerprint(self) -> str:
        """SHA-256 of the canonical schema definition; equal schemas give equal fingerprints."""
        canonical = json.dumps(
            {
                "dataset": self.dataset,
                "version": self.version,
                "columns": [[c.name, c.type.value, c.nullable] for c in self.columns],
                "sort_key": list(self.sort_key),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def column(self, name: str) -> Column:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)

    def validate_rows(self, rows: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
        """Return rows in column order with stored values, or raise :class:`RowValidationError`."""
        known = {c.name for c in self.columns}
        errors: list[str] = []
        out: list[dict[str, object]] = []
        for index, raw in enumerate(rows):
            for extra in sorted(set(raw) - known):
                errors.append(f"row {index}: unknown column {extra!r}")
            clean: dict[str, object] = {}
            for col in self.columns:
                value = raw.get(col.name)
                if value is None:
                    if not col.nullable:
                        errors.append(f"row {index}: column {col.name!r} is required")
                    clean[col.name] = None
                    continue
                try:
                    clean[col.name] = _check_value(col, value)
                except ValueError as exc:
                    errors.append(f"row {index}: column {col.name!r}: {exc}")
            out.append(clean)
            if len(errors) >= _MAX_REPORTED_ERRORS:
                break
        if errors:
            raise RowValidationError(
                f"{self.dataset} v{self.version}: " + "; ".join(errors[:_MAX_REPORTED_ERRORS])
            )
        return out

    def sort_rows(self, rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
        """Order validated rows by the sort key (stable; unchanged when there is no key)."""
        if not self.sort_key:
            return list(rows)
        keys = self.sort_key
        return sorted(rows, key=lambda r: tuple(r[k] for k in keys))  # type: ignore[misc]

    def key_ranges(self, rows: Sequence[Mapping[str, object]]) -> dict[str, list[object]]:
        """``{column: [min, max]}`` for each sort key column, JSON-serialisable."""
        ranges: dict[str, list[object]] = {}
        if not rows:
            return ranges
        for key in self.sort_key:
            values = [r[key] for r in rows]
            ranges[key] = [_json_value(min(values)), _json_value(max(values))]  # type: ignore[type-var]
        return ranges
