"""Bounded historical backfill and gap recovery for the Ethereum and Solana collectors (Stage 2).

The live collectors read *forward* from their live checkpoint. This module adds two separate,
independently controllable workers over the same ``collector`` stream:

- **Backfill** reads a small window *backward* from the ``historical`` checkpoint toward a boundary
  that is set once from the target period (180 days of Ethereum, 90 of Solana by default). The
  cursor moves down only after the window's data and event feeds are committed, never past a
  position that is still being retried, and never past history no configured provider serves:
  that stops the worker with a ``provider_unavailable`` range and a ``blocked`` status, so the
  period is never silently shortened.
- **Recovery** re-reads a few positions of unresolved ``failed``, ``skipped`` or
  ``provider_unavailable`` coverage ranges. A successful read records ``processed``, which marks
  the gap resolved. It never moves a checkpoint.

Both reuse the collectors' per-block scan and event feeds, so a ``processed`` range means the same
thing whichever worker wrote it. They avoid what assumes forward order: the live cursor keys and
the legacy gap list (the token-statistics feed counts only positions above its own cursor, so a
backward window stores transfers but never recounts). Live work has priority: both wait until live
collection has started, and while it is running or further behind than configured. Every run is
bounded by positions, an RPC call budget and a deadline.

When the routed provider does not serve a position, each other eligible provider is asked once;
the one that has it then serves the rest of the run (its receipts and logs too), because a pruned
node's empty log answer would otherwise look like a complete one.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from bip.chains.common import ParseError
from bip.chains.ethereum import parse as eth
from bip.chains.ethereum.adapter import EthereumAdapter
from bip.chains.solana.adapter import SolanaAdapter
from bip.config.settings import Settings
from bip.core.logging import get_logger
from bip.db.checkpoints import (
    CheckpointStatus,
    CheckpointStore,
    CoverageStatus,
    StreamRef,
)
from bip.db.errors import DatabaseError
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.ingest.collector import COLLECTOR_VERSION as ETH_VERSION
from bip.ingest.collector import ChainCollector
from bip.ingest.ethereum_events import EventFeeds
from bip.ingest.solana_collector import COLLECTOR_VERSION as SOL_VERSION
from bip.ingest.solana_collector import SolanaCollector, _deterministic  # pyright: ignore[reportPrivateUsage]
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

log = get_logger("bip.backfill")

Kind = Literal[
    "ok", "empty", "history_unavailable", "ambiguous", "transient", "deterministic", "no_room"
]
READ_ERRORS = (RpcError, ParseError, RoutingFailedError, TransportError)
RECOVERABLE = (CoverageStatus.FAILED, CoverageStatus.SKIPPED, CoverageStatus.PROVIDER_UNAVAILABLE)
VERSIONS = {"ethereum": ETH_VERSION, "solana": SOL_VERSION}
RECEIPT_CAP_REASON = "qualifying transaction(s) not stored (receipt cap)"
AMBIGUOUS_REASON = (
    "every provider asked said 'skipped, or missing in long-term storage': a skipped slot or "
    "pruned history, which the answer does not distinguish"
)
STALE_RUNNING = timedelta(seconds=180)
"""A live checkpoint left ``running`` for longer than two maximum run deadlines is a crashed run,
not live work in progress, and does not keep historical work waiting."""


@dataclass(frozen=True, slots=True)
class Read:
    kind: Kind
    block_hash: str | None = None
    parent_hash: str | None = None
    provider: str | None = None
    detail: str = ""


def _solana_history(exc: RpcError) -> Literal["pruned", "ambiguous"] | None:
    """Classify a Solana error about history. ``-32001`` / "cleaned up" means the node has pruned
    the slot. ``-32009`` / "missing in long-term storage" says *skipped, or* missing: Solana does
    not tell the two apart in that answer."""
    text = str(exc).lower()
    if exc.code == -32001 or "cleaned up" in text:
        return "pruned"
    if exc.code == -32009 or "long-term storage" in text:
        return "ambiguous"
    return None


class _Reader:
    """Chain-specific reading and storing of one position, shared by backfill and recovery."""

    chain: str
    version: str
    ref: StreamRef

    def head(self) -> int | None: ...  # pragma: no cover - interface

    def read(
        self, position: int, m: dict[str, Any], *, first: bool
    ) -> Read: ...  # pragma: no cover

    def after(self, lo: int, hi: int, m: dict[str, Any]) -> None:
        """Feeds over a finished window (Ethereum event logs). Default: nothing."""

    def providers(self) -> set[str]: ...  # pragma: no cover

    def calls(self) -> int: ...  # pragma: no cover


class _EthReader(_Reader):
    chain, version, ref = "ethereum", ETH_VERSION, StreamRef("ethereum", "collector")

    def __init__(
        self,
        router: ProviderRouter,
        repo: Repository,
        settings: Settings,
        budget: CallBudget,
        store: CheckpointStore,
    ) -> None:
        self._router, self._budget = router, budget
        self._adapter = EthereumAdapter(router, budget=budget)
        self._used: list[EthereumAdapter] = [self._adapter]
        self._collector = ChainCollector(router, repo, settings)
        self._cfg = settings.collector
        self._store = store
        self._feeds = EventFeeds(repo, settings.collector, self._gap)
        cap = settings.backfill.max_rpc_calls_per_run
        self._read_limit = cap - min(settings.collector.feed_call_reserve, cap // 2)
        self.headers: dict[int, eth.BlockHeader] = {}

    def _gap(self, first: int, last: int, reason: str) -> None:
        self._store.record_range(
            self.ref,
            first,
            last,
            CoverageStatus.INCOMPLETE,
            decoder_version=self.version,
            reason=reason,
        )

    def head(self) -> int | None:
        fin = self._adapter.finalized_block()
        return None if fin is None else fin[0].number

    def read(self, position: int, m: dict[str, Any], *, first: bool) -> Read:
        if not first and self._budget.calls >= self._read_limit:
            return Read("no_room", detail="call budget kept for the event feeds")
        try:
            pair = self._adapter.block_full(position)
            if pair is None:
                found, tried = self._from_another(position)
                if found == "transient":
                    return Read("transient", detail="another provider failed while asked")
                if found is None:
                    return Read(
                        "history_unavailable",
                        detail=f"block {position} not served by: {', '.join(tried)}",
                    )
                pair = found
            header, txs, src = pair
            wanted = self._collector.wanted(txs, m)
            room = self._cfg.max_receipts_per_run - m["receipts"]
            if len(wanted) > room:
                if not first:  # never half-store a block behind others: it starts the next run
                    return Read("no_room", detail="receipt cap reached; resumes next run")
                self._gap(position, position, RECEIPT_CAP_REASON)
                wanted = wanted[: max(room, 0)]
            m["receipts"] += self._collector.scan_block(self._adapter, header, src, wanted, m)
        except (BudgetExhaustedError, NoEligibleProviderError):
            raise
        except READ_ERRORS as exc:
            kind: Kind = "deterministic" if _deterministic(exc) else "transient"
            return Read(kind, detail=redact_text(f"{type(exc).__name__}: {exc}")[:300])
        self.headers[position] = header
        return Read("ok", header.hash, header.parent_hash, src.provider_id)

    def _from_another(self, position: int) -> tuple[Any, list[str]]:
        """The provider that answered did not serve this block. Ask each other eligible provider
        once, pinned. The first that has it serves the rest of the run. Returns the block, None
        (nobody has it) or ``"transient"`` (a provider failed, so the answer is unknown)."""
        tried = [self._adapter.last_provider or "routed provider"]
        views, _ = self._router.eligible("ethereum", "eth_getBlockByNumber")
        for v in views:
            if v.spec.id in tried:
                continue
            alt = EthereumAdapter(self._router, budget=self._budget, provider_id=v.spec.id)
            self._used.append(alt)
            tried.append(v.spec.id)
            try:
                pair = alt.block_full(position)
            except (BudgetExhaustedError, NoEligibleProviderError):
                raise
            except READ_ERRORS as exc:
                if _deterministic(exc):
                    continue
                return "transient", tried
            if pair is not None:
                self._adapter = alt  # sticky: its receipts and logs too
                return pair, tried
        return None, tried

    def after(self, lo: int, hi: int, m: dict[str, Any]) -> None:
        window = {n: h for n, h in self.headers.items() if lo <= n <= hi}
        if not window:
            return
        self._feeds.run(self._adapter, lo, hi, window, m)  # the same feeds as live collection
        if not self._cfg.token_scan_enabled:
            self._feeds.allowlisted_transfers(self._adapter, lo, hi, window, m)

    def providers(self) -> set[str]:
        out: set[str] = set()
        for a in self._used:
            out |= a.providers_used
        return out

    def calls(self) -> int:
        return sum(a.calls for a in self._used)


class _SolReader(_Reader):
    chain, version, ref = "solana", SOL_VERSION, StreamRef("solana", "collector")

    def __init__(
        self,
        router: ProviderRouter,
        repo: Repository,
        settings: Settings,
        budget: CallBudget,
        store: CheckpointStore,
    ) -> None:
        self._router, self._budget = router, budget
        self._adapter = SolanaAdapter(router, budget=budget)
        self._used: list[SolanaAdapter] = [self._adapter]
        self._collector = SolanaCollector(router, repo, settings, legacy_gaps=False)

    def head(self) -> int | None:
        return self._adapter.slot("finalized")

    def read(self, position: int, m: dict[str, Any], *, first: bool) -> Read:
        try:
            try:
                pair = self._adapter.block_full(position)
            except RpcError as exc:
                history = _solana_history(exc)
                if history is None:
                    raise
                found, tried = self._from_another(position, exc.provider_id, history)
                if isinstance(found, str):
                    kind: Kind = found  # type: ignore[assignment]
                    return Read(
                        kind, detail=redact_text(f"{exc} (asked: {', '.join(tried)})")[:300]
                    )
                pair = found
            if pair is None:
                return Read("empty")  # the node says the slot was skipped (-32007)
            block, src = pair
            m["_stored"] += self._collector.scan_block(position, block, src, m)
        except (BudgetExhaustedError, NoEligibleProviderError):
            raise
        except READ_ERRORS as exc:
            kind = "deterministic" if _deterministic(exc) else "transient"
            return Read(kind, detail=redact_text(f"{type(exc).__name__}: {exc}")[:300])
        return Read(
            "ok",
            str(block.get("blockhash")),
            str(block.get("previousBlockhash")),
            src.provider_id,
        )

    def _from_another(
        self, position: int, failed: str | None, history: str
    ) -> tuple[Any, list[str]]:
        """Ask each other eligible provider once (pinned) for a slot the routed one lacks.
        Returns ``(block, src)``, ``None`` (skipped, per a provider that has the slot), or a
        kind: ``"history_unavailable"`` / ``"ambiguous"`` when nobody has it, ``"transient"`` when
        a provider failed so the answer is unknown."""
        tried = [failed or "routed provider"]
        worst: str = "history_unavailable" if history == "pruned" else "ambiguous"
        views, _ = self._router.eligible("solana", "getBlock")
        for v in views:
            if v.spec.id in tried:
                continue
            alt = SolanaAdapter(self._router, budget=self._budget, provider_id=v.spec.id)
            self._used.append(alt)
            tried.append(v.spec.id)
            try:
                pair = alt.block_full(position)
            except (BudgetExhaustedError, NoEligibleProviderError):
                raise
            except RpcError as exc:
                again = _solana_history(exc)
                if again is None and not _deterministic(exc):
                    return "transient", tried
                if again == "ambiguous":
                    worst = "ambiguous"
                continue
            except READ_ERRORS:
                return "transient", tried
            self._adapter = alt  # sticky for the rest of the run
            return pair, tried
        return worst, tried

    def providers(self) -> set[str]:
        out: set[str] = set()
        for a in self._used:
            out |= a.providers_used
        return out

    def calls(self) -> int:
        return sum(a.calls for a in self._used)


class _Base:
    def __init__(self, router: ProviderRouter, repo: Repository, settings: Settings) -> None:
        self._router = router
        self._repo = repo
        self._settings = settings
        self._cfg = settings.backfill
        self._store = CheckpointStore(repo)

    def _reader(self, chain: str, budget: CallBudget) -> _Reader:
        cls = _EthReader if chain == "ethereum" else _SolReader
        return cls(self._router, self._repo, self._settings, budget, self._store)

    def _budget(self) -> CallBudget:
        return CallBudget.create(self._cfg.max_rpc_calls_per_run, self._cfg.run_deadline_s)

    def _wait_for_live(self, ref: StreamRef) -> str | None:
        """Why historical work must wait, or None. Live collection must have started (its first
        position anchors the backfill, so no range between the two is left unrecorded), must not
        be running, and must not be further behind than configured."""
        live = self._store.get(ref, "live")
        if live is None or live.cursor is None:
            return "live collection has not started yet; it anchors the backfill"
        if (
            live.status == CheckpointStatus.RUNNING.value
            and utcnow() - live.updated_at < STALE_RUNNING
        ):
            return "the live collector is running; live work has priority"
        limit = (
            self._cfg.max_live_lag_blocks
            if ref.chain == "ethereum"
            else self._cfg.max_live_lag_slots
        )
        if live.lag is not None and live.lag > limit:
            return f"live is {live.lag} positions behind (limit {limit}); live work has priority"
        return None

    @staticmethod
    def _manifest(chain: str, kind: str) -> dict[str, Any]:
        m: dict[str, Any] = defaultdict(int)
        m.update(
            chain=chain,
            worker=kind,
            status="succeeded",
            message="",
            started_at=utcnow().isoformat(),
            receipts=0,
            _stored=0,
            positions_read=0,
            positions_committed=0,
            positions_given_up=0,
            positions_ambiguous=0,
            continuity_mismatches=0,
            positions_recovered=0,
            positions_still_missing=0,
        )
        return m

    @staticmethod
    def _out(m: dict[str, Any], reader: _Reader | None) -> dict[str, Any]:
        out = {k: v for k, v in m.items() if not k.startswith("_")}
        out["rpc_calls"] = reader.calls() if reader is not None else 0
        out["providers"] = sorted(reader.providers()) if reader is not None else []
        out["finished_at"] = utcnow().isoformat()
        return out


Stop = tuple[CheckpointStatus, str, str]


class Backfill(_Base):
    """Bounded backward reading of one chain's ``collector`` stream."""

    def run(self, chain: str) -> dict[str, Any]:
        ref = StreamRef(chain, "collector")
        version = VERSIONS[chain]
        m = self._manifest(chain, "backfill")
        cp = self._store.get(ref, "historical")
        if cp is not None and cp.status == CheckpointStatus.PAUSED.value:
            m.update(status="paused", message="Backfill is paused by an operator.")
            return self._out(m, None)
        if cp is not None and cp.status == CheckpointStatus.COMPLETE.value:
            m.update(status="complete", message="The historical boundary was reached.")
            return self._out(m, None)
        if (why := self._wait_for_live(ref)) is not None:
            m.update(status="deferred", message=why)
            return self._out(m, None)
        reader = self._reader(chain, self._budget())
        self._store.start_run(ref, "historical", decoder_version=version)
        if self._store.is_paused(ref, "historical"):  # paused between the check and the start
            m.update(status="paused", message="Backfill was paused as it started.")
            return self._out(m, None)
        stop: Stop | None = None
        try:
            stop = self._window(ref, version, reader, cp, m)
        except BudgetExhaustedError as exc:
            m.update(status="partial", message=f"Stopped at the run budget: {exc}.")
        except NoEligibleProviderError as exc:
            stop = (
                CheckpointStatus.BLOCKED,
                "no_eligible_provider",
                redact_text(f"No eligible provider: {exc}")[:300],
            )
        except DatabaseError:
            raise
        except Exception as exc:  # the manifest must always be written
            log.exception("backfill run failed unexpectedly")
            stop = (CheckpointStatus.STALLED, "internal_error", f"unexpected {type(exc).__name__}")
        finally:
            if stop is not None:
                status, category, detail = stop
                self._store.mark(
                    ref,
                    "historical",
                    status,
                    decoder_version=version,
                    error_category=category,
                    detail=detail,
                    providers=reader.providers(),
                )
                m["status"] = "blocked" if status is CheckpointStatus.BLOCKED else "partial"
                m["message"] = detail
            self._store.finish_run(ref, "historical")
        after = self._store.get(ref, "historical")
        if after is not None:
            m["cursor"], m["boundary"] = after.cursor, after.boundary
            if after.status == CheckpointStatus.COMPLETE.value:
                m["status"] = "complete"
        return self._out(m, reader)

    def _anchor(self, ref: StreamRef, version: str, cp: Any, m: dict[str, Any]) -> tuple[int, int]:
        """The position the window ends below, and the boundary (set once)."""
        cfg = self._cfg
        anchor: int
        if cp is not None and cp.cursor is not None:
            anchor = int(cp.cursor)
        else:
            lowest = self._store.lowest_processed(ref)
            live = self._store.get(ref, "live")
            live_cursor = live.cursor if live is not None and live.cursor is not None else 0
            anchor = int(lowest) if lowest is not None else live_cursor + 1
        boundary: int | None = (
            int(cp.boundary) if cp is not None and cp.boundary is not None else None
        )
        if boundary is None:
            days, rate = (
                (cfg.ethereum_target_days, cfg.ethereum_blocks_per_day_estimate)
                if ref.chain == "ethereum"
                else (cfg.solana_target_days, cfg.solana_slots_per_day_estimate)
            )
            boundary = max(0, anchor - days * rate)
            self._store.set_boundary(ref, boundary, decoder_version=version)
            self._store.set_recovery(
                ref,
                "historical",
                "boundary_basis",
                f"{days} days x {rate} positions/day (estimate) below position {anchor}",
            )
            m["boundary_set"] = boundary
        return anchor, boundary

    def _window(
        self, ref: StreamRef, version: str, reader: _Reader, cp: Any, m: dict[str, Any]
    ) -> Stop | None:
        cfg = self._cfg
        anchor, boundary = self._anchor(ref, version, cp, m)
        hi = anchor - 1
        if hi < boundary:  # already at the boundary: nothing left to read
            self._store.mark(ref, "historical", CheckpointStatus.COMPLETE, decoder_version=version)
            return None
        per_run = cfg.blocks_per_run if ref.chain == "ethereum" else cfg.slots_per_run
        lo = max(boundary, hi - per_run + 1)
        m["window"] = [lo, hi]
        current = self._store.get(ref, "historical")
        attempts: dict[str, int] = dict((current.recovery if current else {}).get("attempts", {}))
        done: list[tuple[int, Read]] = []
        lowest_done: int | None = None
        readable = False
        stop: Stop | None = None
        above: Read | None = None
        for pos in range(hi, lo - 1, -1):
            r = reader.read(pos, m, first=pos == hi)
            if r.kind in ("ok", "empty"):
                if (
                    r.kind == "ok"
                    and above is not None
                    and above.kind == "ok"
                    and above.parent_hash != r.block_hash
                ):
                    m["continuity_mismatches"] += 1
                attempts.pop(str(pos), None)
                done.append((pos, r))
                lowest_done, readable, above = pos, True, r
                m["positions_read"] += 1
                continue
            if r.kind == "no_room":
                m.update(status="partial", message=f"{r.detail} (stopped above position {pos}).")
                break
            if r.kind == "ambiguous":
                self._store.record_range(
                    ref,
                    pos,
                    pos,
                    CoverageStatus.PROVIDER_UNAVAILABLE,
                    decoder_version=version,
                    reason=AMBIGUOUS_REASON,
                )
                m["positions_ambiguous"] += 1
                lowest_done, above = pos, None
                continue
            if r.kind == "history_unavailable":
                self._store.record_range(
                    ref,
                    pos,
                    pos,
                    CoverageStatus.PROVIDER_UNAVAILABLE,
                    decoder_version=version,
                    reason="no configured provider served this position (history floor)",
                )
                stop = (
                    CheckpointStatus.BLOCKED,
                    "history_unavailable",
                    f"Position {pos} is not served by any configured provider; the backfill "
                    f"stops here instead of shortening the period (boundary {boundary}).",
                )
                break
            if r.kind == "deterministic":
                n = attempts.get(str(pos), 0) + 1
                attempts[str(pos)] = n
                if n < cfg.max_attempts:
                    stop = (
                        CheckpointStatus.STALLED,
                        "read_failed",
                        f"Position {pos} failed ({n}/{cfg.max_attempts}); retried next run.",
                    )
                    break
                self._store.record_range(
                    ref,
                    pos,
                    pos,
                    CoverageStatus.FAILED,
                    decoder_version=version,
                    reason=f"given up after {n} failed reads",
                    attempts=n,
                )
                attempts.pop(str(pos), None)
                m["positions_given_up"] += 1
                lowest_done, readable, above = pos, True, None
                continue
            stop = (CheckpointStatus.STALLED, "provider_unavailable", r.detail or "read failed")
            break
        self._store.set_recovery(ref, "historical", "attempts", attempts)
        if lowest_done is None:
            return stop
        if not readable:  # nothing in the window could be read: treat as the history floor
            return (
                CheckpointStatus.BLOCKED,
                "history_unavailable",
                f"No position in {lo}..{hi} could be read from any configured provider; "
                f"the backfill stops here (boundary {boundary}).",
            )
        try:
            reader.after(lowest_done, hi, m)
        except (BudgetExhaustedError, NoEligibleProviderError, *READ_ERRORS) as exc:
            # The feeds did not finish: keep the cursor so the window is read again (idempotent).
            return (
                CheckpointStatus.STALLED,
                "provider_unavailable",
                redact_text(f"event feeds did not finish: {exc}")[:300],
            )
        hashes = {pos: r.block_hash for pos, r in done if r.kind == "ok"}
        with self._repo.transaction() as conn:
            for pos, r in done:
                status = (
                    CoverageStatus.PROCESSED if r.kind == "ok" else CoverageStatus.CONFIRMED_EMPTY
                )
                self._store.record_range(
                    ref,
                    pos,
                    pos,
                    status,
                    decoder_version=version,
                    provider_id=r.provider,
                    conn=conn,
                )
            self._store.commit_batch(
                ref,
                "historical",
                from_pos=lowest_done,
                to_pos=hi,
                decoder_version=version,
                cursor_hash=hashes.get(lowest_done),
                providers=reader.providers(),
                conn=conn,
            )
        m["positions_committed"] = hi - lowest_done + 1
        return stop


