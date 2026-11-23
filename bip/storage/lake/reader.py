"""Read committed lake files with DuckDB, with no filesystem access from SQL (ADR-055).

Only files the manifest commits (not superseded, optionally one partition) are read, after a
size check (or a full re-hash with ``deep=True``). They are handed to DuckDB as pyarrow datasets
registered under the caller's table names; the connection itself has external access disabled
and its configuration locked, so a statement cannot read, write or attach any file. Memory and
threads are capped because the host is shared.
"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyarrow ships without type information; these checks are relaxed only in the files that touch it.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.dataset as ds

from bip.core.exceptions import LakeQueryError, StorageIntegrityError
from bip.storage.lake.manifest import committed_entries, read_manifest
from bip.storage.lake.parquet import ParquetCodec, arrow_schema
from bip.storage.lake.schema import TableSchema
from bip.storage.lake.writer import file_sha256
from bip.storage.paths import Layer, StorageRoot


@dataclass(frozen=True, slots=True)
class TableRef:
    layer: Layer
    chain: str
    schema: TableSchema
    partition: str | None = None
    """``k=v/...`` of one partition, or ``None`` for every partition."""

    @property
    def table(self) -> str:
        return f"{self.chain}.{self.schema.dataset}"


class LakeReader:
    def __init__(
        self, root: StorageRoot, *, memory_limit_mb: int = 512, threads: int = 2, deep: bool = False
    ) -> None:
        self._root = root
        self._memory = memory_limit_mb
        self._threads = threads
        self._deep = deep

    def committed_paths(self, ref: TableRef) -> list[Path]:
        """Verified paths of the committed files for ``ref`` (raises on any integrity problem)."""
        state = read_manifest(self._root, ref.layer, ref.table)
        out: list[Path] = []
        for entry in committed_entries(state.entries, partition=ref.partition):
            if entry.schema_fingerprint != ref.schema.fingerprint:
                raise StorageIntegrityError(f"{entry.path} was written with another schema")
            if entry.codec != ParquetCodec.name:
                raise StorageIntegrityError(f"{entry.path} is not a Parquet file ({entry.codec})")
            path = self._root.path(entry.path)
            if not path.is_file():
                raise StorageIntegrityError(f"{entry.path} is missing; run the lake verify tool")
            if path.stat().st_size != entry.size_bytes or (
                self._deep and file_sha256(path) != entry.sha256
            ):
                raise StorageIntegrityError(f"{entry.path} does not match the manifest")
            out.append(path)
        return out

    def _dataset(self, ref: TableRef) -> Any:
        paths = self.committed_paths(ref)
        if not paths:
            return pa.Table.from_pylist([], schema=arrow_schema(ref.schema))
        return ds.dataset(
            [str(p) for p in paths], format="parquet", schema=arrow_schema(ref.schema)
        )

    def query(self, sql: str, **tables: TableRef) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Run one read-only statement over the named tables; return column names and rows."""
        datasets = {name: self._dataset(ref) for name, ref in tables.items()}
        conn = duckdb.connect(":memory:")
        try:
            conn.execute(f"SET memory_limit = '{int(self._memory)}MB'")
            conn.execute(f"SET threads = {int(self._threads)}")
            conn.execute("SET enable_external_access = false")
            conn.execute("SET lock_configuration = true")
            for name, data in datasets.items():
                conn.register(name, data)
            try:
                result = conn.execute(sql)
                columns = [d[0] for d in (result.description or [])]
                return columns, result.fetchall()
            except duckdb.Error as exc:
                raise LakeQueryError(
                    f"query refused or failed: {type(exc).__name__}: {str(exc)[:200]}"
                ) from None
        finally:
            conn.close()
