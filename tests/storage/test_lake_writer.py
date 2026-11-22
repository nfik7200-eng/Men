from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from bip.core.exceptions import (
    RowValidationError,
    StorageIntegrityError,
    StorageLimitError,
    StoragePathError,
)
from bip.storage.disk_guard import DiskGuard, DiskUsage
from bip.storage.lake.compaction import compact_partition
from bip.storage.lake.integrity import table_stats, verify_table
from bip.storage.lake.manifest import Lineage, committed_entries, manifest_path, read_manifest
from bip.storage.lake.partition import Partition
from bip.storage.lake.writer import LakeWriter, file_sha256
from bip.storage.paths import Layer, StorageRoot
from tests.storage.crash_child import PARTITION
from tests.storage.lake_fakes import BLOCKS, GIB, JsonLinesCodec, roomy_guard, rows

REPO = Path(__file__).resolve().parents[2]
TABLE = "ethereum.blocks"


@pytest.fixture
def root(tmp_path: Path) -> StorageRoot:
    r = StorageRoot(tmp_path / "data")
    r.create()
    return r


@pytest.fixture
def writer(root: StorageRoot) -> LakeWriter:
    return LakeWriter(root, codec=JsonLinesCodec(), guard=roomy_guard(root))


def committed(root: StorageRoot) -> list[str]:
    state = read_manifest(root, Layer.BRONZE, TABLE)
    return [e.path for e in committed_entries(state.entries)]


# ---------- writing ----------


def test_write_places_a_content_addressed_file_and_commits_it(
    root: StorageRoot, writer: LakeWriter
) -> None:
    result = writer.write(BLOCKS, PARTITION, rows([3, 1, 2]), lineage=Lineage(job="t"))
    e = result.entry
    assert result.created
    target = root.root / e.path
    assert target.is_file()
    assert e.path.startswith("bronze/chain=ethereum/dataset=blocks/range=000000000-000000999/")
    assert Path(e.path).name == f"part-{e.sha256[:32]}.jsonl"
    assert file_sha256(target) == e.sha256
    assert e.size_bytes == target.stat().st_size
    assert e.row_count == 3
    assert e.key_ranges == {"number": [1, 3]}
    assert e.schema_fingerprint == BLOCKS.fingerprint
    assert e.partition == "range=000000000-000000999"
    assert committed(root) == [e.path]
    decoded = JsonLinesCodec().decode(BLOCKS, target)
    assert [r["number"] for r in decoded] == [1, 2, 3]  # sorted by the sort key
    assert decoded[0]["ts"] is None  # missing stays NULL
    assert list((root.root / "tmp").rglob("*.jsonl")) == []


def test_rewriting_the_same_rows_in_any_order_is_idempotent(
    root: StorageRoot, writer: LakeWriter
) -> None:
    first = writer.write(BLOCKS, PARTITION, rows([1, 2, 3]), lineage=Lineage(job="t"))
    again = writer.write(BLOCKS, PARTITION, rows([3, 2, 1]), lineage=Lineage(job="t"))
    assert not again.created
    assert again.entry == first.entry
    assert len(read_manifest(root, Layer.BRONZE, TABLE).entries) == 1


def test_invalid_rows_write_nothing(root: StorageRoot, writer: LakeWriter) -> None:
    with pytest.raises(RowValidationError):
        writer.write(BLOCKS, PARTITION, [{"number": 1}], lineage=Lineage(job="t"))
    assert not (root.root / "bronze").exists()
    assert not manifest_path(root, Layer.BRONZE, TABLE).exists()


def test_empty_batch_and_wrong_dataset_are_refused(writer: LakeWriter) -> None:
    with pytest.raises(ValueError, match="no rows"):
        writer.write(BLOCKS, PARTITION, [], lineage=Lineage(job="t"))
    other = Partition(Layer.BRONZE, "ethereum", "logs")
    with pytest.raises(ValueError, match="dataset"):
        writer.write(BLOCKS, other, rows([1]), lineage=Lineage(job="t"))


