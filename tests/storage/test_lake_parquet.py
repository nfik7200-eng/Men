"""Lake Part B: Parquet codec (pyarrow, zstd) and the DuckDB reader over committed files only."""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyarrow ships without type information; these checks are relaxed only in the files that touch it.

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")
pytest.importorskip("duckdb")

import pyarrow.parquet as pq

from bip.core.exceptions import LakeQueryError, StorageIntegrityError
from bip.storage.lake.compaction import compact_partition
from bip.storage.lake.manifest import Lineage
from bip.storage.lake.parquet import ParquetCodec
from bip.storage.lake.reader import LakeReader, TableRef
from bip.storage.lake.runtime import lake_runtime
from bip.storage.lake.schema import Column, ColumnType, TableSchema
from bip.storage.lake.writer import LakeWriter
from bip.storage.paths import Layer, StorageRoot
from tests.storage.crash_child import PARTITION
from tests.storage.lake_fakes import BLOCKS, roomy_guard, rows

ALL_TYPES = TableSchema(
    dataset="mixed",
    version=1,
    columns=(
        Column("id", ColumnType.INT64, nullable=False),
        Column("name", ColumnType.STRING),
        Column("ratio", ColumnType.FLOAT64),
        Column("ok", ColumnType.BOOL),
        Column("raw", ColumnType.BINARY),
        Column("at", ColumnType.TIMESTAMP),
    ),
    sort_key=("id",),
)


@pytest.fixture
def root(tmp_path: Path) -> StorageRoot:
    r = StorageRoot(tmp_path / "data")
    r.create()
    return r


@pytest.fixture
def writer(root: StorageRoot) -> LakeWriter:
    return LakeWriter(root, codec=ParquetCodec(), guard=roomy_guard(root))


def test_the_runtime_reports_both_libraries() -> None:
    rt = lake_runtime()
    assert rt.available
    assert rt.missing == ()


# ---------- codec ----------


def test_every_type_round_trips_and_missing_stays_null(tmp_path: Path) -> None:
    plus2 = timezone(timedelta(hours=2))
    validated = ALL_TYPES.validate_rows(
        [
            {
                "id": 1,
                "name": "a",
                "ratio": 0.5,
                "ok": True,
                "raw": b"\x00\x01",
                "at": datetime(2026, 1, 1, 12, 0, 0, 123456, tzinfo=plus2),
            },
            {"id": 2},
        ]
    )
    path = tmp_path / "x.parquet"
    codec = ParquetCodec()
    codec.encode(ALL_TYPES, validated, path)
    back = codec.decode(ALL_TYPES, path)
    assert back[0] == {
        "id": 1,
        "name": "a",
        "ratio": 0.5,
        "ok": True,
        "raw": b"\x00\x01",
        "at": datetime(2026, 1, 1, 10, 0, 0, 123456, tzinfo=UTC),
    }
    assert back[1] == {"id": 2, "name": None, "ratio": None, "ok": None, "raw": None, "at": None}


def test_encoding_is_deterministic_and_uses_zstd(tmp_path: Path) -> None:
    validated = BLOCKS.validate_rows(rows([1, 2, 3]))
    a, b = tmp_path / "a.parquet", tmp_path / "b.parquet"
    ParquetCodec().encode(BLOCKS, validated, a)
    ParquetCodec().encode(BLOCKS, validated, b)
    assert a.read_bytes() == b.read_bytes()  # content addressing depends on this
    meta = pq.ParquetFile(a).metadata
    assert meta.row_group(0).column(0).compression == "ZSTD"
    assert meta.num_rows == 3


def test_a_file_written_with_another_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "x.parquet"
    ParquetCodec().encode(BLOCKS, BLOCKS.validate_rows(rows([1])), path)
    v2 = TableSchema(dataset="blocks", version=2, columns=BLOCKS.columns, sort_key=("number",))
    with pytest.raises(ValueError, match="schema"):
        ParquetCodec().decode(v2, path)


