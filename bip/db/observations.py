"""Persistence for Stage 2 observations: raw payloads, blocks, network observations,
balance changes, ingestion runs/errors and wallet cursors.

Every insert is idempotent on its natural key; repeated ingestion never duplicates rows.
"""

from __future__ import annotations

import hashlib
import json
import zlib
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple

from sqlalchemy import ColumnElement, Select, Table, func, insert, or_, select, update
from sqlalchemy.engine import Connection

from bip.contracts.provenance import Finality, Provenance
from bip.db import schema
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.security.secrets import redact_text

MAX_RAW_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Cursor:
    newest_tx_id: str | None = None
    newest_position: int | None = None
    oldest_tx_id: str | None = None
    oldest_position: int | None = None
    scanned_from_block: int | None = None
    scanned_to_block: int | None = None


class OrphanEffects(NamedTuple):
    """What orphaning a block changed."""

    transactions: int
    analyses_stale: int
    signals_invalidated: int
    relationships_invalidated: int


class ObservationStore:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    # ------------------------------------------------------------------ raw payloads

    def store_raw(self, chain: str, provider_id: str, method: str, body: bytes) -> str | None:
        if len(body) > MAX_RAW_BYTES:
            return None
        sha = hashlib.sha256(body).hexdigest()
        r = schema.raw_payloads
        with self.repo.transaction() as conn:
            if conn.execute(select(r.c.sha256).where(r.c.sha256 == sha)).first() is None:
                conn.execute(
                    insert(r).values(
                        sha256=sha,
                        chain=chain,
                        provider_id=provider_id,
                        method=method,
                        fetched_at=utcnow(),
                        size_bytes=len(body),
                        body_zlib=zlib.compress(body, 6),
                    )
                )
        return sha

    def raw_payload(self, sha: str) -> bytes | None:
        r = schema.raw_payloads
        with self.repo.transaction() as conn:
            row = conn.execute(select(r.c.body_zlib).where(r.c.sha256 == sha)).first()
        return zlib.decompress(row[0]) if row is not None else None

    # ------------------------------------------------------------------ blocks / network

    def store_block(
        self,
        *,
        chain: str,
        block_hash: str,
        block_number: int | None,
        slot: int | None,
        parent_hash: str | None,
        block_time: datetime | None,
        tx_count: int | None,
        finality: str,
        provenance_json: str,
    ) -> bool:
        b = schema.blocks
        with self.repo.transaction() as conn:
            existing = conn.execute(
                select(b.c.id, b.c.finality).where(b.c.chain == chain, b.c.block_hash == block_hash)
            ).first()
            if existing is not None:
                if existing.finality != "finalized" and finality == "finalized":
                    conn.execute(update(b).where(b.c.id == existing.id).values(finality=finality))
                return False
            conn.execute(
                insert(b).values(
                    chain=chain,
                    block_number=block_number,
                    slot=slot,
                    block_hash=block_hash,
                    parent_hash=parent_hash,
                    block_time=block_time,
                    tx_count=tx_count,
                    finality=finality,
                    provenance_json=provenance_json,
                    stored_at=utcnow(),
                )
            )
        return True

    def record_network_observation(
        self,
        *,
        chain: str,
        network: str,
        provider_id: str,
        identity: str,
        head_block: int | None = None,
        head_slot: int | None = None,
        head_hash: str | None = None,
        head_time: datetime | None = None,
        finalized_block: int | None = None,
        commitment: str | None = None,
    ) -> None:
        with self.repo.transaction() as conn:
            conn.execute(
                insert(schema.network_observations).values(
                    chain=chain,
                    network=network,
                    provider_id=provider_id,
                    observed_at=utcnow(),
                    identity=identity,
                    head_block=head_block,
                    head_slot=head_slot,
                    head_hash=head_hash,
                    head_time=head_time,
                    finalized_block=finalized_block,
                    commitment=commitment,
                )
            )

    def latest_network_observation(self, chain: str) -> dict[str, object] | None:
        n = schema.network_observations
        with self.repo.transaction() as conn:
            row = (
                conn.execute(
                    select(n)
                    .where(n.c.chain == chain)
                    .order_by(n.c.observed_at.desc(), n.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return dict(row) if row is not None else None

    # ------------------------------------------------------------------ balance changes

    def store_balance_change(
        self,
        *,
        chain: str,
        tx_id: str,
        slot: int | None,
        block_time: datetime | None,
        account: str,
        owner: str | None,
        mint: str | None,
        kind: str,
        pre_raw: int | None,
        post_raw: int | None,
        decimals: int | None,
        parse_status: str,
        provenance_json: str,
    ) -> bool:
        bc = schema.balance_changes
        mint_clause = bc.c.mint.is_(None) if mint is None else bc.c.mint == mint
        delta = None if pre_raw is None or post_raw is None else post_raw - pre_raw
        with self.repo.transaction() as conn:
            exists = conn.execute(
                select(bc.c.id).where(
                    bc.c.chain == chain,
                    bc.c.tx_id == tx_id,
                    bc.c.account == account,
                    mint_clause,
                    bc.c.observation_kind == kind,
                )
            ).first()
            if exists is not None:
                return False
            conn.execute(
                insert(bc).values(
                    chain=chain,
                    tx_id=tx_id,
                    slot=slot,
                    block_time=block_time,
                    account=account,
                    owner=owner,
                    mint=mint,
                    observation_kind=kind,
                    pre_raw=str(pre_raw) if pre_raw is not None else None,
                    post_raw=str(post_raw) if post_raw is not None else None,
                    delta_raw=str(delta) if delta is not None else None,
                    decimals=decimals,
                    parse_status=parse_status,
                    provenance_json=provenance_json,
                    stored_at=utcnow(),
                )
            )
        return True

    # ------------------------------------------------------------------ runs / errors / cursors

    def start_run(self, chain: str, address: str, limit: int) -> int:
        with self.repo.transaction() as conn:
            pk = conn.execute(
                insert(schema.ingestion_runs).values(
                    chain=chain,
                    address=address,
                    status="running",
                    requested_limit=limit,
                    started_at=utcnow(),
                )
            ).inserted_primary_key
            assert pk is not None  # noqa: S101
            conn.execute(
                insert(schema.activity_log).values(
                    event_type="ingestion.started",
                    summary=f"Started {chain} ingestion for {address}",
                    created_at=utcnow(),
                )
            )
        return int(pk[0])

    def add_error(
        self, run_id: int, *, stage: str, error_code: str, detail: str, item_ref: str | None = None
    ) -> None:
        with self.repo.transaction() as conn:
            conn.execute(
                insert(schema.ingestion_errors).values(
                    run_id=run_id,
                    occurred_at=utcnow(),
                    stage=stage[:40],
                    item_ref=(item_ref or "")[:120] or None,
                    error_code=error_code[:40],
                    detail=redact_text(detail)[:500],
                )
            )

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        providers: list[str],
        rpc_calls: int,
        counts: dict[str, int],
        coverage: dict[str, object],
        message: str,
    ) -> None:
        e = schema.ingestion_errors
        with self.repo.transaction() as conn:
            errors = int(
                conn.execute(select(func.count()).where(e.c.run_id == run_id)).scalar_one()
            )
            conn.execute(
                update(schema.ingestion_runs)
                .where(schema.ingestion_runs.c.id == run_id)
                .values(
                    status=status,
                    finished_at=utcnow(),
                    providers_json=json.dumps(sorted(providers)),
                    rpc_calls=rpc_calls,
                    error_count=errors,
                    coverage_json=json.dumps(coverage, default=str),
                    message=message[:500],
                    **counts,
                )
            )
            conn.execute(
                insert(schema.activity_log).values(
                    event_type=f"ingestion.{status}",
                    summary=f"Ingestion run {run_id}: {status}. {message}"[:300],
                    created_at=utcnow(),
                )
            )

    def get_run(self, run_id: int) -> dict[str, object] | None:
        r, e = schema.ingestion_runs, schema.ingestion_errors
        with self.repo.transaction() as conn:
            row = conn.execute(select(r).where(r.c.id == run_id)).mappings().first()
            if row is None:
                return None
            errors = (
                conn.execute(
                    select(e.c.occurred_at, e.c.stage, e.c.item_ref, e.c.error_code, e.c.detail)
                    .where(e.c.run_id == run_id)
                    .order_by(e.c.id)
                    .limit(100)
                )
                .mappings()
                .all()
            )
        out = dict(row)
        out["errors"] = [dict(x) for x in errors]
        return out

    def recent_runs(
        self, limit: int = 10, chain: str | None = None, address: str | None = None
    ) -> list[dict[str, object]]:
        r = schema.ingestion_runs
        q = select(r).order_by(r.c.started_at.desc(), r.c.id.desc()).limit(limit)
        if chain is not None:
            q = q.where(r.c.chain == chain)
        if address is not None:
            q = q.where(r.c.address == address)
        with self.repo.transaction() as conn:
            return [dict(x) for x in conn.execute(q).mappings().all()]

    def run_counts(self) -> dict[str, int]:
        r = schema.ingestion_runs
        with self.repo.transaction() as conn:
            rows = conn.execute(select(r.c.status, func.count()).group_by(r.c.status)).all()
        return {str(s): int(n) for s, n in rows}

    def cursor(self, chain: str, address: str) -> Cursor:
        c = schema.wallet_cursors
        with self.repo.transaction() as conn:
            row = (
                conn.execute(select(c).where(c.c.chain == chain, c.c.address == address))
                .mappings()
                .first()
            )
        if row is None:
            return Cursor()
        return Cursor(
            newest_tx_id=row["newest_tx_id"],
            newest_position=row["newest_position"],
            oldest_tx_id=row["oldest_tx_id"],
            oldest_position=row["oldest_position"],
            scanned_from_block=row["scanned_from_block"],
            scanned_to_block=row["scanned_to_block"],
        )

    def save_cursor(self, chain: str, address: str, cursor: Cursor, run_id: int) -> None:
        c = schema.wallet_cursors
        values = {
            "newest_tx_id": cursor.newest_tx_id,
            "newest_position": cursor.newest_position,
            "oldest_tx_id": cursor.oldest_tx_id,
            "oldest_position": cursor.oldest_position,
            "scanned_from_block": cursor.scanned_from_block,
            "scanned_to_block": cursor.scanned_to_block,
            "last_run_id": run_id,
            "updated_at": utcnow(),
        }
        with self.repo.transaction() as conn:
            exists = conn.execute(
                select(c.c.chain).where(c.c.chain == chain, c.c.address == address)
            ).first()
            if exists is None:
                conn.execute(insert(c).values(chain=chain, address=address, **values))
            else:
                conn.execute(
                    update(c).where(c.c.chain == chain, c.c.address == address).values(**values)
                )

    def link_wallet_activity(
        self, chain: str, address: str, tx_id: str, discovered_by: str, run_id: int
    ) -> None:
        wa = schema.wallet_activity
        with self.repo.transaction() as conn:
            exists = conn.execute(
                select(wa.c.tx_id).where(
                    wa.c.chain == chain, wa.c.address == address, wa.c.tx_id == tx_id
                )
            ).first()
            if exists is None:
                conn.execute(
                    insert(wa).values(
                        chain=chain,
                        address=address,
                        tx_id=tx_id,
                        discovered_by=discovered_by,
                        first_run_id=run_id,
                        first_seen_at=utcnow(),
                    )
                )

    # ------------------------------------------------------------------ wallet queries

    def wallet_transactions(
        self, chain: str, address: str, limit: int, offset: int
    ) -> tuple[list[dict[str, object]], int]:
        t = schema.transactions
        party = or_(t.c.from_address == address, t.c.to_address == address)
        involved = select(schema.transfers.c.tx_id).where(
            schema.transfers.c.chain == chain,
            or_(
                schema.transfers.c.from_address == address, schema.transfers.c.to_address == address
            ),
        )
        balance = select(schema.balance_changes.c.tx_id).where(
            schema.balance_changes.c.chain == chain,
            or_(
                schema.balance_changes.c.account == address,
                schema.balance_changes.c.owner == address,
            ),
        )
        linked = select(schema.wallet_activity.c.tx_id).where(
            schema.wallet_activity.c.chain == chain, schema.wallet_activity.c.address == address
        )
        cond = (t.c.chain == chain) & or_(
            party, t.c.tx_id.in_(involved), t.c.tx_id.in_(balance), t.c.tx_id.in_(linked)
        )
        with self.repo.transaction() as conn:
            total = int(conn.execute(select(func.count()).select_from(t).where(cond)).scalar_one())
            rows = (
                conn.execute(
                    select(t)
                    .where(cond)
                    .order_by(t.c.block_number.desc(), t.c.slot.desc(), t.c.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows], total

    def wallet_transfers(
        self, chain: str, address: str, limit: int, offset: int
    ) -> tuple[list[dict[str, object]], int]:
        tr = schema.transfers
        tx_finality = _tx_finality(tr)
        owned = owned_token_accounts(chain, address)
        cond = (tr.c.chain == chain) & or_(
            tr.c.from_address == address,
            tr.c.to_address == address,
            tr.c.from_address.in_(owned),
            tr.c.to_address.in_(owned),
        )
        with self.repo.transaction() as conn:
            total = int(conn.execute(select(func.count()).select_from(tr).where(cond)).scalar_one())
            rows = (
                conn.execute(
                    select(tr, tx_finality)
                    .where(cond)
                    .order_by(tr.c.block_number.desc(), tr.c.slot.desc(), tr.c.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows], total

    def wallet_balance_changes(
        self, chain: str, address: str, limit: int, offset: int
    ) -> tuple[list[dict[str, object]], int]:
        bc = schema.balance_changes
        tx_finality = _tx_finality(bc)
        cond = (bc.c.chain == chain) & or_(bc.c.account == address, bc.c.owner == address)
        with self.repo.transaction() as conn:
            total = int(conn.execute(select(func.count()).select_from(bc).where(cond)).scalar_one())
            rows = (
                conn.execute(
                    select(bc, tx_finality)
                    .where(cond)
                    .order_by(bc.c.slot.desc(), bc.c.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows], total

    # ------------------------------------------------------------------ recovery and finality

    def solana_placeholders(
        self, addresses: list[str], limit: int, max_attempts: int
    ) -> list[tuple[str, int, datetime | None, bool]]:
        """Solana transactions linked to any of ``addresses`` whose stored row is a placeholder.

        A placeholder is a row written when the transaction could not be fetched or parsed
        (``parse_status='failed'``). Rows that already failed ``max_attempts`` fetches (counted from
        the recorded ingestion errors) are skipped, and the least-retried rows come first, so a
        permanently unavailable transaction cannot starve the others. Returns
        ``(signature, slot, block_time, failed)``.
        """
        if not addresses or limit <= 0:
            return []
        t, wa, ie = schema.transactions, schema.wallet_activity, schema.ingestion_errors
        tries = (
            select(ie.c.item_ref.label("ref"), func.count().label("n"))
            .where(ie.c.stage == "sol_tx")
            .group_by(ie.c.item_ref)
            .subquery()
        )
        attempts = func.coalesce(tries.c.n, 0)
        with self.repo.transaction() as conn:
            rows = conn.execute(
                select(t.c.tx_id, t.c.slot, t.c.block_time, t.c.status, attempts.label("tries"))
                .distinct()
                .join(wa, (wa.c.chain == t.c.chain) & (wa.c.tx_id == t.c.tx_id))
                .outerjoin(tries, tries.c.ref == t.c.tx_id)
                .where(
                    t.c.chain == "solana",
                    t.c.parse_status == "failed",
                    t.c.slot.is_not(None),
                    wa.c.address.in_(addresses),
                    attempts < max_attempts,
                )
                .order_by(attempts.asc(), t.c.slot.desc())
                .limit(limit)
            ).all()
        return [(str(r.tx_id), int(r.slot), r.block_time, r.status == "failed") for r in rows]

    def ethereum_partials(self, address: str, limit: int, max_attempts: int) -> list[str]:
        """Ethereum transactions of ``address`` stored without a receipt, least-retried first.

        Rows with ``max_attempts`` or more recorded ``eth_tx`` errors are skipped.
        """
        if limit <= 0:
            return []
        t, wa, ie = schema.transactions, schema.wallet_activity, schema.ingestion_errors
        tries = (
            select(ie.c.item_ref.label("ref"), func.count().label("n"))
            .where(ie.c.stage == "eth_tx")
            .group_by(ie.c.item_ref)
            .subquery()
        )
        attempts = func.coalesce(tries.c.n, 0)
        with self.repo.transaction() as conn:
            rows = conn.execute(
                select(t.c.tx_id, attempts.label("tries"))
                .distinct()
                .join(wa, (wa.c.chain == t.c.chain) & (wa.c.tx_id == t.c.tx_id))
                .outerjoin(tries, tries.c.ref == t.c.tx_id)
                .where(
                    t.c.chain == "ethereum",
                    t.c.parse_status == "partial",
                    wa.c.address == address,
                    attempts < max_attempts,
                )
                .order_by(attempts.asc(), t.c.block_number.desc())
                .limit(limit)
            ).all()
        return [str(r.tx_id) for r in rows]

    def ethereum_blocks_not_final(self, up_to_block: int, limit: int) -> list[tuple[int, str]]:
        """Distinct ``(block_number, block_hash)`` of stored Ethereum transactions that are not yet
        marked finalized, at or below ``up_to_block``. Newest first, at most ``limit`` entries."""
        t = schema.transactions
        with self.repo.transaction() as conn:
            rows = conn.execute(
                select(t.c.block_number, t.c.block_hash)
                .distinct()
                .where(
                    t.c.chain == "ethereum",
                    t.c.block_number.is_not(None),
                    t.c.block_number <= up_to_block,
                    t.c.block_hash.is_not(None),
                    or_(
                        t.c.finality.is_(None),
                        t.c.finality.not_in([Finality.FINALIZED.value, Finality.ORPHANED.value]),
                    ),
                )
                .order_by(t.c.block_number.desc())
                .limit(limit)
            ).all()
        return [(int(r.block_number), str(r.block_hash)) for r in rows]

    def mark_ethereum_block_finalized(self, block_number: int, block_hash: str) -> int:
        """Upgrade the stored transactions of one block to ``finalized``.

        Only call this after the block hash has been checked against the canonical chain. Updates
        both the ``finality`` column and the finality inside ``provenance_json``, and the block row.
        Returns the number of transactions changed.
        """
        t, b = schema.transactions, schema.blocks
        changed = 0
        with self.repo.transaction() as conn:
            rows = conn.execute(
                select(t.c.id, t.c.provenance_json).where(
                    t.c.chain == "ethereum",
                    t.c.block_number == block_number,
                    t.c.block_hash == block_hash,
                    or_(t.c.finality.is_(None), t.c.finality != Finality.FINALIZED.value),
                )
            ).all()
            for r in rows:
                upgraded = Provenance.model_validate_json(str(r.provenance_json)).model_copy(
                    update={"finality": Finality.FINALIZED}
                )
                conn.execute(
                    update(t)
                    .where(t.c.id == r.id)
                    .values(
                        finality=Finality.FINALIZED.value,
                        provenance_json=upgraded.model_dump_json(),
                    )
                )
                changed += 1
            conn.execute(
                update(b)
                .where(b.c.chain == "ethereum", b.c.block_hash == block_hash)
                .values(finality=Finality.FINALIZED.value)
            )
        return changed

    def mark_ethereum_block_orphaned(
        self, block_number: int, block_hash: str, reason: str
    ) -> OrphanEffects:
        """Mark the stored transactions of a block that is no longer canonical as orphaned.

        Call this only after the block was shown to be off the canonical chain (its hash differs
        from the chain at a height at or below the finalized block). Rows are kept for audit.
        Derived data that may rest on them is invalidated in the same transaction (see
        :meth:`_invalidate_derived`) and reported in the returned :class:`OrphanEffects`.
        """
        t, b = schema.transactions, schema.blocks
        orphaned = Finality.ORPHANED
        with self.repo.transaction() as conn:
            rows = conn.execute(
                select(t.c.id, t.c.tx_id, t.c.provenance_json).where(
                    t.c.chain == "ethereum",
                    t.c.block_number == block_number,
                    t.c.block_hash == block_hash,
                    or_(t.c.finality.is_(None), t.c.finality != orphaned.value),
                )
            ).all()
            tx_ids: list[str] = []
            for r in rows:
                marked = Provenance.model_validate_json(str(r.provenance_json)).model_copy(
                    update={"finality": orphaned}
                )
                conn.execute(
                    update(t)
                    .where(t.c.id == r.id)
                    .values(finality=orphaned.value, provenance_json=marked.model_dump_json())
                )
                tx_ids.append(str(r.tx_id))
            conn.execute(
                update(b)
                .where(b.c.chain == "ethereum", b.c.block_hash == block_hash)
                .values(finality=orphaned.value)
            )
            stale, signals, relationships = self._invalidate_derived(
                conn, "ethereum", tx_ids, reason
            )
        return OrphanEffects(len(tx_ids), stale, signals, relationships)

    @staticmethod
    def _invalidate_derived(
        conn: Connection, chain: str, tx_ids: list[str], reason: str
    ) -> tuple[int, int, int]:
        """Invalidate derived records that may rest on now-orphaned transactions.

        * Each transaction gets an ``invalidated`` verification, which is already the effective
          status shown beside any evidence that cites it.
        * Succeeded analyses of every address that took part in those transactions become
          ``stale``: they leave the current views and are never reused for a cache hit. They are
          not deleted, and a new analysis recomputes without the orphaned rows.

        * Open or promoted discovery signals that cite them become ``invalidated`` and stop being
          alertable; dismissed signals are left alone.

        * Active relationships that cite them become ``invalidated``.

        Findings written by analysts are not edited. Returns ``(analyses_marked_stale,
        signals_invalidated, relationships_invalidated)``.
        """
        if not tx_ids:
            return 0, 0, 0
        t, tr, bc, wa = (
            schema.transactions,
            schema.transfers,
            schema.balance_changes,
            schema.wallet_activity,
        )
        ev, ar = schema.evidence_verifications, schema.analysis_runs
        now = utcnow()
        addresses: set[str] = set()
        for start in range(0, len(tx_ids), 400):
            chunk = tx_ids[start : start + 400]
            for column in (t.c.from_address, t.c.to_address):
                addresses.update(
                    str(a)
                    for (a,) in conn.execute(
                        select(column).where(t.c.chain == chain, t.c.tx_id.in_(chunk))
                    ).all()
                    if a
                )
            for column in (tr.c.from_address, tr.c.to_address):
                addresses.update(
                    str(a)
                    for (a,) in conn.execute(
                        select(column).where(tr.c.chain == chain, tr.c.tx_id.in_(chunk))
                    ).all()
                    if a
                )
            for column in (bc.c.account, bc.c.owner):
                addresses.update(
                    str(a)
                    for (a,) in conn.execute(
                        select(column).where(bc.c.chain == chain, bc.c.tx_id.in_(chunk))
                    ).all()
                    if a
                )
            addresses.update(
                str(a)
                for (a,) in conn.execute(
                    select(wa.c.address).where(wa.c.chain == chain, wa.c.tx_id.in_(chunk))
                ).all()
            )
        for tx_id in tx_ids:
            conn.execute(
                insert(ev).values(
                    chain=chain,
                    tx_id=tx_id,
                    checked_at=now,
                    outcome="invalidated",
                    independent_providers=0,
                    providers_json="[]",
                    differences_json=json.dumps({"reason": reason[:200]}),
                    note=reason[:300],
                )
            )
        stale = 0
        ordered = sorted(addresses)
        for start in range(0, len(ordered), 400):
            result = conn.execute(
                update(ar)
                .where(
                    ar.c.chain == chain,
                    ar.c.address.in_(ordered[start : start + 400]),
                    ar.c.status == "succeeded",
                )
                .values(status="stale")
            )
            stale += int(result.rowcount or 0)
        sg, se = schema.signals, schema.signal_evidence
        signal_ids: set[int] = set()
        for start in range(0, len(tx_ids), 400):
            signal_ids.update(
                int(i)
                for (i,) in conn.execute(
                    select(se.c.signal_id).where(
                        se.c.chain == chain, se.c.tx_id.in_(tx_ids[start : start + 400])
                    )
                ).all()
            )
        invalidated = 0
        ids = sorted(signal_ids)
        for start in range(0, len(ids), 400):
            result = conn.execute(
                update(sg)
                .where(sg.c.id.in_(ids[start : start + 400]), sg.c.status.in_(("open", "promoted")))
                .values(status="invalidated", status_note=reason[:300])
            )
            invalidated += int(result.rowcount or 0)
        rel, rev = schema.entity_relationships, schema.relationship_evidence
        rel_ids: set[int] = set()
        for start in range(0, len(tx_ids), 400):
            rel_ids.update(
                int(i)
                for (i,) in conn.execute(
                    select(rev.c.relationship_id).where(
                        rev.c.chain == chain, rev.c.tx_id.in_(tx_ids[start : start + 400])
                    )
                ).all()
            )
        rel_invalidated = 0
        rids = sorted(rel_ids)
        for start in range(0, len(rids), 400):
            result = conn.execute(
                update(rel)
                .where(rel.c.id.in_(rids[start : start + 400]), rel.c.status == "active")
                .values(status="invalidated", note=reason[:300])
            )
            rel_invalidated += int(result.rowcount or 0)
        if rids:
            invalidated += _withdraw_relationship_dependents(conn, chain, rids, reason)
        # Intelligence memory (v18): facts resting on these transactions stop being current. The
        # rows stay, so the history shows what was believed and why it was withdrawn.
        ef = schema.entity_facts
        for start in range(0, len(tx_ids), 400):
            conn.execute(
                update(ef)
                .where(
                    ef.c.source_chain == chain,
                    ef.c.source_tx.in_(tx_ids[start : start + 400]),
                    ef.c.status.in_(("current", "superseded")),
                )
                .values(status="invalidated")
            )
        _requeue_memory(conn, chain, tx_ids, addresses, now)
        return stale, invalidated, rel_invalidated


def _withdraw_relationship_dependents(
    conn: Connection, chain: str, relationship_ids: list[int], reason: str
) -> int:
    """What rests on relationships a reorg just invalidated, without waiting for a later scan.

    * The memory fact ``funded_by:<funder>`` of each invalidated funder link stops being current
      (kept, marked ``invalidated``), since that fact cites the relationship, not a transaction.
    * Open or promoted expansion candidates on the chain are invalidated: they walk stored
      relationships through several hops but keep evidence only for the last one, so any of them
      may rest on the withdrawn link. The next expansion run rebuilds those still supported.

    Returns the number of signals invalidated.
    """
    rel, e, ef, sg = (
        schema.entity_relationships,
        schema.entities,
        schema.entity_facts,
        schema.signals,
    )
    funder_links: list[tuple[str, str]] = []
    for start in range(0, len(relationship_ids), 400):
        funder_links.extend(
            (str(src), str(dst))
            for src, dst in conn.execute(
                select(rel.c.source_ref, rel.c.target_ref).where(
                    rel.c.id.in_(relationship_ids[start : start + 400]),
                    rel.c.kind == "funded_by",
                )
            ).all()
        )
    for source_ref, target_ref in funder_links:
        entity = select(e.c.id).where(e.c.chain == chain, e.c.ref == source_ref).scalar_subquery()
        conn.execute(
            update(ef)
            .where(
                ef.c.entity_id == entity,
                ef.c.fact_key == f"funded_by:{target_ref}",
                ef.c.status == "current",
            )
            .values(status="invalidated")
        )
    result = conn.execute(
        update(sg)
        .where(
            sg.c.chain == chain,
            sg.c.detector_id.like("expansion\\_%", escape="\\"),
            sg.c.status.in_(("open", "promoted")),
        )
        .values(status="invalidated", status_note=f"relationship withdrawn: {reason}"[:300])
    )
    return int(result.rowcount or 0)


def _tx_finality(child: Table) -> ColumnElement[str]:
    """Finality of the transaction a transfer or balance change belongs to.

    Those rows keep the provenance they were parsed with, so their own finality goes stale when
    the transaction is later finalized or orphaned. The transaction row is authoritative.
    """
    t = schema.transactions
    return (
        select(t.c.finality)
        .where(t.c.chain == child.c.chain, t.c.tx_id == child.c.tx_id)
        .limit(1)
        .scalar_subquery()
        .label("tx_finality")
    )


def owned_token_accounts(chain: str, owner: str) -> Select[tuple[str]]:
    """Token accounts that token-balance metadata reports as owned by ``owner`` (Solana)."""
    bc = schema.balance_changes
    return select(bc.c.account).where(
        bc.c.chain == chain, bc.c.owner == owner, bc.c.account != owner
    )


def _requeue_memory(
    conn: Connection, chain: str, tx_ids: list[str], addresses: set[str], now: datetime
) -> None:
    """After a reorg, undo kind upgrades that rested on the orphaned transactions and queue the
    affected addresses so the next memory refresh recomputes their measurements (activity counts
    have no single transaction to invalidate)."""
    e, ef, m = schema.entities, schema.entity_facts, schema.app_metadata
    for start in range(0, len(tx_ids), 400):
        rows = conn.execute(
            select(ef.c.entity_id, ef.c.value_json).where(
                ef.c.fact_key == "kind",
                ef.c.source_chain == chain,
                ef.c.source_tx.in_(tx_ids[start : start + 400]),
                ef.c.status == "invalidated",
            )
        ).all()
        for entity_id, value_json in rows:
            value = json.loads(value_json)
            previous, upgraded = value.get("from"), value.get("to")
            if previous and upgraded:  # only undo an upgrade that is still in place
                conn.execute(
                    update(e).where(e.c.id == entity_id, e.c.kind == upgraded).values(kind=previous)
                )
    if not addresses:
        return
    key = f"memory:pending:{chain}"
    raw = conn.execute(select(m.c.value).where(m.c.key == key)).scalar_one_or_none()
    pending: dict[str, str] = json.loads(raw) if raw else {}
    for a in addresses:
        pending.setdefault(a, "wallet")
    value = json.dumps(pending)
    if raw is None:
        conn.execute(insert(m).values(key=key, value=value, updated_at=now))
    else:
        conn.execute(update(m).where(m.c.key == key).values(value=value, updated_at=now))
