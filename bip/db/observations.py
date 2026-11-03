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

from sqlalchemy import Select, func, insert, or_, select, update

from bip.db import schema
from bip.db.repository import Repository
from bip.db.types import utcnow

MAX_RAW_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Cursor:
    newest_tx_id: str | None = None
    newest_position: int | None = None
    oldest_tx_id: str | None = None
    oldest_position: int | None = None
    scanned_from_block: int | None = None
    scanned_to_block: int | None = None


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
                    detail=detail[:500],
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
                    select(tr)
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
        cond = (bc.c.chain == chain) & or_(bc.c.account == address, bc.c.owner == address)
        with self.repo.transaction() as conn:
            total = int(conn.execute(select(func.count()).select_from(bc).where(cond)).scalar_one())
            rows = (
                conn.execute(
                    select(bc)
                    .where(cond)
                    .order_by(bc.c.slot.desc(), bc.c.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows], total


def owned_token_accounts(chain: str, owner: str) -> Select[tuple[str]]:
    """Token accounts that token-balance metadata reports as owned by ``owner`` (Solana)."""
    bc = schema.balance_changes
    return select(bc.c.account).where(
        bc.c.chain == chain, bc.c.owner == owner, bc.c.account != owner
    )