def test_disk_guard_refusal_writes_nothing(root: StorageRoot) -> None:
    def full(_: Path) -> DiskUsage:
        return DiskUsage(total=100 * GIB, used=99 * GIB, free=GIB // 2)

    guard = DiskGuard(
        root, max_data_bytes=GIB, min_free_bytes=GIB, min_free_ratio=0.0, disk_usage=full
    )
    w = LakeWriter(root, codec=JsonLinesCodec(), guard=guard)
    with pytest.raises(StorageLimitError):
        w.write(BLOCKS, PARTITION, rows([1]), lineage=Lineage(job="t"))
    assert not (root.root / "bronze").exists()


def test_an_existing_file_with_different_content_is_never_overwritten(
    root: StorageRoot, writer: LakeWriter
) -> None:
    probe = writer.write(BLOCKS, PARTITION, rows([1]), lineage=Lineage(job="t"))
    # Simulate corruption of an uncommitted leftover at the target name of a new batch.
    fresh = StorageRoot(root.root.parent / "fresh")
    fresh.create()
    w2 = LakeWriter(fresh, codec=JsonLinesCodec(), guard=roomy_guard(fresh))
    target = fresh.root / probe.entry.path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"corrupt")
    with pytest.raises(StorageIntegrityError, match="refusing to overwrite"):
        w2.write(BLOCKS, PARTITION, rows([1]), lineage=Lineage(job="t"))
    assert target.read_bytes() == b"corrupt"
    assert list((fresh.root / "tmp").rglob("*.jsonl")) == []


def test_a_committed_file_that_was_altered_is_reported_not_reused(
    root: StorageRoot, writer: LakeWriter
) -> None:
    e = writer.write(BLOCKS, PARTITION, rows([1]), lineage=Lineage(job="t")).entry
    (root.root / e.path).write_bytes(b"tampered")
    with pytest.raises(StorageIntegrityError, match="does not match"):
        writer.write(BLOCKS, PARTITION, rows([1]), lineage=Lineage(job="t"))


def test_storage_root_must_exist(tmp_path: Path) -> None:
    missing = StorageRoot(tmp_path / "nope")
    w = LakeWriter(missing, codec=JsonLinesCodec(), guard=roomy_guard(missing))
    with pytest.raises(StoragePathError):
        w.write(BLOCKS, PARTITION, rows([1]), lineage=Lineage(job="t"))


def test_supersedes_must_name_committed_files_of_the_same_partition(
    writer: LakeWriter,
) -> None:
    with pytest.raises(StorageIntegrityError, match="supersede"):
        writer.write(
            BLOCKS,
            PARTITION,
            rows([1]),
            lineage=Lineage(job="t"),
            supersedes=("bronze/chain=ethereum/dataset=blocks/x/part-0.jsonl",),
        )


# ---------- crash safety (real process death) ----------


def crash_at(root: StorageRoot, stage: str) -> int:
    proc = subprocess.run(  # noqa: S603 - fixed interpreter and a module from this repo
        [
            sys.executable,
            "-c",
            "from tests.storage.crash_child import main; main()",
            str(root.root),
            stage,
        ],
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO)},
        check=False,
        timeout=60,
    )
    return proc.returncode


@pytest.mark.parametrize("stage", ["encoded", "placed"])
def test_a_crash_before_the_manifest_commit_leaves_nothing_committed_and_rerun_recovers(
    root: StorageRoot, writer: LakeWriter, stage: str
) -> None:
    assert crash_at(root, stage) == 137
    assert committed(root) == []
    report = verify_table(root, Layer.BRONZE, "ethereum", "blocks")
    assert report.ok  # leftovers are warnings, not corruption
    if stage == "encoded":
        assert len(report.stale_temp) == 1
        assert report.unreferenced == ()
    else:
        assert len(report.unreferenced) == 1
        assert report.stale_temp == ()

    result = writer.write(BLOCKS, PARTITION, rows([1, 2, 3]), lineage=Lineage(job="crash-test"))
    assert result.created
    assert committed(root) == [result.entry.path]
    after = verify_table(root, Layer.BRONZE, "ethereum", "blocks")
    assert after.ok
    assert after.unreferenced == ()  # the leftover was verified and adopted, not duplicated