def test_writer_and_compaction_work_with_parquet(root: StorageRoot, writer: LakeWriter) -> None:
    a = writer.write(BLOCKS, PARTITION, rows([3, 4]), lineage=Lineage(job="t")).entry
    writer.write(BLOCKS, PARTITION, rows([1, 2]), lineage=Lineage(job="t"))
    assert a.path.endswith(".parquet")
    assert a.codec == "parquet-zstd"
    merged = compact_partition(writer, BLOCKS, PARTITION)
    assert merged is not None
    assert merged.entry.row_count == 4


# ---------- reader ----------


def ref(partition: str | None = None) -> TableRef:
    return TableRef(Layer.BRONZE, "ethereum", BLOCKS, partition=partition)


def test_queries_see_committed_files_only(root: StorageRoot, writer: LakeWriter) -> None:
    writer.write(BLOCKS, PARTITION, rows([1, 2]), lineage=Lineage(job="t"))
    writer.write(BLOCKS, PARTITION, rows([3]), lineage=Lineage(job="t"))
    leftover = root.root / PARTITION.relative_dir().as_posix() / "part-uncommitted.parquet"
    ParquetCodec().encode(BLOCKS, BLOCKS.validate_rows(rows([999])), leftover)
    reader = LakeReader(root)
    cols, data = reader.query("SELECT count(*) AS n, max(number) AS top FROM blocks", blocks=ref())
    assert cols == ["n", "top"]
    assert data == [(3, 3)]


def test_compaction_switches_readers_without_double_counting(
    root: StorageRoot, writer: LakeWriter
) -> None:
    writer.write(BLOCKS, PARTITION, rows([1, 2]), lineage=Lineage(job="t"))
    writer.write(BLOCKS, PARTITION, rows([3]), lineage=Lineage(job="t"))
    compact_partition(writer, BLOCKS, PARTITION)
    reader = LakeReader(root)
    assert reader.query("SELECT count(*) FROM b", b=ref())[1] == [(3,)]
    assert len(reader.committed_paths(ref())) == 1


def test_an_empty_table_still_has_its_columns(root: StorageRoot) -> None:
    cols, data = LakeReader(root).query("SELECT number, hash FROM b", b=ref())
    assert (cols, data) == (["number", "hash"], [])


def test_missing_or_altered_files_are_refused(root: StorageRoot, writer: LakeWriter) -> None:
    e = writer.write(BLOCKS, PARTITION, rows([1]), lineage=Lineage(job="t")).entry
    reader = LakeReader(root)
    os.unlink(root.root / e.path)
    with pytest.raises(StorageIntegrityError, match="missing"):
        reader.query("SELECT 1 FROM b", b=ref())
    writer.write(BLOCKS, PARTITION, rows([2]), lineage=Lineage(job="t"))  # a healthy second file
    (root.root / e.path).write_bytes(b"garbage")
    with pytest.raises(StorageIntegrityError):
        reader.query("SELECT 1 FROM b", b=ref())


def test_sql_cannot_touch_the_filesystem(
    root: StorageRoot, writer: LakeWriter, tmp_path: Path
) -> None:
    writer.write(BLOCKS, PARTITION, rows([1]), lineage=Lineage(job="t"))
    reader = LakeReader(root)
    target = root.root / "bronze" / "sneaky.csv"
    for sql in (
        f"COPY (SELECT 1) TO '{target}'",
        f"SELECT * FROM read_parquet('{root.root}/bronze/**/*.parquet')",  # noqa: S608 - must fail
        "SET enable_external_access = true",
    ):
        with pytest.raises(LakeQueryError, match="refused"):
            reader.query(sql, b=ref())
    assert not target.exists()


def test_partition_filter(root: StorageRoot, writer: LakeWriter) -> None:
    from bip.storage.lake.partition import Partition

    other = Partition(Layer.BRONZE, "ethereum", "blocks", (("range", "000001000-000001999"),))
    writer.write(BLOCKS, PARTITION, rows([1]), lineage=Lineage(job="t"))
    writer.write(BLOCKS, other, rows([1500, 1501]), lineage=Lineage(job="t"))
    reader = LakeReader(root)
    q = "SELECT count(*) FROM b"
    assert reader.query(q, b=ref(partition=other.key))[1] == [(2,)]
    assert reader.query(q, b=ref())[1] == [(3,)]
