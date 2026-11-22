from __future__ import annotations

import json
from pathlib import Path

import pytest

from bip.core.exceptions import StorageIntegrityError
from bip.storage.lake.manifest import (
    Lineage,
    ManifestEntry,
    append_entry,
    committed_entries,
    manifest_path,
    read_manifest,
)
from bip.storage.paths import Layer, StorageRoot


def entry(
    name: str, *, partition: str = "range=0", supersedes: tuple[str, ...] = ()
) -> ManifestEntry:
    return ManifestEntry(
        path=f"bronze/chain=ethereum/dataset=blocks/{partition}/part-{name}.jsonl",
        layer="bronze",
        table="ethereum.blocks",
        partition=partition,
        sha256=(name * 64)[:64],
        size_bytes=10,
        row_count=2,
        schema_version=1,
        schema_fingerprint="f" * 64,
        codec="test-jsonl",
        key_ranges={"number": [1, 2]},
        created_at="2026-11-22T00:00:00+00:00",
        lineage=Lineage(job="test", inputs=()),
        supersedes=supersedes,
    )


@pytest.fixture
def root(tmp_path: Path) -> StorageRoot:
    r = StorageRoot(tmp_path / "data")
    r.create()
    return r


def test_entries_round_trip_in_order(root: StorageRoot) -> None:
    a, b = entry("a"), entry("b")
    append_entry(root, a)
    append_entry(root, b)
    state = read_manifest(root, Layer.BRONZE, "ethereum.blocks")
    assert state.entries == (a, b)
    assert state.corrupt == ()
    assert manifest_path(root, Layer.BRONZE, "ethereum.blocks").name == "ethereum.blocks.jsonl"


def test_absent_manifest_reads_as_empty(root: StorageRoot) -> None:
    state = read_manifest(root, Layer.BRONZE, "ethereum.blocks")
    assert state.entries == ()
    assert state.corrupt == ()


def test_torn_trailing_line_is_ignored_and_the_next_append_starts_a_new_line(
    root: StorageRoot,
) -> None:
    a, b = entry("a"), entry("b")
    append_entry(root, a)
    path = manifest_path(root, Layer.BRONZE, "ethereum.blocks")
    with path.open("ab") as fh:  # a crash mid-append leaves half a line
        fh.write(b'{"format": 1, "path": "bronze/cha')
    state = read_manifest(root, Layer.BRONZE, "ethereum.blocks")
    assert state.entries == (a,)
    assert [c.line for c in state.corrupt] == [2]
    append_entry(root, b)
    state = read_manifest(root, Layer.BRONZE, "ethereum.blocks")
    assert state.entries == (a, b)
    assert [c.line for c in state.corrupt] == [2]


def test_a_tampered_line_fails_its_checksum(root: StorageRoot) -> None:
    append_entry(root, entry("a"))
    path = manifest_path(root, Layer.BRONZE, "ethereum.blocks")
    doc = json.loads(path.read_text())
    doc["row_count"] = 999
    path.write_text(json.dumps(doc) + "\n")
    state = read_manifest(root, Layer.BRONZE, "ethereum.blocks")
    assert state.entries == ()
    assert "checksum" in state.corrupt[0].reason


def test_an_entry_for_another_table_is_refused(root: StorageRoot) -> None:
    bad = entry("a")
    with pytest.raises(StorageIntegrityError, match="table"):
        append_entry(root, ManifestEntry(**{**bad.to_dict(), "table": "solana.blocks"}))  # type: ignore[arg-type]


def test_committed_entries_dedupe_and_drop_superseded() -> None:
    a, b = entry("a"), entry("b")
    c = entry("c", supersedes=(a.path, b.path))
    other = entry("d", partition="range=1")
    committed = committed_entries((a, b, a, c, other))
    assert committed == (c, other)
    assert committed_entries((a, b, a, c, other), partition="range=1") == (other,)


def test_conflicting_entries_for_one_path_are_an_integrity_error() -> None:
    a = entry("a")
    clash = ManifestEntry(**{**a.to_dict(), "sha256": "0" * 64})  # type: ignore[arg-type]
    with pytest.raises(StorageIntegrityError, match="conflicting"):
        committed_entries((a, clash))


def test_entry_rejects_unsafe_paths() -> None:
    with pytest.raises(ValueError, match="path"):
        ManifestEntry(**{**entry("a").to_dict(), "path": "../etc/passwd"})  # type: ignore[arg-type]
