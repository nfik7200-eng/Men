"""Bounded Ethereum block collector: activity nobody asked about.

Wallet ingestion needs an address. Discovery needs the opposite, so this reads **full finalized
blocks** in order and keeps only what the detectors can use:

* contract creations (the receipt names the new contract),
* top-level native transfers of at least ``collector.min_native_value_eth``, and
* liquidity-pool creation events (Uniswap v2/v3-style factory logs, one ``eth_getLogs`` call per
  planned block range, no address filter), with the creating transaction stored as evidence.

Everything else in a block is counted and discarded: ordinary transactions are not stored.
Only blocks at or below the provider's ``finalized`` tag are read, so nothing it stores can be
reorganised away. Work per run is bounded by a block count, a receipt count, an RPC call budget
and a deadline. The cursor (last fully scanned block) lives in ``app_metadata`` and advances only
over blocks that were read completely, so an interrupted run resumes instead of skipping.

What this is **not**: it is not a chain index. It sees no ERC-20 transfer events, no internal
transfers, no liquidity or reserve changes, no holder data, only the two factory event layouts it
knows (a layout is not proof of a DEX), and it does not run for Solana. Those need an
event/stream source and are BLOCKED pending an owner decision on data source and cost (see
``CURRENT_STATE.md``). No raw block payloads are retained (disk constraint), so ``raw_ref`` is
empty for these rows.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import insert, select

from bip.chains.common import ParseError
from bip.chains.ethereum import parse as eth
from bip.chains.ethereum.adapter import EthereumAdapter
from bip.chains.ethereum.pools import POOL_TOPICS, decode_pool_created
from bip.config.settings import Settings
from bip.contracts.provenance import Finality
from bip.core.logging import get_logger
from bip.db import schema
from bip.db.observations import ObservationStore
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.router.router import (
    BudgetExhaustedError,
    CallBudget,
    NoEligibleProviderError,
    ProviderRouter,
    RoutingFailedError,
)
from bip.rpc.jsonrpc import RpcError
from bip.rpc.transport import TransportError

log = get_logger("bip.collector")

CURSOR_KEY = "collector:ethereum:last_block"
LAST_RUN_KEY = "collector:ethereum:last_run"
COLLECTOR_VERSION = "2"

NOT_COVERED = (
    "ordinary transactions below the value threshold (counted, not stored)",
    "ERC-20/721 transfer events, internal transfers, liquidity/reserve and holder changes",
    "pools created by factories with an event layout other than Uniswap v2/v3-style",
    "blocks above the finalized tag",
    "Solana (no collector)",
)


class ChainCollector:
    def __init__(self, router: ProviderRouter, repo: Repository, settings: Settings) -> None:
        self._router = router
        self._repo = repo
        self._store = ObservationStore(repo)
        self._cfg = settings.collector

    def status(self) -> dict[str, Any]:
        raw = self._repo.get_meta(LAST_RUN_KEY)
        cursor = self._repo.get_meta(CURSOR_KEY)
        return {
            "version": COLLECTOR_VERSION,
            "chain": "ethereum",
            "last_block_scanned": int(cursor) if cursor else None,
            "last_run": json.loads(raw) if raw else None,
            "limits": {
                "blocks_per_run": self._cfg.blocks_per_run,
                "min_native_value_eth": self._cfg.min_native_value_eth,
                "max_receipts_per_run": self._cfg.max_receipts_per_run,
                "max_rpc_calls_per_run": self._cfg.max_rpc_calls_per_run,
                "max_pools_per_run": self._cfg.max_pools_per_run,
            },
            "not_covered": list(NOT_COVERED),
        }

    def run(self) -> dict[str, Any]:
        """Scan the next finalized blocks once; always returns a manifest, never raises."""
        cfg = self._cfg
        budget = CallBudget.create(cfg.max_rpc_calls_per_run, cfg.run_deadline_s)
        adapter = EthereumAdapter(self._router, budget=budget)  # no raw sink: disk constraint
        min_wei = cfg.min_native_value_eth * 10**18
        m: dict[str, Any] = {
            "status": "succeeded",
            "started_at": utcnow().isoformat(),
            "blocks_scanned": 0,
            "transactions_seen": 0,
            "contract_creations_stored": 0,
            "large_transfers_stored": 0,
            "skipped_below_threshold": 0,
            "skipped_over_receipt_cap": 0,
            "receipt_missing": 0,
            "rejected": 0,
            "pool_events_found": 0,
            "pool_events_stored": 0,
            "pool_events_over_cap": 0,
            "pool_log_splits": 0,
            "first_block": None,
            "last_block": None,
            "finalized_head": None,
            "message": "",
            "not_covered": list(NOT_COVERED),
        }
        receipts = 0
        try:
            fin = adapter.finalized_block()
            if fin is None:
                m["status"] = "blocked"
                m["message"] = (
                    "No provider serves the finalized tag, so nothing is read: BIP does not "
                    "collect provisional blocks."
                )
                return self._finish(m, adapter)
            finalized = fin[0].number
            m["finalized_head"] = finalized
            raw_cursor = self._repo.get_meta(CURSOR_KEY)
            start = (
                int(raw_cursor) + 1
                if raw_cursor
                else max(0, finalized - cfg.initial_lookback_blocks + 1)
            )
            end = min(finalized, start + cfg.blocks_per_run - 1)
            if start > end:
                m["message"] = "Up to date with the finalized head."
                return self._finish(m, adapter)
            self._scan_pools(adapter, start, end, m)
            for number in range(start, end + 1):
                if receipts >= cfg.max_receipts_per_run:
                    m["status"] = "partial"
                    m["message"] = "Receipt cap reached; the rest resumes next run."
                    break
                pair = adapter.block_full(number)
                if pair is None:
                    m["status"] = "partial"
                    m["message"] = f"Block {number} not returned by the provider; stopped there."
                    break
                header, txs, src = pair
                receipts += self._scan_block(adapter, header, txs, src, min_wei, receipts, m)
                self._repo.set_meta(CURSOR_KEY, str(number))
                m["blocks_scanned"] += 1
                m["first_block"] = m["first_block"] if m["first_block"] is not None else number
                m["last_block"] = number
                m["transactions_seen"] += len(txs)
        except BudgetExhaustedError as exc:
            m["status"] = "partial"
            m["message"] = f"Stopped early: {exc}. The unfinished block is retried next run."
        except NoEligibleProviderError as exc:
            m["status"] = "blocked"
            m["message"] = f"No eligible provider: {exc}"
        except (RoutingFailedError, RpcError, TransportError, ParseError) as exc:
            m["status"] = "partial" if m["blocks_scanned"] else "failed"
            m["message"] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # the manifest must always be written
            log.exception("collector run failed unexpectedly")
            m["status"] = "failed"
            m["message"] = f"unexpected {type(exc).__name__}"
        if not m["message"]:
            m["message"] = (
                f"Scanned {m['blocks_scanned']} finalized block(s): "
                f"{m['contract_creations_stored']} creation(s), "
                f"{m['large_transfers_stored']} large transfer(s), "
                f"{m['pool_events_stored']} new pool(s)."
            )
        return self._finish(m, adapter)

    def _finish(self, m: dict[str, Any], adapter: EthereumAdapter) -> dict[str, Any]:
        m["rpc_calls"] = adapter.calls
        m["providers"] = sorted(adapter.providers_used)
        m["finished_at"] = utcnow().isoformat()
        self._repo.set_meta(LAST_RUN_KEY, json.dumps(m))
        return m

    def _scan_block(
        self,
        adapter: EthereumAdapter,
        header: eth.BlockHeader,
        txs: list[object],
        src: Any,
        min_wei: int,
        receipts_so_far: int,
        m: dict[str, Any],
    ) -> int:
        """Store the interesting transactions of one block; return receipts fetched."""
        self._store.store_block(
            chain="ethereum",
            block_hash=header.hash,
            block_number=header.number,
            slot=None,
            parent_hash=header.parent_hash,
            block_time=header.timestamp,
            tx_count=header.tx_count,
            finality=Finality.FINALIZED.value,
            provenance_json=eth.to_block_contract(
                header, src, Finality.FINALIZED
            ).provenance.model_dump_json(),
        )
        used = 0
        for raw in txs:
            try:
                t = eth.obj(raw, "tx")
                creation = t.get("to") is None
                value = eth.quantity(t.get("value"), "tx.value")
                txh = eth.hash32(t.get("hash"), "tx.hash")
            except ParseError:
                m["rejected"] += 1
                continue
            if not creation and value < min_wei:
                m["skipped_below_threshold"] += 1
                continue
            if receipts_so_far + used >= self._cfg.max_receipts_per_run:
                m["skipped_over_receipt_cap"] += 1  # disclosed gap: not retried
                continue
            receipt, rsrc = adapter.receipt(txh)
            used += 1
            if receipt is None:
                m["receipt_missing"] += 1
                continue
            try:
                parsed = eth.parse_transaction(
                    raw,
                    receipt,
                    block_time=header.timestamp,
                    src=rsrc,
                    finality=Finality.FINALIZED,
                )
            except ParseError:
                m["rejected"] += 1
                continue
            if parsed.tx.block_hash != header.hash:
                m["rejected"] += 1  # receipt/tx disagree with the block just read
                continue
            tx = parsed.tx
            self._persist_tx(parsed)
            if creation:
                if tx.created_contract is not None:
                    m["contract_creations_stored"] += 1
                continue
            native = eth.native_transfer(parsed)
            if native is not None and self._repo.store_transfer(
                native,
                extras={
                    "observation_kind": "native_tx_value",
                    "block_time": header.timestamp,
                    "raw_ref": None,
                },
            ):
                m["large_transfers_stored"] += 1
        return used

    def _persist_tx(self, parsed: eth.ParsedTransaction) -> None:
        tx = parsed.tx
        self._repo.store_transaction(
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

    def _scan_pools(
        self, adapter: EthereumAdapter, start: int, end: int, m: dict[str, Any]
    ) -> None:
        """Store pool-creation events found in ``start..end`` (idempotent), with their creating tx.

        Runs before the blocks so a failure leaves the cursor untouched. Pools beyond
        ``max_pools_per_run`` are counted as a gap and are not retried.
        """
        found, notes = adapter.pool_logs_adaptive(start, end, topics=POOL_TOPICS)
        m["pool_log_splits"] = len(notes)
        decoded: list[tuple[Any, Any]] = []
        for raw, src in found:
            try:
                lg = eth.parse_log(raw, src, block_time=None, finality=Finality.FINALIZED)
            except ParseError:
                m["rejected"] += 1
                continue
            pool = decode_pool_created(lg)
            if pool is not None:
                decoded.append((lg, pool))
        decoded.sort(key=lambda x: (x[0].block_number, x[0].log_index))
        m["pool_events_found"] = len(decoded)
        headers: dict[int, eth.BlockHeader | None] = {}
        taken = 0
        for lg, pool in decoded:
            if self._pool_known(lg.tx_hash, lg.log_index):
                continue
            if taken >= self._cfg.max_pools_per_run:
                m["pool_events_over_cap"] += 1
                continue
            taken += 1
            if lg.block_number not in headers:
                pair = adapter.block(lg.block_number)
                headers[lg.block_number] = pair[0] if pair else None
            header = headers[lg.block_number]
            if header is None or header.hash != lg.block_hash:
                m["rejected"] += 1  # block missing, or the log is from a different block hash
                continue
            raw_tx, _ = adapter.transaction(lg.tx_hash)
            receipt, rsrc = adapter.receipt(lg.tx_hash)
            if raw_tx is None or receipt is None:
                m["receipt_missing"] += 1
                continue
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
                continue
            if parsed.tx.block_hash != header.hash:
                m["rejected"] += 1
                continue
            self._persist_tx(parsed)
            with self._repo.transaction() as conn:
                conn.execute(
                    insert(schema.pool_events).values(
                        chain="ethereum",
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

    def _pool_known(self, tx_id: str, log_index: int) -> bool:
        pe = schema.pool_events
        with self._repo.transaction() as conn:
            return (
                conn.execute(
                    select(pe.c.id).where(
                        pe.c.chain == "ethereum", pe.c.tx_id == tx_id, pe.c.log_index == log_index
                    )
                ).first()
                is not None
            )
