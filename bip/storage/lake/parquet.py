"""Parquet codec for the lake (pyarrow, zstd level 3): the production :class:`Codec`.

Files are deterministic for equal rows (no timestamps or random ids in the metadata), because
content addressing (ADR-055) names a file by its hash. Each file records the schema version and
fingerprint in its key-value metadata, and decoding refuses a file written with another schema.
Timestamps are stored as UTC microseconds and come back timezone-aware.
"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyarrow ships without type information; these checks are relaxed only in the files that touch it.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from bip.storage.lake.schema import ColumnType, TableSchema

ZSTD_LEVEL = 3
_FINGERPRINT = b"bip.schema_fingerprint"
_VERSION = b"bip.schema_version"

_TYPES: dict[ColumnType, Any] = {
    ColumnType.STRING: pa.string(),
    ColumnType.INT64: pa.int64(),
    ColumnType.FLOAT64: pa.float64(),
    ColumnType.BOOL: pa.bool_(),
    ColumnType.BINARY: pa.binary(),
    ColumnType.TIMESTAMP: pa.timestamp("us", tz="UTC"),
}


def arrow_schema(schema: TableSchema) -> Any:
    fields = [pa.field(c.name, _TYPES[c.type], nullable=c.nullable) for c in schema.columns]
    return pa.schema(
        fields,
        metadata={
            _FINGERPRINT: schema.fingerprint.encode(),
            _VERSION: str(schema.version).encode(),
        },
    )


class ParquetCodec:
    name = "parquet-zstd"
    suffix = "parquet"

    def encode(self, schema: TableSchema, rows: Sequence[Mapping[str, object]], path: Path) -> None:
        target = arrow_schema(schema)
        columns = {c.name: [r[c.name] for r in rows] for c in schema.columns}
        table = pa.Table.from_pydict(columns, schema=target)
        if path.exists():
            raise FileExistsError(path)
        pq.write_table(
            table,
            str(path),
            compression="zstd",
            compression_level=ZSTD_LEVEL,
            use_dictionary=True,
            write_statistics=True,
        )

    def decode(self, schema: TableSchema, path: Path) -> list[dict[str, object]]:
        table = pq.read_table(str(path))
        meta = table.schema.metadata or {}
        if meta.get(_FINGERPRINT) != schema.fingerprint.encode():
            raise ValueError(f"{path.name} was written with another schema version")
        out: list[dict[str, object]] = []
        for row in table.to_pylist():
            clean: dict[str, object] = {}
            for c in schema.columns:
                value = row.get(c.name)
                if isinstance(value, datetime):
                    value = value.astimezone(UTC)
                clean[c.name] = value
            out.append(clean)
        return out
