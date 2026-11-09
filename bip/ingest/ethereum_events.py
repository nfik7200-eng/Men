"""Event feeds read over a range of finalized blocks: pools, ERC-20 transfers, pool reserves.

Each feed makes one topic-filtered ``eth_getLogs`` request for the range the collector just read
(split automatically when a provider limit is hit) and stores a bounded slice of it:

* **pools**: v2/v3-style creation events, with the creating transaction as evidence;
* **tokens**: every ERC-20 ``Transfer`` is *counted* per token (``token_stats``); transfers of
  tokens that have a known pool are also stored (they feed the whale and holder detectors); the
  first transaction of each newly seen token is stored as evidence;
* **reserves**: v2 ``Sync`` events of known pools, the last one per pool and block.

Every log is checked against the header of the block the collector read: a log from a different
block hash is rejected, never stored. All feeds are idempotent. Caps are disclosed in the run
manifest; nothing here claims to see every token, pool or swap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert, select, update

from bip.chains.common import ParseError, SourceInfo
from bip.chains.ethereum import parse as eth
from bip.chains.ethereum.adapter import EthereumAdapter
from bip.chains.ethereum.parse import TRANSFER_TOPIC
from bip.chains.ethereum.pools import POOL_TOPICS, SYNC_TOPIC, decode_pool_created, decode_sync
from bip.config.settings import CollectorSettings
from bip.contracts.events import EvmLog
from bip.contracts.provenance import Finality
from bip.contracts.transfers import TokenStandard
from bip.db import schema
from bip.db.repository import Repository
from bip.db.types import utcnow

CHAIN = "ethereum"
STATS_CURSOR_KEY = "collector:ethereum:token_stats_block"
"""Highest block already counted in ``token_stats``; a rescan never counts a block twice."""
IN_CHUNK = 400


def persist_transaction(repo: Repository, parsed: eth.ParsedTransaction) -> bool:
    """Store a finalized transaction read by the collector (no raw payload is retained)."""
    tx = parsed.tx
    return repo.store_transaction(
        tx,
        extras={
            "network": "mainnet",
            "block_hash": tx.block_hash,
            "finality": Finality.FINALIZED.value,
            "parse_status": "complete",
            "value_raw": str(parsed.value_wei),
            "gas_used": str(parsed.gas_used) if parsed.gas_used is not None else None,
            "fee_raw": str(parsed.gas_used * parsed.gas_price_wei)
            if parsed.gas_used is not None and parsed.gas_price_wei is not None
            else None,
            "raw_ref": None,
            "run_id": None,
            "created_contract": tx.created_contract,
        },
    )


@dataclass(slots=True)
class _TokenSeen:
    count: int
    first_block: int
    first_log: int
    first_tx: str
    last_block: int


class EventFeeds:
    def __init__(self, repo: Repository, cfg: CollectorSettings) -> None:
        self._repo = repo
        self._cfg = cfg

    def run(
        self,
        adapter: EthereumAdapter,
        start: int,
        end: int,
        headers: dict[int, eth.BlockHeader],
        m: dict[str, Any],
    ) -> None:
        """Run every feed over ``start..end``; the headers are those the collector just read."""
        self.pools(adapter, start, end, headers, m)
        if self._cfg.token_scan_enabled:
            self.tokens(adapter, start, end, headers, m)
        self.reserves(adapter, start, end, headers, m)

    # ----------------------------------------------------------------------------- helpers

    def _log(
        self, raw: object, src: SourceInfo, headers: dict[int, eth.BlockHeader], m: dict[str, Any]
    ) -> EvmLog | None:
        """Parse a log and require it to belong to the canonical block the collector read."""
        try:
            lg = eth.parse_log(raw, src, block_time=None, finality=Finality.FINALIZED)
        except ParseError:
            m["rejected"] += 1
            return None
        header = headers.get(lg.block_number)
        if lg.removed or header is None or header.hash != lg.block_hash:
            m["rejected"] += 1
            return None
        return lg

    def _store_evidence_tx(
        self, adapter: EthereumAdapter, tx_hash: str, header: eth.BlockHeader, m: dict[str, Any]
    ) -> bool:
        raw_tx, _ = adapter.transaction(tx_hash)
        receipt, rsrc = adapter.receipt(tx_hash)
        if raw_tx is None or receipt is None:
            m["receipt_missing"] += 1
            return False
        try:
            parsed = eth.parse_transaction(
                raw_tx,
                receipt,
                block_time=header.timestamp,
                src=rsrc,
                finality=Finality.FINALIZED,
            )
        except ParseError:
            m["rejected"] += 1
            return False
        if parsed.tx.block_hash != header.hash:
            m["rejected"] += 1
            return False
        persist_transaction(self._repo, parsed)
        return True

    # ----------------------------------------------------------------------------- pools

    def pools(
        self,
        adapter: EthereumAdapter,
        start: int,
        end: int,
        headers: dict[int, eth.BlockHeader],
        m: dict[str, Any],
    ) -> None:
        found, notes = adapter.event_logs_adaptive(start, end, topics=POOL_TOPICS)
        m["pool_log_splits"] = len(notes)
        decoded: list[tuple[EvmLog, Any]] = []
        for raw, src in found:
            lg = self._log(raw, src, headers, m)
            pool = decode_pool_created(lg) if lg is not None else None
            if lg is not None and pool is not None:
                decoded.append((lg, pool))
        decoded.sort(key=lambda x: (x[0].block_number, x[0].log_index))
        m["pool_events_found"] = len(decoded)
        pe = schema.pool_events
        taken = 0
        for lg, pool in decoded:
            with self._repo.transaction() as conn:
                known = conn.execute(
                    select(pe.c.id).where(
                        pe.c.chain == CHAIN,
                        pe.c.tx_id == lg.tx_hash,
                        pe.c.log_index == lg.log_index,
                    )
                ).first()
            if known is not None:
                continue
            if taken >= self._cfg.max_pools_per_run:
                m["pool_events_over_cap"] += 1
                continue
            taken += 1
            header = headers[lg.block_number]
            if not self._store_evidence_tx(adapter, lg.tx_hash, header, m):
                continue
            with self._repo.transaction() as conn:
                conn.execute(
                    insert(pe).values(
                        chain=CHAIN,
                        protocol=pool.protocol,
                        factory=pool.factory,
                        pool=pool.pool,
                        token0=pool.token0,
                        token1=pool.token1,
                        fee=pool.fee,
                        tx_id=lg.tx_hash,
                        log_index=lg.log_index,
                        block_number=lg.block_number,
                        block_hash=lg.block_hash,
                        block_time=header.timestamp,
                        finality=Finality.FINALIZED.value,
                        stored_at=utcnow(),
                    )
                )
            m["pool_events_stored"] += 1

    # ----------------------------------------------------------------------------- tokens

    def _pool_tokens(self) -> set[str]:
        pe = schema.pool_events
        with self._repo.transaction() as conn:
            rows = conn.execute(select(pe.c.token0, pe.c.token1).where(pe.c.chain == CHAIN)).all()
        return {str(t) for r in rows for t in (r.token0, r.token1)}

    def tokens(
        self,
        adapter: EthereumAdapter,
        start: int,
        end: int,
        headers: dict[int, eth.BlockHeader],
        m: dict[str, Any],
    ) -> None:
        found, notes = adapter.event_logs_adaptive(start, end, topics=(TRANSFER_TOPIC,))
        m["token_log_splits"] = len(notes)
        m["token_logs_found"] = len(found)
        pool_tokens = self._pool_tokens()
        counted_through = int(self._repo.get_meta(STATS_CURSOR_KEY) or -1)
        seen: dict[str, _TokenSeen] = {}
        stored = 0
        for raw, src in found:
            lg = self._log(raw, src, headers, m)
            if lg is None:
                continue
            transfer, _ = eth.decode_transfer_log(lg)
            if transfer is None:
                m["token_logs_undecodable"] += 1
                continue
            if transfer.standard is not TokenStandard.ERC20:
                m["nft_transfers_skipped"] += 1
                continue
            token = lg.address
            entry = seen.get(token)
            if lg.block_number <= counted_through:
                pass  # counted by an earlier (interrupted) pass; storing below is idempotent
            elif entry is None:
                seen[token] = _TokenSeen(
                    1, lg.block_number, lg.log_index, lg.tx_hash, lg.block_number
                )
            else:
                entry.count += 1
                entry.last_block = max(entry.last_block, lg.block_number)
                if (lg.block_number, lg.log_index) < (entry.first_block, entry.first_log):
                    entry.first_block, entry.first_log, entry.first_tx = (
                        lg.block_number,
                        lg.log_index,
                        lg.tx_hash,
                    )
            if token in pool_tokens:
                if stored >= self._cfg.max_token_transfers_per_run:
                    m["token_transfers_over_cap"] += 1
                elif self._repo.store_transfer(
                    transfer,
                    extras={
                        "observation_kind": "erc20_transfer_log",
                        "block_time": headers[lg.block_number].timestamp,
                        "raw_ref": None,
                    },
                ):
                    stored += 1
        m["token_transfers_stored"] = stored
        m["tokens_seen"] = len(seen)
        self._update_token_stats(adapter, seen, headers, m)
        self._repo.set_meta(STATS_CURSOR_KEY, str(max(end, counted_through)))

    def _update_token_stats(
        self,
        adapter: EthereumAdapter,
        seen: dict[str, _TokenSeen],
        headers: dict[int, eth.BlockHeader],
        m: dict[str, Any],
    ) -> None:
        ts = schema.token_stats
        tokens = sorted(seen)
        known: set[str] = set()
        with self._repo.transaction() as conn:
            for i in range(0, len(tokens), IN_CHUNK):
                chunk = tokens[i : i + IN_CHUNK]
                known.update(
                    str(x)
                    for x in conn.execute(
                        select(ts.c.token).where(ts.c.chain == CHAIN, ts.c.token.in_(chunk))
                    ).scalars()
                )
            for token in known:
                s = seen[token]
                conn.execute(
                    update(ts)
                    .where(ts.c.chain == CHAIN, ts.c.token == token)
                    .values(
                        transfers_seen=ts.c.transfers_seen + s.count,
                        last_seen_block=schema_greatest(ts, s.last_block),
                    )
                )
        fresh = sorted(
            (t for t in tokens if t not in known),
            key=lambda t: (seen[t].first_block, seen[t].first_log),
        )
        with_evidence = 0
        for token in fresh:
            s = seen[token]
            if with_evidence < self._cfg.max_new_tokens_per_run:
                with_evidence += 1
                self._store_evidence_tx(adapter, s.first_tx, headers[s.first_block], m)
            else:
                m["new_tokens_without_evidence_tx"] += 1
            with self._repo.transaction() as conn:
                conn.execute(
                    insert(ts).values(
                        chain=CHAIN,
                        token=token,
                        first_seen_block=s.first_block,
                        first_seen_at=headers[s.first_block].timestamp,
                        first_tx_id=s.first_tx,
                        transfers_seen=s.count,
                        last_seen_block=s.last_block,
                    )
                )
        m["new_tokens"] = len(fresh)

    # ----------------------------------------------------------------------------- reserves

    def reserves(
        self,
        adapter: EthereumAdapter,
        start: int,
        end: int,
        headers: dict[int, eth.BlockHeader],
        m: dict[str, Any],
    ) -> None:
        if self._cfg.max_reserve_pools == 0:
            return
        pe = schema.pool_events
        with self._repo.transaction() as conn:
            pools = [
                str(x)
                for x in conn.execute(
                    select(pe.c.pool)
                    .where(pe.c.chain == CHAIN, pe.c.protocol == "v2_like")
                    .order_by(pe.c.block_time.desc())
                    .limit(self._cfg.max_reserve_pools)
                ).scalars()
            ]
        m["reserve_pools_watched"] = len(pools)
        if not pools:
            return
        found, notes = adapter.event_logs_adaptive(
            start, end, topics=(SYNC_TOPIC,), addresses=tuple(pools)
        )
        m["reserve_log_splits"] = len(notes)
        last: dict[tuple[str, int], tuple[EvmLog, tuple[int, int]]] = {}
        wanted = set(pools)
        for raw, src in found:
            lg = self._log(raw, src, headers, m)
            if lg is None or lg.address not in wanted:
                continue
            reserves = decode_sync(lg)
            if reserves is None:
                m["rejected"] += 1
                continue
            key = (lg.address, lg.block_number)
            if key not in last or lg.log_index > last[key][0].log_index:
                last[key] = (lg, reserves)
        m["reserve_updates_found"] = len(last)
        pr = schema.pool_reserves
        stored = 0
        for (pool, _block), (lg, (r0, r1)) in sorted(
            last.items(), key=lambda kv: (kv[0][1], kv[0][0])
        ):
            with self._repo.transaction() as conn:
                known = conn.execute(
                    select(pr.c.id).where(
                        pr.c.chain == CHAIN,
                        pr.c.tx_id == lg.tx_hash,
                        pr.c.log_index == lg.log_index,
                    )
                ).first()
            if known is not None:
                continue
            if stored >= self._cfg.max_reserve_updates_per_run:
                m["reserve_updates_over_cap"] += 1
                continue
            with self._repo.transaction() as conn:
                conn.execute(
                    insert(pr).values(
                        chain=CHAIN,
                        pool=pool,
                        tx_id=lg.tx_hash,
                        log_index=lg.log_index,
                        block_number=lg.block_number,
                        block_hash=lg.block_hash,
                        block_time=headers[lg.block_number].timestamp,
                        reserve0=str(r0),
                        reserve1=str(r1),
                        finality=Finality.FINALIZED.value,
                        stored_at=utcnow(),
                    )
                )
            stored += 1
        m["reserve_updates_stored"] = stored


def schema_greatest(table: Any, value: int) -> Any:
    """``max(last_seen_block, value)`` as a SQL expression (portable across SQLite versions)."""
    from sqlalchemy import case

    return case((table.c.last_seen_block >= value, table.c.last_seen_block), else_=value)
