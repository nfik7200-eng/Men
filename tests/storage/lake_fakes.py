"""Test helpers for the lake: a deterministic JSON-lines codec and an unconstrained disk guard."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from bip.storage.disk_guard import DiskGuard, DiskUsage
from bip.storage.lake.schema import Column, ColumnType, TableSchema
from bip.storage.paths import StorageRoot

GIB = 1024**3

BLOCKS = TableSchema(
    dataset="blocks",
    version=1,
    columns=(
        Column("number", ColumnType.INT64, nullable=False),
        Column("hash", ColumnType.STRING, nullable=False),
        Column("gas_used", ColumnType.INT64),
        Column("ts", ColumnType.TIMESTAMP),
        Column("payload", ColumnType.BINARY),
    ),
    sort_key=("number",),
)


def _enc(value: object) -> object:
    if isinstance(value, datetime):
        return {"t": value.isoformat()}
    if isinstance(value, bytes):
        return {"b": value.hex()}
    return value


def _dec(column: Column, value: object) -> object:
    if value is None:
        return None
    if column.type is ColumnType.TIMESTAMP:
        assert isinstance(value, dict)
        return datetime.fromisoformat(str(value["t"]))  # pyright: ignore[reportUnknownArgumentType]
    if column.type is ColumnType.BINARY:
        assert isinstance(value, dict)
        return bytes.fromhex(str(value["b"]))  # pyright: ignore[reportUnknownArgumentType]
    return value


class JsonLinesCodec:
    """Deterministic: equal rows give byte-identical files (needed for content addressing)."""

    name = "test-jsonl"
    suffix = "jsonl"

    def encode(self, schema: TableSchema, rows: Sequence[Mapping[str, object]], path: Path) -> None:
        with path.open("x", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps([_enc(r[c.name]) for c in schema.columns]) + "\n")

    def decode(self, schema: TableSchema, path: Path) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            values: list[object] = json.loads(line)
            out.append({c.name: _dec(c, v) for c, v in zip(schema.columns, values, strict=True)})
        return out


def roomy_guard(root: StorageRoot) -> DiskGuard:
    """A guard that measures the real tree but reports a large, mostly free disk."""

    def usage(_: Path) -> DiskUsage:
        return DiskUsage(total=100 * GIB, used=10 * GIB, free=90 * GIB)

    return DiskGuard(
        root, max_data_bytes=GIB, min_free_bytes=GIB, min_free_ratio=0.05, disk_usage=usage
    )


def rows(numbers: Sequence[int]) -> list[dict[str, object]]:
    return [{"number": n, "hash": f"0x{n:04x}", "gas_used": n * 10} for n in numbers]
