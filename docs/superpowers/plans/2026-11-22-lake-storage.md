# Lake Storage Layer (Roadmap Stage 5) Implementation Plan

**Goal:** an atomic, content-addressed, manifest-committed file lake under the existing `StorageRoot`, with integrity checks and compaction that never deletes. Roadmap Stage 5 acceptance: crash-safety test (kill between write and commit) and manifest integrity.

**Owner decision used:** pyarrow and duckdb approved (2026-11-22). Installing them was refused by the session's permission classifier, so the work is split:
- **Part A (this pass, stdlib only):** schemas and row validation, partition layout, manifest, writer commit protocol, integrity check, compaction, CLI. Encoding goes through a `Codec` protocol; tests use a JSON-lines test codec.
- **Part B (after the install is approved):** `ParquetCodec` (pyarrow, zstd level 3), DuckDB reader over committed files only, bytes-per-row measurement.

## Global constraints
- No file is ever deleted or overwritten, except the writer's own in-flight temp file on failure.
- The manifest line is the commit point. A data file without a manifest entry is uncommitted and never read.
- Missing values are `NULL`, never zero; no silent type coercion (§2.8).
- Every write passes the `DiskGuard` first, using a conservative size estimate.
- Nothing is wired into ingestion yet (roadmap Stage 6).

## Tasks (Part A)
1. `bip/storage/lake/schema.py`: `ColumnType`, `Column`, `TableSchema` (fingerprint, strict `validate_rows`, `sort_rows`, `key_ranges`). Tests first.
2. `bip/storage/lake/partition.py`: `Partition` (layer/chain/dataset/k=v path), `block_range_part`, `date_part`. Tests first.
3. `bip/storage/lake/manifest.py`: `ManifestEntry` with a per-line checksum, torn-line-safe append with fsync, `read_manifest`, `committed_entries` (dedupe, supersession). Tests first.
4. `bip/storage/lake/writer.py`: `Codec` protocol, `LakeWriter.write` (validate, sort, guard, encode to temp, fsync, hash, place without overwrite under a lake lock, commit). Idempotent rewrite. Real-process crash tests (`os._exit` at `encoded` and `placed`).
5. `bip/storage/lake/integrity.py` and `compaction.py`: verify (missing, mismatched, unreferenced, corrupt lines, dangling supersedes, stale temp), table stats (bytes per row), compaction (schema and row-count checks, superseded inputs kept).
6. `python -m bip.tools.lake verify|stats`; ADR-055 (Proposed); state docs; checks; commit and push.
