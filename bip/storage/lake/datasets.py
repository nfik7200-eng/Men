"""Silver-shaped lake schemas for stored transactions and transfers, and a bytes-per-row probe.

Roadmap Stage 5 asks for bytes per row measured on real data. Until ingestion writes the lake
(Stage 6), :func:`measure_bytes_per_row` encodes the newest stored rows of the operational
database as Parquet in a temporary file, reports the size, and removes the file. The report names
the database's ``data_origin``, so a figure from fixture data is never mistaken for a real one.
Amounts stay decimal strings (wei and lamport values exceed int64); missing values stay NULL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from bip.db import schema as db
from bip.db.repository import Repository
from bip.storage.lake.schema import Column, ColumnType, TableSchema

S, INT, T = ColumnType.STRING, ColumnType.INT64, ColumnType.TIMESTAMP

TRANSACTIONS = TableSchema(
    dataset="transactions",
    version=1,
    columns=(
        Column("chain", S, nullable=False),
        Column("tx_id", S, nullable=False),
        Column("network", S),
        Column("block_number", INT),
        Column("slot", INT),
        Column("block_hash", S),
        Column("block_time", T),
        Column("from_address", S),
        Column("to_address", S),
        Column("status", S),
        Column("finality", S),
        Column("parse_status", S),
        Column("value_raw", S),
        Column("fee_raw", S),
        Column("created_contract", S),
        Column("provenance_json", S),
    ),
    sort_key=("chain", "tx_id"),
)

TRANSFERS = TableSchema(
    dataset="transfers",
    version=1,
    columns=(
        Column("chain", S, nullable=False),
        Column("tx_id", S, nullable=False),
        Column("event_key", S, nullable=False),
        Column("block_number", INT),
        Column("slot", INT),
        Column("block_time", T),
        Column("standard", S),
        Column("token", S),
        Column("token_id", S),
        Column("from_address", S),
        Column("to_address", S),
        Column("amount_raw", S),
        Column("decimals", INT),
        Column("classification", S),
        Column("observation_kind", S),
        Column("provenance_json", S),
    ),
    sort_key=("chain", "tx_id", "event_key"),
)

_SOURCES = ((TRANSACTIONS, db.transactions), (TRANSFERS, db.transfers))


def _aware(value: object) -> object:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)  # stored UTC, read back naive by SQLite
    return value


def measure_bytes_per_row(
    repo: Repository, data_root: Path, *, limit: int = 10_000
) -> dict[str, Any]:
    """Encode up to ``limit`` newest stored rows per table as Parquet and report their size."""
    from bip.storage.lake.parquet import ParquetCodec

    codec = ParquetCodec()
    tmp_dir = data_root / "tmp" / "lake-measure"
    tables: list[dict[str, Any]] = []
    for lake_schema, table in _SOURCES:
        names = [c.name for c in lake_schema.columns]
        with repo.transaction() as conn:
            raw = conn.execute(
                select(*(table.c[n] for n in names)).order_by(table.c.id.desc()).limit(limit)
            ).all()
        rows = [{n: _aware(v) for n, v in zip(names, r, strict=True)} for r in raw]
        size = 0
        if rows:
            tmp_dir.mkdir(parents=True, exist_ok=True)
            path = tmp_dir / f"{uuid.uuid4().hex}.parquet"
            try:
                codec.encode(lake_schema, lake_schema.validate_rows(rows), path)
                size = path.stat().st_size
            finally:
                if path.exists():
                    path.unlink()  # our own temporary file
        tables.append(
            {
                "dataset": lake_schema.dataset,
                "rows": len(rows),
                "bytes": size,
                "bytes_per_row": size / len(rows) if rows else None,
                "codec": codec.name,
            }
        )
    return {
        "data_origin": repo.get_meta("data_origin") or "unknown",
        "measured_at": datetime.now(UTC).isoformat(),
        "limit": limit,
        "tables": tables,
        "note": (
            "Parquet bytes per row of the newest stored rows, written to a temporary file and "
            "removed. A figure from offline_fixture or unknown data is not a real-data measurement."
        ),
    }
