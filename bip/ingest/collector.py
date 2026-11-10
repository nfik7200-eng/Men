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

from bip.chains.common import ParseError
from bip.chains.ethereum import parse as eth
from bip.chains.ethereum.adapter import EthereumAdapter
from bip.config.settings import Settings
from bip.contracts.provenance import Finality
from bip.contracts.transactions import TxStatus
from bip.core.logging import get_logger
from bip.db.observations import ObservationStore
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.ingest.ethereum_events import EventFeeds, persist_transaction
from bip.router.router import (
    BudgetExhaustedError,
    CallBudget,
    NoEligibleProviderError,
    ProviderRouter,
    RoutingFailedError,
)
from bip.rpc.jsonrpc import RpcError
from bip.rpc.transport import TransportError
from bip.security.secrets import redact_text

log = get_logger("bip.collector")

CURSOR_KEY = "collector:ethereum:last_block"
LAST_RUN_KEY = "collector:ethereum:last_run"
COLLECTOR_VERSION = "3"

GAPS_KEY = "collector:ethereum:gaps"
ATTEMPTS_KEY = "collector:ethereum:attempts"
HASH_KEY = "collector:ethereum:last_block_hash"
MAX_GAPS_KEPT = 50

NOT_COVERED = (
    "ordinary transactions below the value threshold (counted, not stored)",
    "ERC-721 transfers (counted, not stored) and internal transfers",
    "ERC-20 transfers of tokens without a known pool are counted per token, not stored",
    "v3-style pool reserves and liquidity (only v2-style Sync events are read)",
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
        self._feeds = EventFeeds(repo, settings.collector, self._record_gap)

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
                "max_new_tokens_per_run": self._cfg.max_new_tokens_per_run,
                "max_token_transfers_per_run": self._cfg.max_token_transfers_per_run,
                "max_reserve_updates_per_run": self._cfg.max_reserve_updates_per_run,
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
            "blocks_given_up": 0,
            "pool_evidence_missing": 0,
            "feed_blocks_skipped": 0,
            "evidence_fetch_failed": 0,
            "tokens_not_tracked": 0,
            "continuity_mismatches": 0,
            "pool_events_found": 0,
            "pool_events_stored": 0,
            "pool_events_over_cap": 0,
            "pool_log_splits": 0,
            "token_logs_found": 0,
            "token_log_splits": 0,
            "token_logs_undecodable": 0,
            "tokens_seen": 0,
            "new_tokens": 0,
            "new_tokens_without_evidence_tx": 0,
            "nft_transfers_skipped": 0,
            "token_transfers_stored": 0,
            "token_transfers_over_cap": 0,
            "reserve_pools_watched": 0,
            "reserve_log_splits": 0,
            "reserve_updates_found": 0,
            "reserve_updates_stored": 0,
            "reserve_updates_over_cap": 0,
            "first_block": None,
            "last_block": None,
            "finalized_head": None,
            "message": "",
            "not_covered": list(NOT_COVERED),
        }
        receipts = 0
        headers: dict[int, eth.BlockHeader] = {}
        given_up: list[int] = []
        stop: str | None = None
        try:
            fin = adapter.finalized_block()
            if fin is None:
                m["status"] = "blocked"
                m["message"] = (
                    "No provider serves the finalized tag, so nothing is read: BIP does not "
                    "collect provisional blocks."
                )
                return self._finish(m, adapter, budget)
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
                return self._finish(m, adapter, budget)
            attempts: dict[str, int] = json.loads(self._repo.get_meta(ATTEMPTS_KEY) or "{}")
            last_hash = self._repo.get_meta(HASH_KEY) if raw_cursor else None
            limit = cfg.max_rpc_calls_per_run - cfg.feed_call_reserve
            for number in range(start, end + 1):
                if budget.calls >= limit:
                    stop = "Call budget kept for the event feeds; the rest resumes next run."
                    break
                try:
                    pair = adapter.block_full(number)
                    if pair is None:
                        stop = f"Block {number} not returned by the provider; stopped there."
                        break
                    header, txs, src = pair
                    wanted = self._wanted(txs, min_wei, m)
                    room = min(cfg.max_receipts_per_run - receipts, limit - budget.calls)
                    if len(wanted) > room and (headers or receipts):
                        stop = (
                            f"Block {number} needs more receipts than are left; resumes next run."
                        )
                        break
                    if len(wanted) > room:  # first block of a run: take what fits, record the rest
                        self._record_gap(
                            number,
                            number,
                            f"{len(wanted) - max(room, 0)} qualifying transaction(s) not stored "
                            "(receipt cap or call budget)",
                        )
                        m["skipped_over_receipt_cap"] += len(wanted) - max(room, 0)
                        wanted = wanted[: max(room, 0)]
                    receipts += self._scan_block(adapter, header, src, wanted, m)
                except BudgetExhaustedError:
                    raise
                except (RpcError, ParseError) as exc:
                    n = attempts.get(str(number), 0) + 1
                    attempts[str(number)] = n
                    self._repo.set_meta(ATTEMPTS_KEY, json.dumps(attempts))
                    if n < cfg.max_block_attempts:
                        stop = (
                            f"Block {number} failed ({type(exc).__name__}); retried next run "
                            f"({n}/{cfg.max_block_attempts})."
                        )
                        break
                    self._record_gap(number, number, f"given up after {n} failed reads")
                    m["blocks_given_up"] += 1
                    given_up.append(number)
                    attempts.pop(str(number), None)
                    self._repo.set_meta(ATTEMPTS_KEY, json.dumps(attempts))
                    headers.pop(number, None)
                    continue
                attempts.pop(str(number), None)
                if last_hash is not None and header.parent_hash != last_hash and number == start:
                    m["continuity_mismatches"] += 1
                if (
                    prev := headers.get(number - 1)
                ) is not None and header.parent_hash != prev.hash:
                    m["continuity_mismatches"] += 1
                last_hash = header.hash
                headers[number] = header
                m["blocks_scanned"] += 1
                m["first_block"] = m["first_block"] if m["first_block"] is not None else number
                m["last_block"] = number
                m["transactions_seen"] += len(txs)
            if stop is not None:
                m["status"] = "partial"
                m["message"] = stop
            self._advance(adapter, headers, given_up, m)
        except BudgetExhaustedError as exc:
            m["status"] = "partial"
            m["message"] = f"Stopped early: {exc}. The unfinished range is retried next run."
            self._advance_after_failure(adapter, headers, given_up, m)
        except NoEligibleProviderError as exc:
            m["status"] = "blocked"
            m["message"] = redact_text(f"No eligible provider: {exc}")[:300]
            self._advance_after_failure(adapter, headers, given_up, m)
        except (RoutingFailedError, RpcError, TransportError, ParseError) as exc:
            m["status"] = "partial" if m["blocks_scanned"] else "failed"
            m["message"] = redact_text(f"{type(exc).__name__}: {exc}")[:300]
            self._advance_after_failure(adapter, headers, given_up, m)
        except Exception as exc:  # the manifest must always be written
            log.exception("collector run failed unexpectedly")
            m["status"] = "failed"
            m["message"] = f"unexpected {type(exc).__name__}"
        if not m["message"]:
            m["message"] = (
                f"Scanned {m['blocks_scanned']} finalized block(s): "
                f"{m['contract_creations_stored']} creation(s), "
                f"{m['large_transfers_stored']} large transfer(s), "
                f"{m['pool_events_stored']} new pool(s), {m['new_tokens']} new token(s), "
                f"{m['reserve_updates_stored']} reserve update(s)."
            )
        return self._finish(m, adapter, budget)

    def _finish(
        self, m: dict[str, Any], adapter: EthereumAdapter, budget: CallBudget
    ) -> dict[str, Any]:
        m["rpc_calls"] = budget.calls
        m["providers"] = sorted(adapter.providers_used)
        m["finished_at"] = utcnow().isoformat()
        self._repo.set_meta(LAST_RUN_KEY, json.dumps(m))
        return m

    # ------------------------------------------------------------------------------ helpers

    def _record_gap(self, first: int, last: int, reason: str) -> None:
        """Persist a range BIP knowingly did not store, so the manifest of a later run cannot
        make it look covered."""
        gaps: list[Any] = json.loads(self._repo.get_meta(GAPS_KEY) or "[]")
        gaps.append(
            {"from_block": first, "to_block": last, "reason": reason, "at": utcnow().isoformat()}
        )
        self._repo.set_meta(GAPS_KEY, json.dumps(gaps[-MAX_GAPS_KEPT:]))

    def _advance(
        self,
        adapter: EthereumAdapter,
        headers: dict[int, eth.BlockHeader],
        given_up: list[int],
        m: dict[str, Any],
    ) -> None:
        """Run the event feeds over the blocks completed so far, then move the cursor.

        The cursor goes to the last block that was completed or given up (and recorded as a gap),
        never past a block that is still being retried.
        """
        if not headers and not given_up:
            return
        if headers:
            self._feeds.run(adapter, min(headers), max(headers), headers, m)
        new_cursor = max([*headers, *given_up])
        self._repo.set_meta(CURSOR_KEY, str(new_cursor))
        if headers:
            self._repo.set_meta(HASH_KEY, headers[max(headers)].hash)

    def _advance_after_failure(
        self,
        adapter: EthereumAdapter,
        headers: dict[int, eth.BlockHeader],
        given_up: list[int],
        m: dict[str, Any],
    ) -> None:
        """A run-level failure after some blocks were stored: finish the feeds for them if the
        provider still answers; otherwise leave the cursor alone (a rescan is idempotent)."""
        try:
            self._advance(adapter, headers, given_up, m)
        except (BudgetExhaustedError, NoEligibleProviderError, RoutingFailedError, RpcError):
            m["message"] += " Event feeds for blocks already stored did not finish; retried."
        except (TransportError, ParseError):
            m["message"] += " Event feeds for blocks already stored did not finish; retried."

    def _wanted(
        self, txs: list[object], min_wei: int, m: dict[str, Any]
    ) -> list[tuple[object, str, bool]]:
        """Transactions worth a receipt: creations and transfers of at least the threshold."""
        out: list[tuple[object, str, bool]] = []
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
            out.append((raw, txh, creation))
        return out

    def _scan_block(
        self,
        adapter: EthereumAdapter,
        header: eth.BlockHeader,
        src: Any,
        wanted: list[tuple[object, str, bool]],
        m: dict[str, Any],
    ) -> int:
        """Store one block and its wanted transactions; return receipts fetched.

        A transaction lost here (no receipt, unparseable, hash mismatch) is counted in the
        manifest and recorded as a gap for the block.
        """
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
        lost = 0
        for raw, txh, creation in wanted:
            receipt, rsrc = adapter.receipt(txh)
            if receipt is None:
                m["receipt_missing"] += 1
                lost += 1
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
                lost += 1
                continue
            if parsed.tx.block_hash != header.hash:
                m["rejected"] += 1  # receipt/tx disagree with the block just read
                lost += 1
                continue
            tx = parsed.tx
            persist_transaction(self._repo, parsed)
            if creation:
                if tx.created_contract is not None and tx.status is not TxStatus.FAILED:
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
        if lost:
            self._record_gap(
                header.number,
                header.number,
                f"{lost} qualifying transaction(s) could not be stored",
            )
        return len(wanted)