class GapRecovery(_Base):
    """Re-read a few positions of unresolved gaps. Never moves a checkpoint."""

    def run(self, chain: str) -> dict[str, Any]:
        ref = StreamRef(chain, "collector")
        version = VERSIONS[chain]
        m = self._manifest(chain, "recovery")
        if self._store.is_paused(ref, "historical"):
            m.update(status="paused", message="Historical work (backfill and recovery) is paused.")
            return self._out(m, None)
        if (why := self._wait_for_live(ref)) is not None:
            m.update(status="deferred", message=why)
            return self._out(m, None)
        targets = self._targets(ref)
        m["positions_targeted"] = len(targets)
        if not targets:
            m["message"] = "No unresolved gaps to recover."
            return self._out(m, None)
        reader = self._reader(chain, self._budget())
        try:
            for i, pos in enumerate(targets):
                r = reader.read(pos, m, first=i == 0)
                if r.kind in ("ok", "empty"):
                    reader.after(pos, pos, m)
                    self._store.record_range(
                        ref,
                        pos,
                        pos,
                        CoverageStatus.PROCESSED
                        if r.kind == "ok"
                        else CoverageStatus.CONFIRMED_EMPTY,
                        decoder_version=version,
                        provider_id=r.provider,
                    )
                    m["positions_recovered"] += 1
                elif r.kind in ("transient", "no_room"):
                    m.update(status="partial", message=r.detail or "a provider failed")
                    break
                else:
                    m["positions_still_missing"] += 1
        except BudgetExhaustedError as exc:
            m.update(status="partial", message=f"Stopped at the run budget: {exc}.")
        except NoEligibleProviderError as exc:
            m.update(status="blocked", message=redact_text(f"No eligible provider: {exc}")[:300])
        except READ_ERRORS as exc:
            m.update(status="partial", message=redact_text(f"{type(exc).__name__}: {exc}")[:300])
        if not m["message"]:
            m["message"] = (
                f"Recovered {m['positions_recovered']} of {len(targets)} position(s); "
                f"{m['positions_still_missing']} still missing."
            )
        return self._out(m, reader)

    def _targets(self, ref: StreamRef) -> list[int]:
        """Up to the configured number of positions from unresolved gaps, newest gaps first,
        skipping positions a processed range already holds."""
        out: list[int] = []
        rows = self._store.ranges(
            ref.chain,
            stream=ref.stream,
            statuses=RECOVERABLE,
            include_resolved=False,
            exclude_reason=AMBIGUOUS_REASON,  # every provider already said "skipped, or missing"
            limit=50,
        )
        for r in rows:
            for pos in range(int(r["from_pos"]), int(r["to_pos"]) + 1):
                if len(out) >= self._cfg.recovery_positions_per_run:
                    return out
                if not self._store.covered(ref, pos):
                    out.append(pos)
        return out
