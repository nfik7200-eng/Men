"""Append-only JSON-lines manifests: the commit log of the lake.

One manifest per layer and table: ``manifests/<layer>/<chain>.<dataset>.jsonl``. Each line is
one :class:`ManifestEntry` plus ``entry_sha256``, a checksum over the line's other fields.
A line that does not parse or fails its checksum (a torn write, tampering) is reported as
corrupt and never treated as committed. Appends start on a fresh line and are fsynced, so a
crash can lose at most the line being written.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path, PurePosixPath
from typing import Any

from bip.core.exceptions import StorageIntegrityError
from bip.storage.paths import Layer, StorageRoot

MANIFEST_FORMAT = 1
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}\.[a-z][a-z0-9_]{0,62}$")


@dataclass(frozen=True, slots=True)
class Lineage:
    job: str
    """Producing job and version, for example ``compaction/1``."""
    inputs: tuple[str, ...] = ()
    """Content hashes of input lake files, or external references for Bronze."""


def _check_rel_path(path: str) -> None:
    pure = PurePosixPath(path)
    if not path or pure.is_absolute() or ".." in pure.parts or "\\" in path or "\x00" in path:
        raise ValueError("manifest path must be a relative POSIX path inside the storage root")


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    path: str
    """Data file, relative to the storage root (POSIX separators)."""
    layer: str
    table: str
    partition: str
    sha256: str
    size_bytes: int
    row_count: int
    schema_version: int
    schema_fingerprint: str
    codec: str
    key_ranges: dict[str, list[Any]]
    created_at: str
    lineage: Lineage
    supersedes: tuple[str, ...] = ()
    writer: str = field(default="bip-lake/1")

    def __post_init__(self) -> None:
        _check_rel_path(self.path)
        for p in self.supersedes:
            _check_rel_path(p)
        Layer(self.layer)
        if not _TABLE_RE.match(self.table):
            raise ValueError("table must be <chain>.<dataset>")
        if not _SHA_RE.match(self.sha256) or not _SHA_RE.match(self.schema_fingerprint):
            raise ValueError("sha256 and schema_fingerprint must be 64 lower-case hex digits")
        if self.size_bytes < 0 or self.row_count < 0 or self.schema_version < 1:
            raise ValueError("sizes and counts must be non-negative and schema_version >= 1")

    def to_dict(self) -> dict[str, Any]:
        """Constructor keyword arguments (``lineage`` stays a :class:`Lineage`)."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def _body(self) -> dict[str, Any]:
        body = self.to_dict()
        body["lineage"] = {"job": self.lineage.job, "inputs": list(self.lineage.inputs)}
        body["supersedes"] = list(self.supersedes)
        body["format"] = MANIFEST_FORMAT
        return body

    def to_line(self) -> str:
        body = self._body()
        body["entry_sha256"] = _checksum(body)
        return json.dumps(body, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_line(cls, line: str) -> ManifestEntry:
        """Parse one line; raise ``ValueError`` with a reason if it is not a valid entry."""
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError("not valid JSON (torn write?)") from None
        if not isinstance(doc, dict):
            raise ValueError("not a JSON object")
        body: dict[str, Any] = dict(doc)  # pyright: ignore[reportUnknownArgumentType]
        claimed = body.pop("entry_sha256", None)
        if claimed != _checksum(body):
            raise ValueError("entry checksum mismatch")
        if body.pop("format", None) != MANIFEST_FORMAT:
            raise ValueError("unsupported manifest format")
        try:
            lineage_doc = body.pop("lineage")
            lineage = Lineage(job=str(lineage_doc["job"]), inputs=tuple(lineage_doc["inputs"]))
            body["supersedes"] = tuple(body.get("supersedes", ()))
            return cls(lineage=lineage, **body)
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed entry: {type(exc).__name__}") from None


def _checksum(body: dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CorruptLine:
    line: int
    """1-based line number in the manifest file."""
    reason: str


@dataclass(frozen=True, slots=True)
class ManifestState:
    entries: tuple[ManifestEntry, ...]
    corrupt: tuple[CorruptLine, ...]


def manifest_path(root: StorageRoot, layer: Layer, table: str) -> Path:
    if not _TABLE_RE.match(table):
        raise ValueError("table must be <chain>.<dataset>")
    return root.layer_path(Layer.MANIFESTS, layer.value, f"{table}.jsonl")


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def append_entry(root: StorageRoot, entry: ManifestEntry) -> None:
    """Durably append ``entry``. Callers serialise appends (the writer holds the lake lock)."""
    if not entry.path.startswith(f"{entry.layer}/"):
        raise StorageIntegrityError("entry path is not inside its layer")
    chain, dataset = entry.table.split(".", 1)
    if not entry.path.startswith(f"{entry.layer}/chain={chain}/dataset={dataset}/"):
        raise StorageIntegrityError("entry path does not belong to its table")
    root.require_exists()
    path = manifest_path(root, Layer(entry.layer), entry.table)
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    with path.open("ab") as fh:
        if fh.tell() > 0:
            with path.open("rb") as check:
                check.seek(-1, os.SEEK_END)
                if check.read(1) != b"\n":
                    fh.write(b"\n")  # close a torn line so it stays a separate, corrupt line
        fh.write(entry.to_line().encode() + b"\n")
        fh.flush()
        os.fsync(fh.fileno())
    if created:
        _fsync_dir(path.parent)


def read_manifest(root: StorageRoot, layer: Layer, table: str) -> ManifestState:
    path = manifest_path(root, layer, table)
    if not path.exists():
        return ManifestState((), ())
    entries: list[ManifestEntry] = []
    corrupt: list[CorruptLine] = []
    with path.open("rb") as fh:
        for number, raw in enumerate(fh, start=1):
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                entry = ManifestEntry.from_line(text)
            except ValueError as exc:
                corrupt.append(CorruptLine(number, str(exc)))
                continue
            if entry.layer != layer.value or entry.table != table:
                corrupt.append(CorruptLine(number, "entry belongs to another table"))
                continue
            entries.append(entry)
    return ManifestState(tuple(entries), tuple(corrupt))


def committed_entries(
    entries: Sequence[ManifestEntry], *, partition: str | None = None
) -> tuple[ManifestEntry, ...]:
    """The live set: one entry per path, minus every superseded path, optionally one partition.

    Two entries for the same path with different content are an integrity error, because a
    content-addressed path can only ever hold one file.
    """
    by_path: dict[str, ManifestEntry] = {}
    superseded: set[str] = set()
    for entry in entries:
        seen = by_path.get(entry.path)
        if seen is None:
            by_path[entry.path] = entry
        elif (seen.sha256, seen.row_count) != (entry.sha256, entry.row_count):
            raise StorageIntegrityError(f"conflicting manifest entries for {entry.path}")
        superseded.update(entry.supersedes)
    return tuple(
        e
        for e in by_path.values()
        if e.path not in superseded and (partition is None or e.partition == partition)
    )
