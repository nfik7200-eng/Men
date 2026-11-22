"""Compaction: merge the committed files of one partition into one file.

The merged file is committed with ``supersedes`` naming its inputs and ``lineage.inputs``
holding their hashes, in one manifest line, so readers switch atomically. Inputs are verified
(hash, schema fingerprint, row count) before and after decoding, and are never deleted:
removing superseded files is a separate retention decision (``docs/DATA_ARCHITECTURE.md`` §4.6).
Rows are not de-duplicated; Bronze keeps every provider's copy and Silver de-duplicates.
"""

from __future__ import annotations

from bip.core.exceptions import StorageIntegrityError
from bip.storage.lake.manifest import Lineage, committed_entries, read_manifest
from bip.storage.lake.partition import Partition
from bip.storage.lake.schema import TableSchema
from bip.storage.lake.writer import LakeWriter, WriteResult, file_sha256

COMPACTION_JOB = "compaction/1"


def compact_partition(
    writer: LakeWriter, schema: TableSchema, partition: Partition, *, min_files: int = 2
) -> WriteResult | None:
    """Merge the partition's live files; ``None`` when fewer than ``min_files`` exist."""
    if min_files < 2:
        raise ValueError("min_files must be >= 2")
    root = writer.root
    state = read_manifest(root, partition.layer, partition.table)
    inputs = committed_entries(state.entries, partition=partition.key)
    if len(inputs) < min_files:
        return None

    merged: list[dict[str, object]] = []
    for entry in inputs:
        if entry.schema_fingerprint != schema.fingerprint:
            raise StorageIntegrityError(
                f"compaction refused: {entry.path} was written with another schema version"
            )
        if entry.codec != writer.codec.name:
            raise StorageIntegrityError(
                f"compaction refused: {entry.path} uses codec {entry.codec}"
            )
        path = root.path(entry.path)
        if not path.is_file() or file_sha256(path) != entry.sha256:
            raise StorageIntegrityError(f"compaction refused: {entry.path} is missing or altered")
        try:
            decoded = writer.codec.decode(schema, path)
        except (ValueError, KeyError, TypeError) as exc:
            raise StorageIntegrityError(
                f"compaction refused: {entry.path} could not be decoded ({type(exc).__name__})"
            ) from None
        if len(decoded) != entry.row_count:
            raise StorageIntegrityError(
                f"compaction refused: {entry.path} holds {len(decoded)} rows, "
                f"the manifest says {entry.row_count}"
            )
        merged.extend(decoded)

    return writer.write(
        schema,
        partition,
        merged,
        lineage=Lineage(job=COMPACTION_JOB, inputs=tuple(e.sha256 for e in inputs)),
        supersedes=tuple(e.path for e in inputs),
    )
