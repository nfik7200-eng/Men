"""Export stored transactions and transfers into Silver lake partitions (bounded, resumable).

Each run reads up to ``batch_rows`` rows per source table past an id cursor kept in
``app_metadata`` (``lake:export:<dataset>:last_id``), groups them by chain and UTC day
(``silver/chain=<c>/dataset=<d>/date=<YYYY-MM-DD|undated>``), writes each group through the
crash-safe :class:`LakeWriter`, and only then moves the cursor. A crash before the cursor moves
replays the same batch: the files come out identical and are adopted, not duplicated.

Limits (stated, not hidden):
- rows are exported as stored at export time; later in-place changes in the database (a finality
  upgrade, an orphan mark) are not re-exported, so readers must filter on the exported
  ``finality``;
- replaying after a crash is idempotent with the same ``batch_rows``; a different batch size can
  produce overlapping files, which Silver consumers de-duplicate on natural keys.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from bip.db import schema as db
from bip.db.repository import Repository
from bip.storage.disk_guard import DiskGuard
from bip.storage.lake.datasets import TRANSACTIONS, TRANSFERS
from bip.storage.lake.manifest import Lineage
from bip.storage.lake.partition import Partition
from bip.storage.lake.schema import TableSchema
from bip.storage.paths import Layer, StorageRoot

EXPORT_JOB = "lake_export/1"
_SOURCES = ((TRANSACTIONS, db.transactions), (TRANSFERS, db.transfers))
_LOCK = threading.Lock()


class LakeExportBusyError(RuntimeError):
    pass


def cursor_key(dataset: str) -> str:
    return f"lake:export:{dataset}:last_id"


def _aware(value: object) -> object:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _day(row: dict[str, object]) -> str:
    when = row.get("block_time")
    return when.astimezone(UTC).date().isoformat() if isinstance(when, datetime) else "undated"


class LakeExporter:
    def __init__(
        self, repo: Repository, root: StorageRoot, guard: DiskGuard, *, batch_rows: int = 5000
    ) -> None:
        from bip.storage.lake.parquet import ParquetCodec  # needs the optional lake runtime
        from bip.storage.lake.writer import LakeWriter

        if not 1 <= batch_rows <= 100_000:
            raise ValueError("batch_rows must be 1..100000")
        self._repo = repo
        self._root = root
        self._writer = LakeWriter(root, codec=ParquetCodec(), guard=guard)
        self._batch = batch_rows

    def cursors(self) -> dict[str, int]:
        return {
            s.dataset: int(self._repo.get_meta(cursor_key(s.dataset)) or 0) for s, _ in _SOURCES
        }

    def run(self) -> dict[str, Any]:
        if not _LOCK.acquire(blocking=False):
            raise LakeExportBusyError("a lake export is already running")
        try:
            self._root.create()
            return {"datasets": [self._export(s, t) for s, t in _SOURCES]}
        finally:
            _LOCK.release()

    def _export(self, lake_schema: TableSchema, table: Any) -> dict[str, Any]:
        key = cursor_key(lake_schema.dataset)
        last = int(self._repo.get_meta(key) or 0)
        names = [c.name for c in lake_schema.columns]
        with self._repo.transaction() as conn:
            raw = conn.execute(
                select(table.c.id, *(table.c[n] for n in names))
                .where(table.c.id > last)
                .order_by(table.c.id)
                .limit(self._batch)
            ).all()
        if not raw:
            return {
                "dataset": lake_schema.dataset,
                "rows": 0,
                "files": 0,
                "files_created": 0,
                "more": False,
            }
        first_id, last_id = int(raw[0][0]), int(raw[-1][0])
        groups: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
        for r in raw:
            row = {n: _aware(v) for n, v in zip(names, r[1:], strict=True)}
            groups[(str(row["chain"]), _day(row))].append(row)
        created = 0
        lineage = Lineage(job=EXPORT_JOB, inputs=(f"sqlite:{table.name}:{first_id}-{last_id}",))
        for (chain, day), rows in sorted(groups.items()):
            partition = Partition(Layer.SILVER, chain, lake_schema.dataset, (("date", day),))
            result = self._writer.write(lake_schema, partition, rows, lineage=lineage)
            created += int(result.created)
        self._repo.set_meta(key, str(last_id))  # only after every file of the batch committed
        return {
            "dataset": lake_schema.dataset,
            "rows": len(raw),
            "files": len(groups),
            "files_created": created,
            "more": len(raw) == self._batch,
        }
