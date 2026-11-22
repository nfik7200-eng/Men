"""Integrity verification and size statistics for one lake table.

Errors (``ok`` becomes False): committed or superseded files that are missing or whose content
no longer matches the manifest, corrupt manifest lines, and supersede references to paths the
manifest never committed. Warnings (crash leftovers, safe to keep): uncommitted data files in
the table directory and temp files under ``tmp/lake``. Nothing is deleted or repaired here.
"""

from __future__ import annotations

from dataclasses import dataclass

from bip.storage.lake.manifest import CorruptLine, committed_entries, read_manifest
from bip.storage.lake.partition import Partition
from bip.storage.lake.writer import file_sha256
from bip.storage.paths import Layer, StorageRoot


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    layer: str
    table: str
    committed_files: int
    checked_files: int
    deep: bool
    missing: tuple[str, ...]
    mismatched: tuple[str, ...]
    corrupt_lines: tuple[CorruptLine, ...]
    dangling_supersedes: tuple[str, ...]
    unreferenced: tuple[str, ...]
    stale_temp: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not (
            self.missing or self.mismatched or self.corrupt_lines or self.dangling_supersedes
        )


def verify_table(
    root: StorageRoot, layer: Layer, chain: str, dataset: str, *, deep: bool = True
) -> IntegrityReport:
    """Check every file the manifest references; ``deep`` re-hashes, otherwise sizes only."""
    table_dir_rel = Partition(layer, chain, dataset).table_dir()
    table = f"{chain}.{dataset}"
    state = read_manifest(root, layer, table)
    live = committed_entries(state.entries)

    referenced: dict[str, tuple[str, int]] = {}
    for entry in state.entries:
        referenced.setdefault(entry.path, (entry.sha256, entry.size_bytes))
    missing: list[str] = []
    mismatched: list[str] = []
    for path, (sha, size) in referenced.items():
        target = root.path(path)
        if not target.is_file():
            missing.append(path)
        elif target.stat().st_size != size or (deep and file_sha256(target) != sha):
            mismatched.append(path)

    dangling = sorted({p for e in state.entries for p in e.supersedes} - set(referenced))

    unreferenced: list[str] = []
    table_dir = root.path(table_dir_rel.as_posix())
    if table_dir.is_dir():
        for found in sorted(table_dir.rglob("part-*")):
            if found.is_file() and not found.is_symlink():
                rel = found.relative_to(root.root).as_posix()
                if rel not in referenced:
                    unreferenced.append(rel)

    stale: list[str] = []
    tmp_dir = root.root / Layer.TMP.value / "lake"
    if tmp_dir.is_dir():
        stale = sorted(
            p.relative_to(root.root).as_posix() for p in tmp_dir.iterdir() if p.is_file()
        )

    return IntegrityReport(
        layer=layer.value,
        table=table,
        committed_files=len(live),
        checked_files=len(referenced),
        deep=deep,
        missing=tuple(sorted(missing)),
        mismatched=tuple(sorted(mismatched)),
        corrupt_lines=state.corrupt,
        dangling_supersedes=tuple(dangling),
        unreferenced=tuple(unreferenced),
        stale_temp=tuple(stale),
    )


@dataclass(frozen=True, slots=True)
class TableStats:
    layer: str
    table: str
    files: int
    rows: int
    bytes: int

    @property
    def bytes_per_row(self) -> float | None:
        """``None`` when there are no rows: unknown, not zero."""
        return self.bytes / self.rows if self.rows else None


def table_stats(root: StorageRoot, layer: Layer, table: str) -> TableStats:
    """Committed (live) files only, as recorded in the manifest."""
    live = committed_entries(read_manifest(root, layer, table).entries)
    return TableStats(
        layer=layer.value,
        table=table,
        files=len(live),
        rows=sum(e.row_count for e in live),
        bytes=sum(e.size_bytes for e in live),
    )
