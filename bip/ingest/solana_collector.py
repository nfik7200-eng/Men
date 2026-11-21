"""Bounded Solana finalized-slot collector: new mints, programs, pools and large SOL transfers.

Wallet ingestion needs an address. This reads **finalized slots** in order (one ``getBlock`` per
slot, compiled ``json`` encoding, transaction versions 0 and 1) and keeps only:

* new token mints (SPL Token and Token-2022 ``InitializeMint``/``InitializeMint2``),
* new programs (upgradeable-loader ``DeployWithMaxDataLen``),
* new pools of the two AMM layouts in :mod:`bip.chains.solana.programs`, and
* System Program transfers of at least ``solana_collector.min_sol_transfer`` SOL.

Everything else in a block is counted and dropped. Failed transactions apply no instruction and
are never decoded. The cursor (last scanned slot and its blockhash) lives in ``app_metadata``.
Consecutive slots are checked for blockhash continuity; skipped or unavailable slots are counted
(the node cannot tell them apart). When the cursor falls further behind the head than
``max_lag_slots``, the collector jumps to the head and records the skipped range as a gap, so the
manifest never implies contiguous coverage it does not have.

Not covered: pools of other programs, SPL transfers, token holders and balances, program upgrades,
and anything older than the cursor. No raw block payload is retained (disk constraint).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import insert, select

from bip.chains.common import ParseError
from bip.chains.solana import parse as sol
from bip.chains.solana.adapter import SolanaAdapter
from bip.chains.solana.programs import (
    KNOWN_PROGRAMS,
    as_dict,
    as_list,
    decode_discoveries,
    system_transfers,
)
from bip.config.settings import Settings
from bip.contracts.provenance import Finality
from bip.core.logging import get_logger
from bip.db import schema
from bip.db.checkpoints import CheckpointStatus, CheckpointStore, CoverageStatus, StreamRef
from bip.db.errors import DatabaseError
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
from bip.security.secrets import redact_text

log = get_logger("bip.solana_collector")

CURSOR_KEY = "collector:solana:last_slot"
HASH_KEY = "collector:solana:last_blockhash"
"""Stored as ``<slot>:<blockhash>`` so continuity can be checked across runs."""
GAPS_KEY = "collector:solana:gaps"
GAPS_EVICTED_KEY = "collector:solana:gaps_evicted"
ATTEMPTS_KEY = "collector:solana:attempts"
LAST_RUN_KEY = "collector:solana:last_run"
COLLECTOR_VERSION = "1"
MAX_GAPS_KEPT = 50
STREAM = StreamRef("solana", "collector")
LAMPORTS_PER_SOL = 10**9

NOT_COVERED = (
    "pools created by programs other than Raydium AMM v4 and Orca Whirlpools (initialize_pool)",
    "SPL token transfers of mints outside solana_collector.spl_mint_allowlist (none stored when "
    "it is empty); token-account owners are not resolved; Token-2022 TransferCheckedWithFee and "
    "other extension instructions are not decoded",
    "SPL holders, balances and liquidity/reserve changes",
    "program upgrades and any slot before the cursor",
    "ordinary transactions (counted, not stored)",
)


def _deterministic(exc: BaseException) -> bool:
    """Would the same request fail again for the same reason? Only then does it count as an
    attempt against a slot; an outage or a rate limit must not make BIP give a slot up."""
    if isinstance(exc, ParseError):
        return True
    if isinstance(exc, RpcError):
        return exc.error_class in {"not_available", "invalid_request", "malformed"}
    if isinstance(exc, RoutingFailedError):
        text = str(exc)
        return "too_large" in text and "timeout" not in text and "connection" not in text
    return False


def _token_mints(meta: dict[str, Any]) -> set[str]:
    """Mints that appear in a transaction's token balances (a cheap pre-filter: a transfer of a
    mint always shows that mint in the pre or post token balances)."""
    out: set[str] = set()
    for key in ("preTokenBalances", "postTokenBalances"):
        for b in as_list(meta.get(key)) or []:
            entry = as_dict(b)
            if entry is not None and isinstance(entry.get("mint"), str):
                out.add(str(entry["mint"]))
    return out


class SolanaCollector:
    def __init__(
        self,
        router: ProviderRouter,
        repo: Repository,
        settings: Settings,
        *,
        legacy_gaps: bool = True,
    ) -> None:
        self._router = router
        self._repo = repo
        self._cfg = settings.solana_collector
        self._checkpoints = CheckpointStore(repo)
        self._mints = frozenset(self._cfg.spl_mint_allowlist)
        self._legacy_gaps = legacy_gaps
        """The live collector also keeps the old ``collector:solana:gaps`` list; the backfill,
        which reuses the per-slot scan, records coverage only."""
        self._spl_capped_reported = 0

    def status(self) -> dict[str, Any]:
        raw = self._repo.get_meta(LAST_RUN_KEY)
        cursor = self._repo.get_meta(CURSOR_KEY)
        gaps = self._repo.get_meta(GAPS_KEY)
        return {
            "version": COLLECTOR_VERSION,
            "chain": "solana",
            "last_slot_scanned": int(cursor) if cursor else None,
            "last_run": json.loads(raw) if raw else None,
            "coverage_gaps": json.loads(gaps) if gaps else [],
            "coverage_gaps_evicted": int(self._repo.get_meta(GAPS_EVICTED_KEY) or 0),
            "limits": {
                "blocks_per_run": self._cfg.blocks_per_run,
                "max_lag_slots": self._cfg.max_lag_slots,
                "min_sol_transfer": self._cfg.min_sol_transfer,
                "max_rpc_calls_per_run": self._cfg.max_rpc_calls_per_run,
                "run_deadline_s": self._cfg.run_deadline_s,
            },
            "recognised_programs": [
                {"program_id": p.program_id, "name": p.name, "source_url": p.source_url}
                | {"verified_at": p.verified_at}
                for p in KNOWN_PROGRAMS
            ],
            "not_covered": list(NOT_COVERED),
        }

    # ------------------------------------------------------------------------------- run

    def run(self) -> dict[str, Any]:
        """Scan the next finalized slots once; always returns a manifest, never raises."""
        cfg = self._cfg
        self._spl_capped_reported = 0
        budget = CallBudget.create(cfg.max_rpc_calls_per_run, cfg.run_deadline_s)
        adapter = SolanaAdapter(self._router, budget=budget)
        m: dict[str, Any] = {
            "status": "succeeded",
            "started_at": utcnow().isoformat(),
            "slots_scanned": 0,
            "slots_skipped": 0,
            "unknown_status_transactions": 0,
            "slots_given_up": 0,
            "transactions_seen": 0,
            "failed_transactions": 0,
            "mints_found": 0,
            "programs_found": 0,
            "pools_found": 0,
            "discoveries_stored": 0,
            "large_transfers_stored": 0,
            "spl_transfers_stored": 0,
            "spl_transfers_over_cap": 0,
            "stored_transactions_over_cap": 0,
            "rejected": 0,
            "continuity_mismatches": 0,
            "gap_recorded": None,
            "first_slot": None,
            "last_slot": None,
            "finalized_head": None,
            "lag_slots": None,
            "message": "",
            "not_covered": list(NOT_COVERED),
        }
        stored_txs = 0
        error_category: str | None = None
        if self._checkpoints.is_paused(STREAM, "live"):
            m["status"] = "paused"
            m["message"] = "Live collection is paused by an operator; nothing was read."
            return self._finish(m, adapter)
        self._checkpoints.start_run(STREAM, "live", decoder_version=COLLECTOR_VERSION)
        try:
            head = adapter.slot("finalized")
            m["finalized_head"] = head
            raw_cursor = self._repo.get_meta(CURSOR_KEY)
            fresh = max(0, head - cfg.blocks_per_run + 1)
            prev: tuple[int, str] | None = None
            if raw_cursor is None:
                start = fresh
            elif head - int(raw_cursor) > cfg.max_lag_slots and fresh > int(raw_cursor) + 1:
                # Too far behind and the head is out of reach of one run: jump, and record the
                # slots passed over as skipped (never as read or empty), in one transaction with
                # the cursor move. The old blockhash no longer links to the first slot read.
                start = fresh
                reason = "lag exceeded max_lag_slots"
                self._checkpoints.skip_to(
                    STREAM, start - 1, decoder_version=COLLECTOR_VERSION, reason=reason
                )
                self._record_gap(int(raw_cursor) + 1, start - 1, reason, m, CoverageStatus.SKIPPED)
                self._repo.set_meta(CURSOR_KEY, str(start - 1))
                self._repo.set_meta(HASH_KEY, "")
            else:
                start = int(raw_cursor) + 1
                stored_hash = self._repo.get_meta(HASH_KEY) or ""
                head_slot, _, h = stored_hash.partition(":")
                prev = (int(head_slot), h) if head_slot.isdecimal() and h else None
            end = min(head, start + cfg.blocks_per_run - 1)
            if start > end:
                m["lag_slots"] = head - int(raw_cursor or end)
                m["message"] = "Up to date with the finalized head."
                return self._finish(m, adapter)
            attempts: dict[str, int] = json.loads(self._repo.get_meta(ATTEMPTS_KEY) or "{}")
            for slot in range(start, end + 1):
                try:
                    pair = adapter.block_full(slot)
                except (RoutingFailedError, RpcError, TransportError, ParseError) as exc:
                    if not _deterministic(exc):
                        # A provider outage must never burn the slot's attempts: stop and retry.
                        m["status"] = "partial"
                        m["message"] = redact_text(f"{type(exc).__name__}: {exc}")[:300]
                        error_category = "provider_unavailable"
                        break
                    n = attempts.get(str(slot), 0) + 1
                    attempts[str(slot)] = n
                    self._repo.set_meta(ATTEMPTS_KEY, json.dumps(attempts))
                    if n < cfg.max_slot_attempts:
                        m["status"] = "partial"
                        m["message"] = (
                            f"Slot {slot} failed ({type(exc).__name__}); retried next run "
                            f"({n}/{cfg.max_slot_attempts})."
                        )
                        error_category = "read_failed"
                        break
                    self._record_gap(
                        slot, slot, f"given up after {n} failed reads", m, CoverageStatus.FAILED
                    )
                    m["slots_given_up"] += 1
                    attempts.pop(str(slot), None)
                    self._repo.set_meta(ATTEMPTS_KEY, json.dumps(attempts))
                    self._advance(slot, None, adapter)
                    continue
                attempts.pop(str(slot), None)
                if pair is None:
                    m["slots_skipped"] += 1  # the node said the slot was skipped (-32007)
                    self._advance(slot, None, adapter, CoverageStatus.CONFIRMED_EMPTY)
                    continue
                block, src = pair
                blockhash = str(block["blockhash"])
                if (
                    prev is not None
                    and block.get("parentSlot") == prev[0]
                    and block.get("previousBlockhash") != prev[1]
                ):
                    m["continuity_mismatches"] += 1
                stored_txs += self._scan_block(slot, block, src, stored_txs, m)
                prev = (slot, blockhash)
                self._advance(
                    slot,
                    f"{slot}:{blockhash}",
                    adapter,
                    CoverageStatus.PROCESSED,
                    src.provider_id,
                )
                m["slots_scanned"] += 1
                m["first_slot"] = m["first_slot"] if m["first_slot"] is not None else slot
                m["last_slot"] = slot
        except BudgetExhaustedError as exc:
            m["status"] = "partial"
            m["message"] = f"Stopped early: {exc}. The cursor keeps the last finished slot."
        except NoEligibleProviderError as exc:
            m["status"] = "blocked"
            m["message"] = redact_text(f"No eligible provider: {exc}")[:300]
            error_category = "no_eligible_provider"
        except (RoutingFailedError, RpcError, TransportError, ParseError) as exc:
            m["status"] = "partial" if m["slots_scanned"] else "failed"
            m["message"] = redact_text(f"{type(exc).__name__}: {exc}")[:300]
            error_category = "provider_unavailable"
        except Exception as exc:  # the manifest must always be written
            log.exception("solana collector run failed unexpectedly")
            m["status"] = "failed"
            m["message"] = f"unexpected {type(exc).__name__}"
            error_category = "internal_error"
        if error_category is not None:
            try:
                self._checkpoints.mark(
                    STREAM,
                    "live",
                    CheckpointStatus.BLOCKED
                    if error_category == "no_eligible_provider"
                    else CheckpointStatus.STALLED,
                    decoder_version=COLLECTOR_VERSION,
                    error_category=error_category,
                    detail=m["message"],
                    providers=adapter.providers_used,
                )
            except DatabaseError:
                log.warning("could not record the solana collector checkpoint status")
        if not m["message"]:
            m["message"] = (
                f"Scanned {m['slots_scanned']} finalized slot(s) "
                f"({m['slots_skipped']} skipped): "
                f"{m['mints_found']} mint(s), {m['programs_found']} program(s), "
                f"{m['pools_found']} pool(s), {m['large_transfers_stored']} large transfer(s)."
            )
        return self._finish(m, adapter)

    def _finish(self, m: dict[str, Any], adapter: SolanaAdapter) -> dict[str, Any]:
        m["rpc_calls"] = adapter.calls
        raw_cursor = self._repo.get_meta(CURSOR_KEY)
        if m["finalized_head"] is not None and raw_cursor is not None:
            m["lag_slots"] = max(0, m["finalized_head"] - int(raw_cursor))
        m["providers"] = sorted(adapter.providers_used)
        m["finished_at"] = utcnow().isoformat()
        self._repo.set_meta(LAST_RUN_KEY, json.dumps(m))
        try:
            self._checkpoints.finish_run(STREAM, "live", head_seen=m["finalized_head"])
        except DatabaseError:
            log.warning("could not finish the solana collector checkpoint run")
        return m

    def _advance(
        self,
        slot: int,
        blockhash: str | None,
        adapter: SolanaAdapter,
        status: CoverageStatus | None = None,
        provider_id: str | None = None,
    ) -> None:
        """Move past ``slot`` after its data is committed. ``status`` is recorded as coverage
        (``None`` when the slot was already recorded as a gap)."""
        with self._repo.transaction() as conn:
            if status is not None:
                self._checkpoints.record_range(
                    STREAM,
                    slot,
                    slot,
                    status,
                    decoder_version=COLLECTOR_VERSION,
                    provider_id=provider_id,
                    conn=conn,
                )
            self._checkpoints.commit_batch(
                STREAM,
                "live",
                from_pos=slot,
                to_pos=slot,
                decoder_version=COLLECTOR_VERSION,
                cursor_hash=blockhash.partition(":")[2] if blockhash else None,
                providers=adapter.providers_used,
                conn=conn,
            )
        self._repo.set_meta(CURSOR_KEY, str(slot))
        if blockhash is not None:
            self._repo.set_meta(HASH_KEY, blockhash)

    def _record_gap(
        self,
        first: int,
        last: int,
        reason: str,
        m: dict[str, Any],
        status: CoverageStatus = CoverageStatus.INCOMPLETE,
    ) -> None:
        if last < first:
            return
        self._checkpoints.record_range(
            STREAM, first, last, status, decoder_version=COLLECTOR_VERSION, reason=reason
        )
        if not self._legacy_gaps:
            return
        gaps: list[Any] = json.loads(self._repo.get_meta(GAPS_KEY) or "[]")
        gaps.append(
            {"from_slot": first, "to_slot": last, "reason": reason, "at": utcnow().isoformat()}
        )
        if len(gaps) > MAX_GAPS_KEPT:
            evicted = int(self._repo.get_meta(GAPS_EVICTED_KEY) or 0) + len(gaps) - MAX_GAPS_KEPT
            self._repo.set_meta(GAPS_EVICTED_KEY, str(evicted))
        self._repo.set_meta(GAPS_KEY, json.dumps(gaps[-MAX_GAPS_KEPT:]))
        m["gap_recorded"] = {"from_slot": first, "to_slot": last, "reason": reason}

    # ------------------------------------------------------------------------------ block

    def _scan_block(
        self, slot: int, block: dict[str, object], src: Any, stored_so_far: int, m: dict[str, Any]
    ) -> int:
        """Handle one block's transactions; return how many transactions were stored.

        Discovery rows (a mint, program or pool) are tiny and are always stored. The per-run cap
        applies to storing whole transactions and large transfers; what it drops is recorded.
        """
        cfg = self._cfg
        txs: list[Any] = list(block["transactions"])  # type: ignore[call-overload]
        block_time = block.get("blockTime")
        blockhash = str(block["blockhash"])
        min_lamports = cfg.min_sol_transfer * LAMPORTS_PER_SOL
        stored = 0
        dropped = 0
        spl_dropped = 0
        for tx in txs:
            m["transactions_seen"] += 1
            if not isinstance(tx, dict):
                m["rejected"] += 1
                continue
            entry: dict[str, Any] = tx  # pyright: ignore[reportUnknownVariableType]
            meta = as_dict(entry.get("meta"))
            if meta is None:
                m["unknown_status_transactions"] += 1  # never assume success without a status
                continue
            if meta.get("err") is not None:
                m["failed_transactions"] += 1
                continue
            found = decode_discoveries(entry)
            big = [t for t in system_transfers(entry) if t[2] >= min_lamports]
            spl = bool(self._mints) and bool(self._mints & _token_mints(meta))
            if not found and not big and not spl:
                continue
            for d in found:
                m[
                    {"mint": "mints_found", "program": "programs_found", "pool": "pools_found"}[
                        d.kind
                    ]
                ] += 1
            wrapped = {
                "slot": slot,
                "blockTime": block_time,
                "version": entry.get("version", "legacy"),
                "transaction": entry.get("transaction"),
                "meta": entry.get("meta"),
            }
            payer = (big[0][0] if big else None) or self._fee_payer(entry)
            try:
                parsed = sol.parse_transaction(
                    wrapped, wallet=payer or "", src=src, commitment="finalized"
                )
            except ParseError:
                m["rejected"] += 1
                continue
            if stored_so_far + stored >= cfg.max_stored_transactions_per_run:
                m["stored_transactions_over_cap"] += 1
                if big:
                    dropped += 1
                elif spl:
                    spl_dropped += 1
                self._store_discoveries(parsed, blockhash, found, m)  # tiny: never capped
                continue
            self._store(parsed, blockhash, found, m)
            stored += 1
        if m["spl_transfers_over_cap"] > self._spl_capped_reported:
            self._record_gap(slot, slot, "SPL transfer(s) not stored (per-run cap)", m)
            self._spl_capped_reported = m["spl_transfers_over_cap"]
        if spl_dropped:
            m["spl_transactions_over_cap"] = m.get("spl_transactions_over_cap", 0) + spl_dropped
            self._record_gap(
                slot,
                slot,
                "transaction(s) with allowlisted SPL transfers not stored (per-run cap)",
                m,
            )
        if dropped:
            self._record_gap(
                slot, slot, f"{dropped} large SOL transfer(s) not stored (per-run cap)", m
            )
        return stored

    def scan_block(self, slot: int, block: dict[str, object], src: Any, m: dict[str, Any]) -> int:
        """Public for the backfill and recovery: handle one finalized block's transactions
        (idempotent, no cursor). Returns how many transactions were stored."""
        self._spl_capped_reported = m.get("spl_transfers_over_cap", 0)
        return self._scan_block(slot, block, src, m.get("_stored", 0), m)

    @staticmethod
    def _fee_payer(entry: dict[str, Any]) -> str | None:
        message = as_dict((as_dict(entry.get("transaction")) or {}).get("message")) or {}
        keys = as_list(message.get("accountKeys")) or []
        return str(keys[0]) if keys else None

    def _store(
        self, parsed: sol.ParsedSolanaTx, blockhash: str, found: list[Any], m: dict[str, Any]
    ) -> None:
        tx = parsed.tx
        self._repo.store_transaction(
            tx,
            extras={
                "network": "mainnet-beta",
                "finality": Finality.FINALIZED.value,
                "parse_status": parsed.parse_status,
                "fee_raw": str(tx.fee_lamports) if tx.fee_lamports is not None else None,
                "instruction_count": parsed.instruction_count,
                "unknown_instruction_count": parsed.unknown_instruction_count,
                "program_ids_json": json.dumps(list(parsed.program_ids)),
                "raw_ref": None,
                "run_id": None,
            },
        )
        for transfer, kind in parsed.transfers:
            if transfer.standard.value != "native":
                mint = transfer.token.address if transfer.token is not None else None
                if mint is None or mint not in self._mints:
                    continue  # only allowlisted mints are collected
                if m["spl_transfers_stored"] >= self._cfg.max_spl_transfers_per_run:
                    m["spl_transfers_over_cap"] += 1
                    continue
                if self._repo.store_transfer(
                    transfer,
                    extras={"observation_kind": kind, "block_time": tx.block_time, "raw_ref": None},
                ):
                    m["spl_transfers_stored"] += 1
                continue
            lamports = transfer.amount_raw
            if (
                lamports >= self._cfg.min_sol_transfer * LAMPORTS_PER_SOL
                and self._repo.store_transfer(
                    transfer,
                    extras={"observation_kind": kind, "block_time": tx.block_time, "raw_ref": None},
                )
            ):
                m["large_transfers_stored"] += 1
        self._store_discoveries(parsed, blockhash, found, m)

    def _store_discoveries(
        self, parsed: sol.ParsedSolanaTx, blockhash: str, found: list[Any], m: dict[str, Any]
    ) -> None:
        tx = parsed.tx
        sd = schema.solana_discoveries
        for d in found:
            with self._repo.transaction() as conn:
                known = conn.execute(
                    select(sd.c.id).where(sd.c.kind == d.kind, sd.c.ref == d.ref)
                ).first()
                if known is not None:
                    continue
                conn.execute(
                    insert(sd).values(
                        kind=d.kind,
                        ref=d.ref,
                        slot=tx.slot,
                        blockhash=blockhash,
                        signature=tx.signature,
                        block_time=tx.block_time,
                        detail_json=json.dumps(dict(d.detail)),
                        finality=Finality.FINALIZED.value,
                        stored_at=utcnow(),
                    )
                )
            m["discoveries_stored"] += 1
