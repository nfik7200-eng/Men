"""BIP-owned ingestion checkpoints and coverage ranges (schema v16).

A checkpoint is BIP's progress for one ``(chain, network, stream, direction)``. It moves only after
the batch it covers was committed, only in its own direction, and never because a provider failed:
a failure changes ``status`` and ``last_error_category``, not ``cursor``. Providers are recorded as
provenance and are never part of the identity. ``cursor_hash`` always belongs to ``cursor``.

Coverage ranges say what BIP knows about each block or slot range. Ranges with the same meaning
that touch or overlap are merged, so a long run of processed blocks is one row and a rescan adds
nothing. Anything without a row is *not observed*; anything beyond the live cursor is *not yet
processed*. Neither is ever reported as empty: only ``confirmed_empty`` (the chain itself has no
block there) means empty. A gap that a later read fully processes is marked resolved, not deleted.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.engine import Connection, RowMapping

from bip.contracts.blockchain import KNOWN_CHAINS
from bip.db import schema
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.security.secrets import redact_text

Direction = Literal["live", "historical"]


class CoverageStatus(StrEnum):
    PROCESSED = "processed"
    CONFIRMED_EMPTY = "confirmed_empty"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    SKIPPED = "skipped"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class CheckpointStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    """A run is in progress (set at its start; every exit path replaces it)."""
    STALLED = "stalled"
    """The last run stopped on a provider or read failure; the cursor kept its place."""
    BLOCKED = "blocked"
    """No eligible provider (or no finalized data) was available at all."""
    COMPLETE = "complete"
    """A historical checkpoint that reached its boundary."""
    PAUSED = "paused"
    """An operator paused this stream and direction; workers skip it until it is resumed."""


GAP_STATUSES = frozenset(
    {
        CoverageStatus.INCOMPLETE,
        CoverageStatus.FAILED,
        CoverageStatus.SKIPPED,
        CoverageStatus.PROVIDER_UNAVAILABLE,
    }
)
"""Statuses that mean BIP knowingly does not hold complete data for the range."""

RESOLVABLE = frozenset(
    {CoverageStatus.FAILED, CoverageStatus.SKIPPED, CoverageStatus.PROVIDER_UNAVAILABLE}
)
"""Gaps that a later successful read of the whole range resolves. ``incomplete`` is not one: it
qualifies a processed read of the same positions (read, but not everything stored)."""


@dataclass(frozen=True, slots=True)
class StreamRef:
    """One ingestion stream of one chain network. ``network`` and the position kind come from the
    chain registry so Ethereum blocks and Solana slots keep their own meaning."""

    chain: str
    stream: str

    @property
    def network(self) -> str:
        return KNOWN_CHAINS[self.chain].network

    @property
    def position_kind(self) -> Literal["block", "slot"]:
        return KNOWN_CHAINS[self.chain].position_kind


@dataclass(frozen=True, slots=True)
class Checkpoint:
    chain: str
    network: str
    stream: str
    direction: Direction
    position_kind: str
    cursor: int | None
    cursor_hash: str | None
    boundary: int | None
    head_seen: int | None
    head_seen_at: datetime | None
    last_batch_from: int | None
    last_batch_to: int | None
    last_committed_at: datetime | None
    status: str
    last_error_category: str | None
    last_error_at: datetime | None
    last_providers: list[str]
    decoder_version: str
    config_version: str | None
    recovery: dict[str, Any]
    updated_at: datetime

    @property
    def lag(self) -> int | None:
        """Positions between the cursor and the head the last run saw (live only)."""
        if self.direction != "live" or self.head_seen is None or self.cursor is None:
            return None
        return max(0, self.head_seen - self.cursor)


def _checkpoint(row: RowMapping) -> Checkpoint:
    return Checkpoint(
        chain=row["chain"],
        network=row["network"],
        stream=row["stream"],
        direction=row["direction"],
        position_kind=row["position_kind"],
        cursor=row["cursor"],
        cursor_hash=row["cursor_hash"],
        boundary=row["boundary"],
        head_seen=row["head_seen"],
        head_seen_at=row["head_seen_at"],
        last_batch_from=row["last_batch_from"],
        last_batch_to=row["last_batch_to"],
        last_committed_at=row["last_committed_at"],
        status=row["status"],
        last_error_category=row["last_error_category"],
        last_error_at=row["last_error_at"],
        last_providers=list(json.loads(row["last_providers_json"] or "[]")),
        decoder_version=row["decoder_version"],
        config_version=row["config_version"],
        recovery=dict(json.loads(row["recovery_json"] or "{}")),
        updated_at=row["updated_at"],
    )


class CheckpointStore:
    """Reads and writes ``ingestion_checkpoints`` and ``coverage_ranges``.

    Write methods take an optional ``conn`` so a caller can put the checkpoint move in the same
    transaction as the data it covers.
    """

    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------ checkpoints

    def get(
        self, ref: StreamRef, direction: Direction, *, conn: Connection | None = None
    ) -> Checkpoint | None:
        if conn is not None:
            return self._get(conn, ref, direction)
        with self._repo.transaction() as c:
            return self._get(c, ref, direction)

    def all(self, chain: str | None = None) -> list[Checkpoint]:
        t = schema.ingestion_checkpoints
        q = select(t).order_by(t.c.chain, t.c.stream, t.c.direction)
        if chain is not None:
            q = q.where(t.c.chain == chain)
        with self._repo.transaction() as conn:
            return [_checkpoint(r) for r in conn.execute(q).mappings()]

    def commit_batch(
        self,
        ref: StreamRef,
        direction: Direction,
        *,
        from_pos: int,
        to_pos: int,
        decoder_version: str,
        cursor_hash: str | None = None,
        providers: Iterable[str] = (),
        conn: Connection | None = None,
    ) -> Checkpoint:
        """Record that ``from_pos..to_pos`` was committed and move the cursor over it.

        Call this only after the batch's data is committed (or inside the same transaction). The
        cursor moves in its direction only: a ``live`` cursor never goes backward and a
        ``historical`` cursor never goes forward, so a rescan cannot lose progress. When it moves,
        ``cursor_hash`` becomes the hash of the new cursor position, or ``NULL`` if that position
        has none (a given-up block, a skipped slot): never the hash of another position.
        """
        if to_pos < from_pos:
            raise ValueError("to_pos must be >= from_pos")
        used = sorted(set(providers))
        if conn is not None:
            return self._commit(
                conn, ref, direction, from_pos, to_pos, decoder_version, cursor_hash, used
            )
        with self._repo.transaction() as c:
            return self._commit(
                c, ref, direction, from_pos, to_pos, decoder_version, cursor_hash, used
            )

    def skip_to(
        self,
        ref: StreamRef,
        position: int,
        *,
        decoder_version: str,
        reason: str,
        conn: Connection | None = None,
    ) -> Checkpoint:
        """Move a live cursor past positions that were deliberately *not* read, recording them as
        ``skipped`` in the same transaction. Batch fields are left alone: nothing was committed."""
        if conn is not None:
            return self._skip(conn, ref, position, decoder_version, reason)
        with self._repo.transaction() as c:
            return self._skip(c, ref, position, decoder_version, reason)

    def set_boundary(self, ref: StreamRef, boundary: int, *, decoder_version: str) -> Checkpoint:
        """Set where a historical checkpoint stops (it becomes ``complete`` on reaching it)."""
        now = utcnow()
        with self._repo.transaction() as conn:
            if self._get(conn, ref, "historical") is None:
                self._insert(conn, ref, "historical", decoder_version, now)
            conn.execute(
                update(schema.ingestion_checkpoints)
                .where(*self._key(ref, "historical"))
                .values(boundary=boundary, updated_at=now)
            )
            result = self._get(conn, ref, "historical")
            assert result is not None  # noqa: S101
            return result

    def pause(self, ref: StreamRef, direction: Direction, *, decoder_version: str) -> Checkpoint:
        """Pause one stream direction. Its cursor and history stay as they are; the other
        direction keeps running."""
        return self.mark(ref, direction, CheckpointStatus.PAUSED, decoder_version=decoder_version)

    def resume(self, ref: StreamRef, direction: Direction, *, decoder_version: str) -> Checkpoint:
        now = utcnow()
        with self._repo.transaction() as conn:
            if self._get(conn, ref, direction) is None:
                self._insert(conn, ref, direction, decoder_version, now)
            conn.execute(
                update(schema.ingestion_checkpoints)
                .where(*self._key(ref, direction))
                .values(status=CheckpointStatus.IDLE.value, updated_at=now)
            )
            result = self._get(conn, ref, direction)
            assert result is not None  # noqa: S101
            return result

    def set_recovery(self, ref: StreamRef, direction: Direction, key: str, value: Any) -> None:
        """Store worker recovery state (for example per-position attempt counts) on the
        checkpoint itself, so it survives restarts and never lives in a shared key."""
        with self._repo.transaction() as conn:
            current = self._get(conn, ref, direction)
            if current is None:
                return
            recovery = dict(current.recovery)
            recovery[key] = value
            conn.execute(
                update(schema.ingestion_checkpoints)
                .where(*self._key(ref, direction))
                .values(recovery_json=json.dumps(recovery))
            )

    def covered(self, ref: StreamRef, position: int) -> bool:
        """Whether a ``processed`` or ``confirmed_empty`` range already holds ``position``."""
        t = schema.coverage_ranges
        with self._repo.transaction() as conn:
            row = conn.execute(
                select(t.c.id).where(
                    t.c.chain == ref.chain,
                    t.c.network == ref.network,
                    t.c.stream == ref.stream,
                    t.c.status.in_(
                        [CoverageStatus.PROCESSED.value, CoverageStatus.CONFIRMED_EMPTY.value]
                    ),
                    t.c.from_pos <= position,
                    t.c.to_pos >= position,
                )
            ).first()
        return row is not None

    def lowest_processed(self, ref: StreamRef) -> int | None:
        t = schema.coverage_ranges
        with self._repo.transaction() as conn:
            value: int | None = conn.execute(
                select(func.min(t.c.from_pos)).where(
                    t.c.chain == ref.chain,
                    t.c.network == ref.network,
                    t.c.stream == ref.stream,
                    t.c.status.in_(
                        [CoverageStatus.PROCESSED.value, CoverageStatus.CONFIRMED_EMPTY.value]
                    ),
                )
            ).scalar_one_or_none()
            return value

    def is_paused(self, ref: StreamRef, direction: Direction) -> bool:
        cp = self.get(ref, direction)
        return cp is not None and cp.status == CheckpointStatus.PAUSED.value

    def start_run(self, ref: StreamRef, direction: Direction, *, decoder_version: str) -> None:
        """Mark a run in progress. :meth:`finish_run` must follow on every exit path."""
        self.mark(ref, direction, CheckpointStatus.RUNNING, decoder_version=decoder_version)

    def finish_run(
        self,
        ref: StreamRef,
        direction: Direction,
        *,
        head_seen: int | None = None,
    ) -> None:
        """End a run: record the head it saw, and turn a still-``running`` status into ``idle``
        (a failure already set ``stalled`` or ``blocked``, which is kept)."""
        now = utcnow()
        t = schema.ingestion_checkpoints
        with self._repo.transaction() as conn:
            values: dict[str, Any] = {"updated_at": now}
            if head_seen is not None:
                values.update(head_seen=head_seen, head_seen_at=now)
            conn.execute(update(t).where(*self._key(ref, direction)).values(**values))
            conn.execute(
                update(t)
                .where(*self._key(ref, direction), t.c.status == CheckpointStatus.RUNNING.value)
                .values(status=CheckpointStatus.IDLE.value)
            )

    def mark(
        self,
        ref: StreamRef,
        direction: Direction,
        status: CheckpointStatus,
        *,
        decoder_version: str,
        error_category: str | None = None,
        detail: str | None = None,
        providers: Iterable[str] = (),
    ) -> Checkpoint:
        """Change a checkpoint's status without touching its cursor (a failure never resets it)."""
        now = utcnow()
        with self._repo.transaction() as conn:
            current = self._get(conn, ref, direction)
            if current is None:
                self._insert(conn, ref, direction, decoder_version, now)
                current = self._get(conn, ref, direction)
                assert current is not None  # noqa: S101 - just inserted
            paused = current.status == CheckpointStatus.PAUSED.value
            # a pause set while a run was in flight survives that run's status updates
            keep = paused and status is not CheckpointStatus.PAUSED
            values: dict[str, Any] = {
                "status": current.status if keep else status.value,
                "updated_at": now,
            }
            if error_category is not None:
                recovery = dict(current.recovery)
                recovery["last_error"] = redact_text(detail or error_category)[:300]
                values.update(
                    last_error_category=error_category[:32],
                    last_error_at=now,
                    recovery_json=json.dumps(recovery),
                )
            used = sorted(set(providers))
            if used:
                values["last_providers_json"] = json.dumps(used)
            conn.execute(
                update(schema.ingestion_checkpoints)
                .where(*self._key(ref, direction))
                .values(**values)
            )
            result = self._get(conn, ref, direction)
            assert result is not None  # noqa: S101
            return result

    # ------------------------------------------------------------------ coverage

    def record_range(
        self,
        ref: StreamRef,
        from_pos: int,
        to_pos: int,
        status: CoverageStatus,
        *,
        decoder_version: str,
        provider_id: str | None = None,
        reason: str | None = None,
        attempts: int = 0,
        conn: Connection | None = None,
    ) -> None:
        """Record what BIP knows about ``from_pos..to_pos``.

        Rows with the same status, provider, decoder version and reason that touch or overlap the
        new range are merged into one, so recording a range again (a rescan) changes nothing but
        the timestamp. A ``processed`` range resolves the failed, skipped or provider-unavailable
        gaps it fully covers.
        """
        if to_pos < from_pos:
            raise ValueError("to_pos must be >= from_pos")
        clean = None if reason is None else redact_text(reason)[:300]
        if conn is not None:
            self._record(
                conn, ref, from_pos, to_pos, status, decoder_version, provider_id, clean, attempts
            )
            return
        with self._repo.transaction() as c:
            self._record(
                c, ref, from_pos, to_pos, status, decoder_version, provider_id, clean, attempts
            )

    def ranges(
        self,
        chain: str,
        *,
        stream: str | None = None,
        statuses: Iterable[CoverageStatus] | None = None,
        include_resolved: bool = True,
        exclude_reason: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        t = schema.coverage_ranges
        q = select(t).where(t.c.chain == chain)
        if stream is not None:
            q = q.where(t.c.stream == stream)
        if statuses is not None:
            q = q.where(t.c.status.in_([s.value for s in statuses]))
        if not include_resolved:
            q = q.where(t.c.resolved_at.is_(None))
        if exclude_reason is not None:
            q = q.where(t.c.reason.is_(None) | (t.c.reason != exclude_reason))
        q = q.order_by(t.c.from_pos.desc(), t.c.id.desc()).limit(limit)
        with self._repo.transaction() as conn:
            return [
                {
                    "chain": r["chain"],
                    "network": r["network"],
                    "stream": r["stream"],
                    "position_kind": r["position_kind"],
                    "from_pos": r["from_pos"],
                    "to_pos": r["to_pos"],
                    "status": r["status"],
                    "provider_id": r["provider_id"],
                    "decoder_version": r["decoder_version"],
                    "attempts": r["attempts"],
                    "reason": r["reason"],
                    "first_recorded_at": r["first_recorded_at"],
                    "last_recorded_at": r["last_recorded_at"],
                    "resolved_at": r["resolved_at"],
                }
                for r in conn.execute(q).mappings()
            ]

    def summary(self, chain: str, stream: str) -> dict[str, Any]:
        """Per-status position counts and bounds over unresolved rows.

        Within one status rows never overlap (they are merged), so counts are exact per status.
        A block can appear under two statuses (``processed`` and ``incomplete``), so the
        statuses are not meant to be added up. Resolved gaps are counted separately.
        """
        t = schema.coverage_ranges
        units = func.sum(t.c.to_pos - t.c.from_pos + 1)
        where = (t.c.chain == chain, t.c.stream == stream)
        q = (
            select(t.c.status, units, func.min(t.c.from_pos), func.max(t.c.to_pos), func.count())
            .where(*where, t.c.resolved_at.is_(None))
            .group_by(t.c.status)
        )
        with self._repo.transaction() as conn:
            rows = conn.execute(q).all()
            resolved = conn.execute(
                select(func.count()).where(*where, t.c.resolved_at.is_not(None))
            ).scalar_one()
            providers = sorted(
                str(p)
                for (p,) in conn.execute(
                    select(t.c.provider_id).distinct().where(*where, t.c.provider_id.is_not(None))
                )
            )
            decoders = sorted(
                str(d)
                for (d,) in conn.execute(select(t.c.decoder_version).distinct().where(*where))
            )
        by_status = {
            str(status): {"positions": int(n or 0), "lowest": lo, "highest": hi, "ranges": int(c)}
            for status, n, lo, hi, c in rows
        }
        return {
            "by_status": by_status,
            "resolved_gap_ranges": int(resolved),
            "providers": providers,
            "decoder_versions": decoders,
            "lowest_observed": min((v["lowest"] for v in by_status.values()), default=None),
            "highest_observed": max((v["highest"] for v in by_status.values()), default=None),
        }

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _key(ref: StreamRef, direction: Direction) -> tuple[Any, ...]:
        t = schema.ingestion_checkpoints
        return (
            t.c.chain == ref.chain,
            t.c.network == ref.network,
            t.c.stream == ref.stream,
            t.c.direction == direction,
        )

    def _get(self, conn: Connection, ref: StreamRef, direction: Direction) -> Checkpoint | None:
        row = (
            conn.execute(select(schema.ingestion_checkpoints).where(*self._key(ref, direction)))
            .mappings()
            .first()
        )
        return None if row is None else _checkpoint(row)

    def _insert(
        self, conn: Connection, ref: StreamRef, direction: Direction, decoder: str, now: datetime
    ) -> None:
        conn.execute(
            insert(schema.ingestion_checkpoints).values(
                chain=ref.chain,
                network=ref.network,
                stream=ref.stream,
                direction=direction,
                position_kind=ref.position_kind,
                status=CheckpointStatus.IDLE.value,
                decoder_version=decoder,
                updated_at=now,
            )
        )

    def _commit(
        self,
        conn: Connection,
        ref: StreamRef,
        direction: Direction,
        from_pos: int,
        to_pos: int,
        decoder: str,
        cursor_hash: str | None,
        providers: list[str],
    ) -> Checkpoint:
        now = utcnow()
        current = self._get(conn, ref, direction)
        if current is None:
            self._insert(conn, ref, direction, decoder, now)
        old = None if current is None else current.cursor
        if direction == "live":
            moves = old is None or to_pos > old
            new_cursor = to_pos if moves else old
        else:
            moves = old is None or from_pos < old
            new_cursor = from_pos if moves else old
        running = current is not None and current.status == CheckpointStatus.RUNNING.value
        paused = current is not None and current.status == CheckpointStatus.PAUSED.value
        values: dict[str, Any] = {
            "last_batch_from": from_pos,
            "last_batch_to": to_pos,
            "last_committed_at": now,
            # a batch inside a run keeps the run visible; finish_run makes it idle
            "status": (
                CheckpointStatus.PAUSED
                if paused
                else CheckpointStatus.RUNNING
                if running
                else CheckpointStatus.IDLE
            ).value,
            "decoder_version": decoder,
            "updated_at": now,
        }
        if moves:
            values.update(cursor=new_cursor, cursor_hash=cursor_hash)
        if providers:
            values["last_providers_json"] = json.dumps(providers)
        boundary = None if current is None else current.boundary
        if (
            direction == "historical"
            and boundary is not None
            and new_cursor is not None
            and new_cursor <= boundary
        ):
            values["status"] = CheckpointStatus.COMPLETE.value
        conn.execute(
            update(schema.ingestion_checkpoints).where(*self._key(ref, direction)).values(**values)
        )
        result = self._get(conn, ref, direction)
        assert result is not None  # noqa: S101
        return result

    def _skip(
        self, conn: Connection, ref: StreamRef, position: int, decoder: str, reason: str
    ) -> Checkpoint:
        now = utcnow()
        current = self._get(conn, ref, "live")
        if current is None:
            self._insert(conn, ref, "live", decoder, now)
        old = None if current is None else current.cursor
        if old is None or position > old:
            first = position if old is None else old + 1
            self._record(
                conn,
                ref,
                first,
                position,
                CoverageStatus.SKIPPED,
                decoder,
                None,
                redact_text(reason)[:300],
                0,
            )
            conn.execute(
                update(schema.ingestion_checkpoints)
                .where(*self._key(ref, "live"))
                .values(cursor=position, cursor_hash=None, updated_at=now)
            )
        result = self._get(conn, ref, "live")
        assert result is not None  # noqa: S101
        return result

    def _record(
        self,
        conn: Connection,
        ref: StreamRef,
        from_pos: int,
        to_pos: int,
        status: CoverageStatus,
        decoder: str,
        provider_id: str | None,
        reason: str | None,
        attempts: int,
    ) -> None:
        t = schema.coverage_ranges
        now = utcnow()
        same = and_(
            t.c.chain == ref.chain,
            t.c.network == ref.network,
            t.c.stream == ref.stream,
            t.c.status == status.value,
            t.c.decoder_version == decoder,
            t.c.provider_id.is_(None) if provider_id is None else t.c.provider_id == provider_id,
            t.c.reason.is_(None) if reason is None else t.c.reason == reason,
            t.c.resolved_at.is_(None),
        )
        # every row with the same meaning that touches or overlaps the new range
        touching = conn.execute(
            select(t.c.id, t.c.from_pos, t.c.to_pos, t.c.attempts, t.c.first_recorded_at)
            .where(same, t.c.from_pos <= to_pos + 1, t.c.to_pos >= from_pos - 1)
            .order_by(t.c.from_pos)
        ).all()
        if touching:
            keep = touching[0]
            lo = min(from_pos, *(r.from_pos for r in touching))
            hi = max(to_pos, *(r.to_pos for r in touching))
            most = max(attempts, *(r.attempts for r in touching))
            first = min(r.first_recorded_at for r in touching)
            conn.execute(
                update(t)
                .where(t.c.id == keep.id)
                .values(
                    from_pos=lo,
                    to_pos=hi,
                    attempts=most,
                    first_recorded_at=first,
                    last_recorded_at=now,
                )
            )
            others = [r.id for r in touching[1:]]
            if others:
                conn.execute(delete(t).where(t.c.id.in_(others)))
        else:
            lo, hi = from_pos, to_pos
            conn.execute(
                insert(t).values(
                    chain=ref.chain,
                    network=ref.network,
                    stream=ref.stream,
                    position_kind=ref.position_kind,
                    from_pos=lo,
                    to_pos=hi,
                    status=status.value,
                    provider_id=provider_id,
                    decoder_version=decoder,
                    attempts=attempts,
                    reason=reason,
                    first_recorded_at=now,
                    last_recorded_at=now,
                )
            )
        if status is CoverageStatus.PROCESSED:
            conn.execute(
                update(t)
                .where(
                    t.c.chain == ref.chain,
                    t.c.network == ref.network,
                    t.c.stream == ref.stream,
                    t.c.status.in_([s.value for s in RESOLVABLE]),
                    t.c.resolved_at.is_(None),
                    t.c.from_pos >= lo,
                    t.c.to_pos <= hi,
                )
                .values(resolved_at=now)
            )
