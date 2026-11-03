"""Bounded, resumable, read-only wallet ingestion for Ethereum and Solana.

One call to :meth:`WalletIngestor.run` performs one *run*:
- validates chain and address with BIP contracts;
- bounds work by ``limit`` (≤ ``ingest.max_limit``), an RPC call budget and a deadline;
- persists everything with provenance and raw-payload references;
- records every failure in ``ingestion_errors`` (nothing is silently dropped);
- advances a per-wallet cursor, so repeated or interrupted runs continue instead of restarting.

Run status: ``succeeded`` (finished, no errors), ``partial`` (stopped early by budget/deadline
or some items failed but data was stored), ``failed`` (nothing usable retrieved).

**Coverage is never claimed to be complete.** Ethereum has no address index in standard RPC:
activity is discovered only through ``Transfer`` logs (ERC-20/721) in the scanned block window,
so plain ETH transfers without logs and internal transfers are *not* discovered. Solana uses
``getSignaturesForAddress``, bounded to the most recent ``limit`` signatures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from bip.chains.common import ParseError, SourceInfo
from bip.chains.ethereum import parse as eth
from bip.chains.ethereum.adapter import EthereumAdapter
from bip.chains.solana import parse as sol
from bip.chains.solana.adapter import SolanaAdapter
from bip.config.settings import Settings
from bip.contracts.blockchain import SlotPosition, family_of
from bip.contracts.identifiers import SolanaTxRef
from bip.contracts.provenance import Finality, IngestionMethod, Provenance, SourceKind
from bip.core.logging import get_logger
from bip.db.observations import Cursor, ObservationStore
from bip.db.repository import Repository, canonical_address
from bip.router.router import (
    BudgetExhaustedError,
    CallBudget,
    NoEligibleProviderError,
    ProviderRouter,
    RoutingFailedError,
)
from bip.rpc.jsonrpc import RpcError
from bip.rpc.transport import TransportError

log = get_logger("bip.ingest")


@dataclass
class RunStats:
    transactions_seen: int = 0
    transactions_stored: int = 0
    transfers_stored: int = 0
    balance_changes_stored: int = 0
    errors: int = 0
    stopped_early: str | None = None
    coverage: dict[str, object] = field(default_factory=dict[str, object])


class WalletIngestor:
    def __init__(self, router: ProviderRouter, repo: Repository, settings: Settings) -> None:
        self._router = router
        self._repo = repo
        self._store = ObservationStore(repo)
        self._cfg = settings.ingest

    @property
    def store(self) -> ObservationStore:
        return self._store

    def run(self, chain: str, address: str, limit: int | None = None) -> int:
        ref = canonical_address(chain, address)
        n = min(limit if limit is not None else self._cfg.default_limit, self._cfg.max_limit)
        if n < 1:
            n = 1
        run_id = self._store.start_run(ref.chain, ref.address, n)
        budget = CallBudget.create(self._cfg.max_rpc_calls_per_run, self._cfg.run_deadline_s)
        stats = RunStats()
        adapter: EthereumAdapter | SolanaAdapter | None = None
        message = ""
        try:
            if family_of(ref.chain).value == "evm":
                adapter = EthereumAdapter(self._router, raw_sink=self._store, budget=budget)
                self._run_ethereum(run_id, adapter, ref.address, n, stats)
            else:
                adapter = SolanaAdapter(self._router, raw_sink=self._store, budget=budget)
                self._run_solana(run_id, adapter, ref.address, n, stats)
        except BudgetExhaustedError as exc:
            stats.stopped_early = str(exc)
            self._error(run_id, stats, "run", "budget_exhausted", str(exc))
        except NoEligibleProviderError as exc:
            self._error(run_id, stats, "routing", "no_eligible_provider", str(exc))
            message = "No eligible provider."
        except (RoutingFailedError, RpcError, TransportError) as exc:
            self._error(run_id, stats, "rpc", type(exc).__name__, str(exc))
        except ParseError as exc:
            self._error(run_id, stats, "parse", "parse_error", str(exc))
        except Exception as exc:
            log.exception("ingestion run %s failed unexpectedly", run_id)
            self._error(run_id, stats, "run", "internal_error", f"unexpected {type(exc).__name__}")
        stored_any = (
            stats.transactions_stored + stats.transfers_stored + stats.balance_changes_stored
        ) > 0
        retrieved_any = bool(stats.coverage.get("head_retrieved")) or stats.transactions_seen > 0
        if stats.errors == 0 and stats.stopped_early is None:
            status = "succeeded"
        elif stored_any or retrieved_any:
            status = "partial"  # something real was retrieved, but the run is incomplete
        else:
            status = "failed"
        if not message:
            message = (
                f"{stats.transactions_stored} new transactions, "
                f"{stats.transfers_stored} new transfers, "
                f"{stats.balance_changes_stored} new balance changes "
                f"from {stats.transactions_seen} seen."
            )
            if stats.stopped_early:
                message += f" Stopped early: {stats.stopped_early}."
        self._store.finish_run(
            run_id,
            status=status,
            providers=sorted(adapter.providers_used) if adapter else [],
            rpc_calls=adapter.calls if adapter else 0,
            counts={
                "transactions_seen": stats.transactions_seen,
                "transactions_stored": stats.transactions_stored,
                "transfers_stored": stats.transfers_stored,
                "balance_changes_stored": stats.balance_changes_stored,
            },
            coverage=stats.coverage,
            message=message,
        )
        return run_id

    def _error(
        self,
        run_id: int,
        stats: RunStats,
        stage: str,
        code: str,
        detail: str,
        item: str | None = None,
    ) -> None:
        stats.errors += 1
        log.warning("ingestion run %s %s/%s: %s", run_id, stage, code, detail)
        self._store.add_error(run_id, stage=stage, error_code=code, detail=detail, item_ref=item)

    # ================================================================== Ethereum

    def _run_ethereum(
        self, run_id: int, a: EthereumAdapter, wallet: str, limit: int, stats: RunStats
    ) -> None:
        head_pair = a.block("latest")
        if head_pair is None:
            raise ParseError("eth_getBlockByNumber", "provider returned no latest block")
        head, head_src = head_pair
        stats.coverage["head_retrieved"] = True
        finalized_pair = a.finalized_block()
        finalized = finalized_pair[0].number if finalized_pair else None
        self._store_eth_block(head, head_src, finalized)
        self._store.record_network_observation(
            chain="ethereum",
            network="mainnet",
            provider_id=head_src.provider_id,
            identity="chain id 1",
            head_block=head.number,
            head_hash=head.hash,
            head_time=head.timestamp,
            finalized_block=finalized,
        )
        balance = a.balance(wallet)
        nonce = a.transaction_count(wallet)
        stats.coverage.update(
            {
                "balance_wei_at_head": str(balance),
                "outgoing_tx_count_at_head": nonce,
                "head_block": head.number,
                "finalized_block": finalized,
                "method": "Transfer logs (ERC-20/721) where the wallet is sender or recipient",
                "not_covered": [
                    "plain ETH transfers that emit no logs "
                    "(found only if the same tx also has a Transfer log)",
                    "internal (contract-initiated) ETH transfers",
                    "activity older than the scanned block window",
                ],
            }
        )

        # Plan the scan newest-first and skip the already-scanned contiguous interval.
        cur = self._store.cursor("ethereum", wallet)
        lower = max(0, head.number - self._cfg.ethereum_lookback_blocks + 1)
        chunk = self._cfg.ethereum_log_chunk_blocks
        windows: list[tuple[int, int]] = []
        top = head.number
        while top >= lower:
            bottom = max(lower, top - chunk + 1)
            windows.append((bottom, top))
            top = bottom - 1
        if cur.scanned_from_block is not None and cur.scanned_to_block is not None:
            windows = _subtract(windows, cur.scanned_from_block, cur.scanned_to_block)

        logs: dict[tuple[str, int], tuple[object, SourceInfo]] = {}
        tx_order: list[str] = []
        tx_block: dict[str, int] = {}
        scanned: list[tuple[int, int]] = []  # windows fully queried in both directions
        notes: list[str] = []
        for bottom, top in windows:
            for direction in ("from", "to"):
                found, split_notes = a.transfer_logs_adaptive(
                    wallet, bottom, top, direction=direction
                )
                notes += split_notes
                for raw, src in found:
                    try:
                        o = eth.obj(raw, "log")
                        key = (
                            str(o.get("blockHash")),
                            eth.position(o.get("logIndex"), "log.logIndex"),
                        )
                        txh = eth.hash32(o.get("transactionHash"), "log.transactionHash")
                        number = eth.position(o.get("blockNumber"), "log.blockNumber")
                    except ParseError as exc:
                        self._error(run_id, stats, "eth_logs", "parse_error", str(exc))
                        continue
                    logs.setdefault(key, (raw, src))
                    if txh not in tx_block:
                        tx_order.append(txh)
                        tx_block[txh] = number
            scanned.append((bottom, top))
            if len(tx_order) >= limit:
                break
        stats.coverage["activity_found"] = bool(tx_order)

        # Newest transactions first. The cursor is advanced only over blocks whose matching
        # transactions were all ingested, so truncated or failed ones are retried next run.
        selected = sorted(tx_order, key=lambda h: tx_block[h], reverse=True)[:limit]
        done: set[str] = set()
        block_cache: dict[int, tuple[eth.BlockHeader, SourceInfo]] = {}
        try:
            for txh in selected:
                stats.transactions_seen += 1
                self._store.link_wallet_activity("ethereum", wallet, txh, "transfer_log", run_id)
                try:
                    if self._ingest_eth_tx(
                        run_id, a, wallet, txh, logs, block_cache, finalized, stats
                    ):
                        done.add(txh)
                except (RpcError, RoutingFailedError, TransportError) as exc:
                    self._error(run_id, stats, "eth_tx", type(exc).__name__, str(exc), txh)
        finally:
            pending = [tx_block[h] for h in tx_order if h not in done]
            ceiling = max(pending) if pending else None  # blocks <= ceiling must be rescanned
            safe = [
                (max(lo, ceiling + 1) if ceiling is not None else lo, hi)
                for lo, hi in scanned
                if ceiling is None or hi > ceiling
            ]
            interval = _merge_top(
                safe,
                (cur.scanned_from_block, cur.scanned_to_block)
                if cur.scanned_from_block is not None and cur.scanned_to_block is not None
                else None,
            )
            if interval is not None:
                self._store.save_cursor(
                    "ethereum",
                    wallet,
                    Cursor(scanned_from_block=interval[0], scanned_to_block=interval[1]),
                    run_id,
                )
            stats.coverage.update(
                {
                    "scanned_from_block": interval[0] if interval else None,
                    "scanned_to_block": interval[1] if interval else None,
                    "pending_transactions": len(pending),
                    "range_notes": notes[:10],
                }
            )

    def _ingest_eth_tx(
        self,
        run_id: int,
        a: EthereumAdapter,
        wallet: str,
        txh: str,
        logs: dict[tuple[str, int], tuple[object, SourceInfo]],
        block_cache: dict[int, tuple[eth.BlockHeader, SourceInfo]],
        finalized: int | None,
        stats: RunStats,
    ) -> bool:
        """Ingest one transaction; True only if it was stored without a blocking problem."""
        raw_tx, tx_src = a.transaction(txh)
        raw_receipt, _ = a.receipt(txh)
        if raw_tx is None:
            self._error(
                run_id, stats, "eth_tx", "not_found", "provider returned null for transaction", txh
            )
            return False
        try:
            number = eth.quantity(eth.obj(raw_tx, "tx").get("blockNumber"), "tx.blockNumber")
            if number not in block_cache:
                pair = a.block(number)
                if pair is None:
                    raise ParseError("block", f"block {number} not returned")
                block_cache[number] = pair
            header, bsrc = block_cache[number]
            finality = (
                Finality.FINALIZED
                if finalized is not None and number <= finalized
                else Finality.OBSERVED
            )
            parsed = eth.parse_transaction(
                raw_tx, raw_receipt, block_time=header.timestamp, src=tx_src, finality=finality
            )
        except ParseError as exc:
            code = "reorg_detected" if exc.field == "receipt.blockHash" else "parse_error"
            self._error(run_id, stats, "eth_tx", code, str(exc), txh)
            return False
        if parsed.tx.block_hash != header.hash:
            self._error(
                run_id,
                stats,
                "eth_tx",
                "reorg_detected",
                f"tx block hash differs from block {header.number} hash; not stored",
                txh,
            )
            return False
        self._store_eth_block(header, bsrc, finalized)
        tx = parsed.tx
        if self._repo.store_transaction(
            tx,
            extras={
                "network": "mainnet",
                "block_hash": tx.block_hash,
                "finality": finality.value,
                "parse_status": "complete" if raw_receipt is not None else "partial",
                "value_raw": str(parsed.value_wei),
                "gas_used": str(parsed.gas_used) if parsed.gas_used is not None else None,
                "fee_raw": str(parsed.gas_used * parsed.gas_price_wei)
                if parsed.gas_used is not None and parsed.gas_price_wei is not None
                else None,
                "raw_ref": tx_src.raw_sha256,
                "run_id": run_id,
            },
        ):
            stats.transactions_stored += 1
        if raw_receipt is None:
            self._error(
                run_id,
                stats,
                "eth_tx",
                "receipt_missing",
                "no receipt: status and logs unknown",
                txh,
            )
        native = eth.native_transfer(parsed)
        if native is not None and self._repo.store_transfer(
            native,
            extras={
                "observation_kind": "native_tx_value",
                "block_time": header.timestamp,
                "raw_ref": tx_src.raw_sha256,
            },
        ):
            stats.transfers_stored += 1
        for (_, _), (raw_log, lsrc) in logs.items():
            if eth.obj(raw_log, "log").get("transactionHash") != txh:
                continue
            try:
                lg = eth.parse_log(raw_log, lsrc, block_time=header.timestamp, finality=finality)
            except ParseError as exc:
                self._error(run_id, stats, "eth_log", "parse_error", str(exc), txh)
                continue
            if lg.block_hash != header.hash:
                self._error(
                    run_id,
                    stats,
                    "eth_log",
                    "reorg_detected",
                    "log block hash differs from block",
                    txh,
                )
                continue
            transfer, reason = eth.decode_transfer_log(lg)
            if transfer is None:
                if reason:
                    self._error(
                        run_id, stats, "eth_log", "unsupported_log", reason, f"{txh}:{lg.log_index}"
                    )
                continue
            kind = (
                "erc20_transfer_log"
                if transfer.standard.value == "erc20"
                else "erc721_transfer_log"
            )
            if self._repo.store_transfer(
                transfer,
                extras={
                    "observation_kind": kind,
                    "block_time": header.timestamp,
                    "raw_ref": lsrc.raw_sha256,
                },
            ):
                stats.transfers_stored += 1
        self._repo.observe_wallet("ethereum", wallet, header.timestamp, tx.provenance)
        return raw_receipt is not None

    def _store_eth_block(self, h: eth.BlockHeader, src: SourceInfo, finalized: int | None) -> None:
        finality = (
            Finality.FINALIZED
            if finalized is not None and h.number <= finalized
            else Finality.OBSERVED
        )
        block = eth.to_block_contract(h, src, finality)
        self._store.store_block(
            chain="ethereum",
            block_hash=h.hash,
            block_number=h.number,
            slot=None,
            parent_hash=h.parent_hash,
            block_time=h.timestamp,
            tx_count=h.tx_count,
            finality=finality.value,
            provenance_json=block.provenance.model_dump_json(),
        )

    # ================================================================== Solana

    def _run_solana(
        self, run_id: int, a: SolanaAdapter, wallet: str, limit: int, stats: RunStats
    ) -> None:
        commitment: sol.Commitment = self._cfg.solana_commitment
        head_slot = a.slot(commitment)
        stats.coverage["head_retrieved"] = True
        provider = sorted(a.providers_used)[0] if a.providers_used else "unknown"
        self._store.record_network_observation(
            chain="solana",
            network="mainnet-beta",
            provider_id=provider,
            identity="mainnet-beta genesis hash",
            head_slot=head_slot,
            commitment=commitment,
        )
        balance = a.balance(wallet, commitment)
        stats.coverage.update(
            {
                "balance_lamports_at_head": str(balance),
                "head_slot": head_slot,
                "commitment": commitment,
                "method": "getSignaturesForAddress (most recent first) + getTransaction",
                "not_covered": [
                    "signatures older than the bounded window",
                    "instructions other than System/SPL transfers are counted, not interpreted",
                ],
            }
        )
        cur = self._store.cursor("solana", wallet)

        def save(
            newest: tuple[str | None, int | None], oldest: tuple[str | None, int | None]
        ) -> None:
            self._store.save_cursor(
                "solana",
                wallet,
                Cursor(
                    newest_tx_id=newest[0],
                    newest_position=newest[1],
                    oldest_tx_id=oldest[0],
                    oldest_position=oldest[1],
                ),
                run_id,
            )

        def ingest(info: sol.SignatureInfo) -> None:
            stats.transactions_seen += 1
            self._store.link_wallet_activity(
                "solana", wallet, info.signature, "signature_list", run_id
            )
            self._ingest_sol_tx(run_id, a, wallet, info, commitment, stats)

        # 1) Newer than the cursor. The cursor's `newest` moves only after the WHOLE batch is
        #    ingested; if the batch filled the limit, a gap may exist below it, so the cursor is
        #    reset to this batch (older ranges are re-walked later; storage dedupes).
        new = a.signatures(wallet, limit=limit, commitment=commitment, until=cur.newest_tx_id)
        stats.coverage["activity_found"] = bool(new) or cur.newest_tx_id is not None
        for info in new:
            ingest(info)
        newest = (cur.newest_tx_id, cur.newest_position)
        oldest = (cur.oldest_tx_id, cur.oldest_position)
        if new:
            top = (new[0].signature, new[0].slot)
            bottom = (new[-1].signature, new[-1].slot)
            if cur.newest_tx_id is None or len(new) >= limit:
                newest, oldest = top, bottom  # fresh range (or possible gap: restart range)
            else:
                newest = top  # contiguous with the previous range
            save(newest, oldest)
        # 2) Older than the cursor, walking backwards; `oldest` advances per ingested tx.
        remaining = limit - len(new)
        if remaining > 0 and oldest[0] is not None:
            for info in a.signatures(
                wallet, limit=remaining, commitment=commitment, before=oldest[0]
            ):
                ingest(info)
                oldest = (info.signature, info.slot)
                save(newest, oldest)

    def _ingest_sol_tx(
        self,
        run_id: int,
        a: SolanaAdapter,
        wallet: str,
        info: sol.SignatureInfo,
        commitment: sol.Commitment,
        stats: RunStats,
    ) -> None:
        try:
            raw, src = a.transaction(info.signature, commitment)
        except RpcError as exc:
            if exc.error_class in {"not_available", "invalid_request"}:
                self._error(run_id, stats, "sol_tx", exc.error_class, str(exc), info.signature)
                if self._store_unparsed_sol(
                    info, None, commitment, "provider did not serve the transaction"
                ):
                    stats.transactions_stored += 1
                return
            raise
        try:
            parsed = sol.parse_transaction(raw, wallet=wallet, src=src, commitment=commitment)
        except ParseError as exc:
            self._error(run_id, stats, "sol_tx", "parse_error", str(exc), info.signature)
            if self._store_unparsed_sol(info, src, commitment, str(exc)):
                stats.transactions_stored += 1
            return
        if parsed.tx.slot != info.slot:
            self._error(
                run_id,
                stats,
                "sol_tx",
                "slot_mismatch",
                "getTransaction slot differs from signature list",
                info.signature,
            )
        for issue in parsed.issues:
            self._error(run_id, stats, "sol_tx", "partial_parse", issue, info.signature)
        tx = parsed.tx
        if self._repo.store_transaction(
            tx,
            extras={
                "network": "mainnet-beta",
                "finality": tx.provenance.finality.value,
                "parse_status": parsed.parse_status,
                "fee_raw": str(tx.fee_lamports) if tx.fee_lamports is not None else None,
                "instruction_count": parsed.instruction_count,
                "unknown_instruction_count": parsed.unknown_instruction_count,
                "program_ids_json": json.dumps(list(parsed.program_ids)),
                "raw_ref": src.raw_sha256,
                "run_id": run_id,
            },
        ):
            stats.transactions_stored += 1
        for transfer, kind in parsed.transfers:
            if self._repo.store_transfer(
                transfer,
                extras={
                    "observation_kind": kind,
                    "block_time": tx.block_time,
                    "raw_ref": src.raw_sha256,
                },
            ):
                stats.transfers_stored += 1
        prov_json = tx.provenance.model_dump_json()
        for bc in parsed.balance_changes:
            if self._store.store_balance_change(
                chain="solana",
                tx_id=tx.signature,
                slot=tx.slot,
                block_time=tx.block_time,
                account=bc.account,
                owner=bc.owner,
                mint=bc.mint,
                kind=bc.kind,
                pre_raw=bc.pre_raw,
                post_raw=bc.post_raw,
                decimals=bc.decimals,
                parse_status=parsed.parse_status,
                provenance_json=prov_json,
            ):
                stats.balance_changes_stored += 1
        self._repo.observe_wallet("solana", wallet, tx.block_time, tx.provenance)

    def _store_unparsed_sol(
        self,
        info: sol.SignatureInfo,
        src: SourceInfo | None,
        commitment: sol.Commitment,
        reason: str,
    ) -> bool:
        """Keep a failed observation visible: signature + slot from the signature list only."""
        from sqlalchemy import insert, select

        from bip.db import schema
        from bip.db.types import utcnow

        prov = Provenance(
            chain="solana",
            source_kind=SourceKind.RPC,
            source_provider=src.provider_id if src else "unknown",
            source_method="getSignaturesForAddress",
            collected_at=utcnow(),
            event_time=info.block_time,
            ingestion_method=IngestionMethod.ON_DEMAND,
            finality=sol.finality_for(commitment),
            schema_version=1,
            position=SlotPosition(slot=info.slot),
            tx=SolanaTxRef(signature=info.signature),
        )
        t = schema.transactions
        with self._repo.transaction() as conn:
            if conn.execute(
                select(t.c.id).where(t.c.chain == "solana", t.c.tx_id == info.signature)
            ).first():
                return False
            conn.execute(
                insert(t).values(
                    chain="solana",
                    tx_id=info.signature,
                    block_number=None,
                    slot=info.slot,
                    block_time=info.block_time,
                    from_address=None,
                    to_address=None,
                    status="failed" if info.failed else None,
                    provenance_json=prov.model_dump_json(),
                    stored_at=utcnow(),
                    network="mainnet-beta",
                    parse_status="failed",
                    finality=prov.finality.value,
                    raw_ref=src.raw_sha256 if src else None,
                )
            )
        return True


def _merge_top(
    intervals: list[tuple[int, int]], previous: tuple[int, int] | None
) -> tuple[int, int] | None:
    """Merge contiguous/overlapping block intervals; return the highest merged one.

    The cursor stores a single contiguous interval, so knowledge of lower, disconnected
    intervals is dropped (those blocks are simply rescanned later; storage dedupes)."""
    items: list[tuple[int, int]] = sorted([*intervals, *([previous] if previous else [])])
    merged: list[tuple[int, int]] = []
    for lo, hi in items:
        if lo > hi:
            continue
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged[-1] if merged else None


def _subtract(windows: list[tuple[int, int]], lo: int, hi: int) -> list[tuple[int, int]]:
    """Remove [lo, hi] from each window, keeping order (newest first)."""
    out: list[tuple[int, int]] = []
    for a, b in windows:
        if b < lo or a > hi:
            out.append((a, b))
            continue
        if b > hi:
            out.append((hi + 1, b))
        if a < lo:
            out.append((a, lo - 1))
    return out