def test_a_crash_after_the_commit_keeps_the_batch(root: StorageRoot, writer: LakeWriter) -> None:
    assert crash_at(root, "committed") == 137
    assert len(committed(root)) == 1
    again = writer.write(BLOCKS, PARTITION, rows([1, 2, 3]), lineage=Lineage(job="crash-test"))
    assert not again.created  # a resumed job replaying the batch adds nothing


# ---------- integrity and stats ----------


def test_verify_reports_missing_and_mismatched_files(root: StorageRoot, writer: LakeWriter) -> None:
    a = writer.write(BLOCKS, PARTITION, rows([1]), lineage=Lineage(job="t")).entry
    b = writer.write(BLOCKS, PARTITION, rows([2]), lineage=Lineage(job="t")).entry
    os.unlink(root.root / a.path)
    (root.root / b.path).write_bytes(b"x" * b.size_bytes)  # same size, different content
    deep = verify_table(root, Layer.BRONZE, "ethereum", "blocks")
    assert not deep.ok
    assert deep.missing == (a.path,)
    assert deep.mismatched == (b.path,)
    shallow = verify_table(root, Layer.BRONZE, "ethereum", "blocks", deep=False)
    assert shallow.mismatched == ()  # a size check cannot see it; that is why deep is default


def test_table_stats_measure_bytes_per_row(root: StorageRoot, writer: LakeWriter) -> None:
    writer.write(BLOCKS, PARTITION, rows([1, 2]), lineage=Lineage(job="t"))
    writer.write(BLOCKS, PARTITION, rows([3, 4]), lineage=Lineage(job="t"))
    stats = table_stats(root, Layer.BRONZE, TABLE)
    assert stats.files == 2
    assert stats.rows == 4
    assert stats.bytes_per_row == pytest.approx(stats.bytes / 4)
    empty = table_stats(root, Layer.BRONZE, "solana.blocks")
    assert empty.rows == 0
    assert empty.bytes_per_row is None  # unknown, not zero


# ---------- compaction ----------


def test_compaction_merges_and_keeps_superseded_files(
    root: StorageRoot, writer: LakeWriter
) -> None:
    a = writer.write(BLOCKS, PARTITION, rows([5, 6]), lineage=Lineage(job="t")).entry
    b = writer.write(BLOCKS, PARTITION, rows([1, 2]), lineage=Lineage(job="t")).entry
    result = compact_partition(writer, BLOCKS, PARTITION)
    assert result is not None
    c = result.entry
    assert c.supersedes == (a.path, b.path)
    assert c.lineage.inputs == (a.sha256, b.sha256)
    assert c.row_count == 4
    assert committed(root) == [c.path]
    assert (root.root / a.path).is_file()  # never deleted
    assert (root.root / b.path).is_file()
    decoded = JsonLinesCodec().decode(BLOCKS, root.root / c.path)
    assert [r["number"] for r in decoded] == [1, 2, 5, 6]
    assert verify_table(root, Layer.BRONZE, "ethereum", "blocks").ok
    assert compact_partition(writer, BLOCKS, PARTITION) is None  # one file left: nothing to do


def test_compaction_refuses_a_schema_mismatch(root: StorageRoot, writer: LakeWriter) -> None:
    writer.write(BLOCKS, PARTITION, rows([1]), lineage=Lineage(job="t"))
    writer.write(BLOCKS, PARTITION, rows([2]), lineage=Lineage(job="t"))
    from bip.storage.lake.schema import TableSchema

    v2 = TableSchema(dataset="blocks", version=2, columns=BLOCKS.columns, sort_key=("number",))
    with pytest.raises(StorageIntegrityError, match="schema"):
        compact_partition(writer, v2, PARTITION)


def test_compaction_refuses_a_corrupt_input(root: StorageRoot, writer: LakeWriter) -> None:
    a = writer.write(BLOCKS, PARTITION, rows([1]), lineage=Lineage(job="t")).entry
    writer.write(BLOCKS, PARTITION, rows([2]), lineage=Lineage(job="t"))
    (root.root / a.path).write_bytes(b"[]\n")
    with pytest.raises(StorageIntegrityError):
        compact_partition(writer, BLOCKS, PARTITION)
    assert len(committed(root)) == 2
