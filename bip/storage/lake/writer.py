"""Atomic, content-addressed, idempotent writes into the lake.

``LakeWriter.write`` follows a fixed protocol (ADR-055):

1. validate rows against the schema and sort them by the sort key (deterministic content);
2. ask the :class:`DiskGuard` with a conservative size estimate;
3. encode to a temp file under ``tmp/lake/`` and fsync it (stage ``encoded``);
4. hash it; the final name is ``part-<sha256[:32]>.<suffix>`` inside the partition directory;
5. under the lake lock: if the manifest already commits that path, verify and reuse it; if an
   uncommitted file sits there (a crash leftover), adopt it only when its hash matches; never
   overwrite (stage ``placed``);
6. append the manifest entry, the commit point (stage ``committed``).

A crash before step 6 leaves nothing committed; re-running the same batch adopts the leftover.
The only file this module ever removes is its own temp file.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import uuid
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from bip.core.exceptions import StorageIntegrityError
from bip.storage.disk_guard import DiskGuard
from bip.storage.lake.manifest import (
    Lineage,
    ManifestEntry,
    append_entry,
    committed_entries,
    read_manifest,
)
from bip.storage.lake.partition import Partition
from bip.storage.lake.schema import TableSchema
from bip.storage.paths import Layer, StorageRoot

WRITER_ID = "bip-lake/1"
_CHUNK = 1024 * 1024


class Codec(Protocol):
    """Turns validated rows into one file and back. Must be deterministic for equal rows."""

    @property
    def name(self) -> str: ...

    @property
    def suffix(self) -> str: ...

    def encode(
        self, schema: TableSchema, rows: Sequence[Mapping[str, object]], path: Path
    ) -> None: ...

    def decode(self, schema: TableSchema, path: Path) -> list[dict[str, object]]: ...


@dataclass(frozen=True, slots=True)
class WriteResult:
    entry: ManifestEntry
    created: bool
    """False when the manifest already committed this exact file (an idempotent replay)."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    _fsync_file(path)


def estimate_bytes(rows: Sequence[Mapping[str, object]]) -> int:
    """An upper-bound size estimate: the rows as JSON (columnar zstd files are far smaller)."""
    return sum(len(json.dumps(list(r.values()), default=str)) for r in rows) + 4096


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _no_stage(stage: str) -> None:
    del stage


@contextmanager
def lake_lock(root: StorageRoot) -> Generator[None]:
    """Exclusive, process-wide lock over manifest reads-then-appends (advisory ``flock``)."""
    path = root.layer_path(Layer.MANIFESTS, ".lake.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class LakeWriter:
    def __init__(
        self,
        root: StorageRoot,
        *,
        codec: Codec,
        guard: DiskGuard,
        clock: Callable[[], datetime] = _utcnow,
        on_stage: Callable[[str], None] | None = None,
    ) -> None:
        self._root = root
        self._codec = codec
        self._guard = guard
        self._clock = clock
        self._on_stage: Callable[[str], None] = on_stage or _no_stage

    @property
    def root(self) -> StorageRoot:
        return self._root

    @property
    def codec(self) -> Codec:
        return self._codec

    def write(
        self,
        schema: TableSchema,
        partition: Partition,
        rows: Sequence[Mapping[str, object]],
        *,
        lineage: Lineage,
        supersedes: Sequence[str] = (),
    ) -> WriteResult:
        if partition.dataset != schema.dataset:
            raise ValueError(
                f"partition dataset {partition.dataset!r} does not match schema {schema.dataset!r}"
            )
        if not rows:
            raise ValueError("no rows to write; an empty batch creates no file")
        ordered = schema.sort_rows(schema.validate_rows(rows))
        self._root.require_exists()
        self._guard.require(estimate_bytes(ordered))

        tmp = self._root.layer_path(Layer.TMP, "lake", f"{uuid.uuid4().hex}.{self._codec.suffix}")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._codec.encode(schema, ordered, tmp)
            _fsync_file(tmp)
            self._on_stage("encoded")
            sha = file_sha256(tmp)
            rel = partition.relative_dir() / f"part-{sha[:32]}.{self._codec.suffix}"
            target = self._root.path(rel.as_posix())
            with lake_lock(self._root):
                return self._commit(
                    schema,
                    partition,
                    ordered,
                    tmp,
                    target,
                    rel.as_posix(),
                    sha,
                    lineage,
                    tuple(supersedes),
                )
        finally:
            if tmp.exists():
                tmp.unlink()  # only ever our own in-flight temp file

    def _commit(
        self,
        schema: TableSchema,
        partition: Partition,
        ordered: Sequence[Mapping[str, object]],
        tmp: Path,
        target: Path,
        rel: str,
        sha: str,
        lineage: Lineage,
        supersedes: tuple[str, ...],
    ) -> WriteResult:
        state = read_manifest(self._root, partition.layer, partition.table)
        live = {e.path: e for e in committed_entries(state.entries)}
        known = {e.path: e for e in state.entries}

        existing = known.get(rel)
        if existing is not None:
            if not target.is_file() or file_sha256(target) != existing.sha256:
                raise StorageIntegrityError(
                    f"{rel} does not match the manifest (missing or altered); run verify"
                )
            return WriteResult(existing, created=False)

        for path in supersedes:
            old = live.get(path)
            if old is None or old.partition != partition.key:
                raise StorageIntegrityError(
                    f"cannot supersede {path}: not a committed file of this partition"
                )

        if target.exists():
            if file_sha256(target) != sha:
                raise StorageIntegrityError(
                    f"refusing to overwrite {rel}: an uncommitted file with other content is there"
                )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, target)
            _fsync_dir(target.parent)
        self._on_stage("placed")

        entry = ManifestEntry(
            path=rel,
            layer=partition.layer.value,
            table=partition.table,
            partition=partition.key,
            sha256=sha,
            size_bytes=target.stat().st_size,
            row_count=len(ordered),
            schema_version=schema.version,
            schema_fingerprint=schema.fingerprint,
            codec=self._codec.name,
            key_ranges=schema.key_ranges(ordered),
            created_at=self._clock().isoformat(),
            lineage=lineage,
            supersedes=supersedes,
            writer=WRITER_ID,
        )
        append_entry(self._root, entry)
        self._on_stage("committed")
        return WriteResult(entry, created=True)
