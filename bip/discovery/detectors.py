"""Detectors over stored canonical data.

Each detector reads rows ingestion already stored (never orphaned ones), is bounded by
``max_rows_scanned`` (and says when it truncated), and returns raw candidates: what was observed,
how it compares with a baseline, and which stored transactions it rests on. Scoring, deduplication
and persistence happen in :mod:`bip.discovery.service`.

What a detector cannot see is part of its contract. BIP stores only the history it was asked to
ingest, so "dormant", "new" and "normal" always mean *in the stored data*; the coverage and
limitation text of each detector says so, and its confidence cap reflects it.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any, NamedTuple

from sqlalchemy import select

from bip.config.settings import DiscoverySettings
from bip.db import schema
from bip.db.canonical import canonical_transaction, orphaned_tx_ids
from bip.db.repository import Repository
from bip.discovery.scoring import clamp, magnitude_from_z, reliability_from_count

MAD_SCALE = 1.4826
"""Makes the median absolute deviation comparable to a standard deviation for normal data."""
LOG10_MAD_FLOOR = 0.05
"""Smallest scale used for log10(amount) comparisons (about 12% of an amount), so a baseline of
identical amounts cannot make every small difference look infinitely unusual."""
COUNT_MAD_FLOOR = 1.0
MAX_EVIDENCE_TXS = 20
MAX_SUBJECTS_PER_DETECTOR = 200
NATIVE = "native"


@dataclass(frozen=True, slots=True)
class DetectorInfo:
    id: str
    version: str
    family: str
    title: str
    method: str
    data_required: str
    coverage: str
    limitations: str
    refresh: str
    cost: str
    confidence_cap: float
    chains: tuple[str, ...]


@dataclass(slots=True)
class RawCandidate:
    detector_id: str
    detector_version: str
    family: str
    chain: str
    subject_kind: str
    subject_ref: str
    title: str
    key: str
    observed_at: datetime
    tx_ids: list[str]
    magnitude: float
    baseline_reliability: float
    confidence_cap: float
    explanation: dict[str, object]
    notes: dict[str, str] = field(default_factory=dict[str, str])
    z: float = 0.0
    """Ranking value used to keep the strongest candidates when a detector is capped."""
    evidence_finalized: bool = False
    """The evidence comes from rows the collector only reads at finalized height (and checks
    against the canonical block hash), so it counts as finalized even without a stored
    transaction row."""


@dataclass(slots=True)
class DetectorOutput:
    candidates: list[RawCandidate]
    rows_scanned: int
    truncated: bool
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Context:
    repo: Repository
    cfg: DiscoverySettings
    chain: str
    now: datetime


Run = Callable[[Context], DetectorOutput]


class _Transfer(NamedTuple):
    log_amount: float
    row_id: int
    tx_id: str
    token: str
    sender: str | None
    recipient: str | None
    amount: int
    decimals: int | None
    when: datetime


def robust_z(x: float, baseline: list[float], floor: float) -> tuple[float, float, float]:
    """Robust z-score of ``x`` against ``baseline`` using the median and the scaled MAD.

    Returns ``(z, median, scaled_mad)``. The scale never falls below ``floor``.
    """
    median = statistics.median(baseline)
    mad = statistics.median(abs(v - median) for v in baseline) * MAD_SCALE
    return (x - median) / max(mad, floor), median, mad


def _tx_rows(
    ctx: Context,
) -> tuple[list[tuple[str, str | None, str | None, datetime, str | None, str]], bool]:
    """Canonical transactions of the baseline window, newest first, bounded by the row cap."""
    t = schema.transactions
    since = ctx.now - timedelta(days=ctx.cfg.baseline_days)
    cap = ctx.cfg.max_rows_scanned
    with ctx.repo.transaction() as conn:
        rows = conn.execute(
            select(
                t.c.tx_id,
                t.c.from_address,
                t.c.to_address,
                t.c.block_time,
                t.c.created_contract,
                t.c.parse_status,
            )
            .where(
                t.c.chain == ctx.chain,
                t.c.block_time.is_not(None),
                t.c.block_time >= since,
                canonical_transaction(),
            )
            .order_by(t.c.block_time.desc())
            .limit(cap + 1)
        ).all()
    truncated = len(rows) > cap
    out = [
        (
            str(r.tx_id),
            r.from_address,
            r.to_address,
            r.block_time,
            r.created_contract,
            str(r.parse_status),
        )
        for r in rows[:cap]
    ]
    return out, truncated


# --------------------------------------------------------------------------- large transfers


def large_transfers(ctx: Context) -> DetectorOutput:
    """Recent transfers far above what is usual for the same token (or native asset)."""
    tr = schema.transfers
    cfg = ctx.cfg
    since = ctx.now - timedelta(days=cfg.baseline_days)
    recent_start = ctx.now - timedelta(hours=cfg.lookback_hours)
    with ctx.repo.transaction() as conn:
        rows = conn.execute(
            select(
                tr.c.id,
                tr.c.tx_id,
                tr.c.token,
                tr.c.from_address,
                tr.c.to_address,
                tr.c.amount_raw,
                tr.c.decimals,
                tr.c.block_time,
            )
            .where(
                tr.c.chain == ctx.chain,
                tr.c.token_id.is_(None),
                tr.c.block_time.is_not(None),
                tr.c.block_time >= since,
                tr.c.tx_id.not_in(orphaned_tx_ids(ctx.chain)),
            )
            .order_by(tr.c.block_time.desc())
            .limit(cfg.max_rows_scanned + 1)
        ).all()
    truncated = len(rows) > cfg.max_rows_scanned
    rows = rows[: cfg.max_rows_scanned]
    groups: dict[str, list[_Transfer]] = defaultdict(list)
    for r in rows:
        try:
            amount = int(r.amount_raw)
        except ValueError:
            continue
        if amount > 0:
            token = r.token or NATIVE
            groups[token].append(
                _Transfer(
                    math.log10(amount),
                    int(r.id),
                    str(r.tx_id),
                    token,
                    r.from_address,
                    r.to_address,
                    amount,
                    r.decimals,
                    r.block_time,
                )
            )
    out: list[RawCandidate] = []
    for token, items in groups.items():
        baseline = [x.log_amount for x in items if x.when < recent_start]
        if len(baseline) < cfg.min_baseline:
            continue
        for x in items:
            if x.when < recent_start:
                continue
            z, median, mad = robust_z(x.log_amount, baseline, LOG10_MAD_FLOOR)
            if z < cfg.mad_threshold:
                continue
            shown = x.amount / 10**x.decimals if x.decimals is not None else float(x.amount)
            is_native = token == NATIVE
            what = "the native asset" if is_native else "a token"
            out.append(
                RawCandidate(
                    detector_id="large_transfer_mad",
                    detector_version="1",
                    family="large_transfers",
                    chain=ctx.chain,
                    subject_kind="asset" if is_native else "token",
                    subject_ref=token,
                    title=f"Unusually large transfer of {what}",
                    key=f"large_transfer:{ctx.chain}:{x.tx_id}:{x.row_id}",
                    observed_at=x.when,
                    tx_ids=[x.tx_id],
                    magnitude=magnitude_from_z(z, cfg.mad_threshold),
                    baseline_reliability=reliability_from_count(len(baseline)),
                    confidence_cap=0.8,
                    z=z,
                    explanation={
                        "statement": (
                            "A stored transfer is far larger than the usual transfers of the "
                            "same asset in the stored baseline."
                        ),
                        "observed": {
                            "amount": shown,
                            "amount_raw": str(x.amount),
                            "decimals": x.decimals,
                            "from": x.sender,
                            "to": x.recipient,
                        },
                        "baseline": {
                            "observations": len(baseline),
                            "median_log10_amount": round(median, 4),
                            "scaled_mad_log10": round(mad, 4),
                            "robust_z": round(z, 2),
                            "threshold": cfg.mad_threshold,
                        },
                        "method": (
                            "median/MAD on log10(amount), same asset; the baseline excludes the "
                            "lookback window"
                        ),
                        "data_censoring": (
                            "Native transfers are stored only at or above the collector "
                            "threshold, so the baseline omits small transfers and the robust z is "
                            "relative to that censored baseline."
                        ),
                    },
                    notes={
                        "magnitude": f"robust z {z:.1f} against threshold {cfg.mad_threshold}",
                        "baseline_reliability": f"{len(baseline)} baseline transfers (50 = full)",
                    },
                )
            )
    return DetectorOutput(out, len(rows), truncated)


# --------------------------------------------------------------------------- dormancy


def dormant_reactivation(ctx: Context) -> DetectorOutput:
    """Signing wallets active in the lookback window after a long gap in the stored history."""
    t = schema.transactions
    cfg = ctx.cfg
    recent_start = ctx.now - timedelta(hours=cfg.lookback_hours)
    rows, truncated = _tx_rows(ctx)
    first_recent: dict[str, tuple[datetime, str]] = {}
    for tx_id, sender, _to, when, _created, _status in rows:
        if sender and when >= recent_start:
            current = first_recent.get(sender)
            if current is None or when < current[0]:
                first_recent[sender] = (when, tx_id)
    out: list[RawCandidate] = []
    with ctx.repo.transaction() as conn:
        for sender, (when, tx_id) in list(first_recent.items())[:MAX_SUBJECTS_PER_DETECTOR]:
            prior = conn.execute(
                select(t.c.tx_id, t.c.block_time)
                .where(
                    t.c.chain == ctx.chain,
                    t.c.from_address == sender,
                    t.c.block_time.is_not(None),
                    t.c.block_time < when,
                    canonical_transaction(),
                )
                .order_by(t.c.block_time.desc())
                .limit(1)
            ).first()
            if prior is None:
                continue  # nothing earlier is stored: "new" in the data is not "dormant"
            gap_days = (when - prior.block_time).total_seconds() / 86_400
            if gap_days < cfg.dormant_days:
                continue
            out.append(
                RawCandidate(
                    detector_id="dormant_reactivation",
                    detector_version="1",
                    family="wallet_activity",
                    chain=ctx.chain,
                    subject_kind="wallet",
                    subject_ref=sender,
                    title=f"Wallet active again after {int(gap_days)} days without stored activity",
                    key=f"dormant:{ctx.chain}:{sender}:{tx_id}",
                    observed_at=when,
                    tx_ids=[tx_id, str(prior.tx_id)],
                    magnitude=clamp(gap_days / (cfg.dormant_days * 4)),
                    baseline_reliability=0.5,
                    confidence_cap=0.5,
                    z=gap_days,
                    explanation={
                        "statement": (
                            "A wallet that signed a transaction in the lookback window had no "
                            "earlier stored transaction for a long period."
                        ),
                        "observed": {
                            "gap_days": round(gap_days, 1),
                            "previous_activity_at": prior.block_time.isoformat(),
                            "reactivated_at": when.isoformat(),
                        },
                        "method": (
                            "gap between consecutive stored transactions signed by the wallet"
                        ),
                        "caveat": (
                            "The wallet may have been active in the gap on transactions BIP "
                            "did not "
                            "ingest. This is dormancy in the stored data only."
                        ),
                    },
                    notes={
                        "magnitude": f"gap {gap_days:.0f} days against {cfg.dormant_days}",
                        "baseline_reliability": "fixed 0.5: stored history is partial",
                    },
                )
            )
    return DetectorOutput(out, len(rows), truncated)


# --------------------------------------------------------------------------- activity bursts


def activity_burst(ctx: Context) -> DetectorOutput:
    """Wallets that signed far more transactions in the last 24 hours than on their usual days."""
    cfg = ctx.cfg
    rows, truncated = _tx_rows(ctx)
    window_start = ctx.now - timedelta(hours=24)
    recent: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    daily: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for tx_id, sender, _to, when, _created, _status in rows:
        if not sender:
            continue
        if when >= window_start:
            recent[sender].append((when, tx_id))
        else:
            daily[sender][when.date().isoformat()] += 1
    out: list[RawCandidate] = []
    for sender, txs in recent.items():
        count = len(txs)
        if count < cfg.burst_min_count:
            continue
        baseline = [float(n) for n in daily[sender].values()]
        if len(baseline) < cfg.min_baseline:
            continue
        z, median, mad = robust_z(float(count), baseline, COUNT_MAD_FLOOR)
        if z < cfg.mad_threshold:
            continue
        txs.sort(reverse=True)
        out.append(
            RawCandidate(
                detector_id="activity_burst",
                detector_version="1",
                family="abnormal_frequency",
                chain=ctx.chain,
                subject_kind="wallet",
                subject_ref=sender,
                title=f"Wallet signed {count} transactions in 24 hours, far above its usual day",
                key=f"burst:{ctx.chain}:{sender}:{ctx.now.date().isoformat()}",
                observed_at=txs[0][0],
                tx_ids=[tx for _w, tx in txs[:MAX_EVIDENCE_TXS]],
                magnitude=magnitude_from_z(z, cfg.mad_threshold),
                baseline_reliability=reliability_from_count(len(baseline)),
                confidence_cap=0.8,
                z=z,
                explanation={
                    "statement": (
                        "The wallet signed many more transactions in the last 24 hours than on "
                        "its other active days in the stored baseline."
                    ),
                    "observed": {"transactions_24h": count},
                    "baseline": {
                        "active_days": len(baseline),
                        "median_daily_count": median,
                        "scaled_mad": round(mad, 3),
                        "robust_z": round(z, 2),
                        "threshold": cfg.mad_threshold,
                    },
                    "method": "median/MAD of daily signed-transaction counts on active days",
                },
                notes={
                    "magnitude": f"robust z {z:.1f} against threshold {cfg.mad_threshold}",
                    "baseline_reliability": f"{len(baseline)} active baseline days (50 = full)",
                },
            )
        )
    return DetectorOutput(out, len(rows), truncated)


# --------------------------------------------------------------------------- deployments


def new_contract_deployment(ctx: Context) -> DetectorOutput:
    """Contracts created in the lookback window, with how often the deployer deploys."""
    cfg = ctx.cfg
    recent_start = ctx.now - timedelta(hours=cfg.lookback_hours)
    rows, truncated = _tx_rows(ctx)
    per_deployer: dict[str, int] = defaultdict(int)
    for _tx, sender, _to, _when, created, _status in rows:
        if created and sender:
            per_deployer[sender] += 1
    out: list[RawCandidate] = []
    for tx_id, sender, _to, when, created, status in rows:
        if not created or not sender or when < recent_start:
            continue
        n = per_deployer[sender]
        complete = status == "complete"
        out.append(
            RawCandidate(
                detector_id="new_contract_deployment",
                detector_version="1",
                family="contracts",
                chain=ctx.chain,
                subject_kind="contract",
                subject_ref=created,
                title="New contract deployed"
                + (f" by a repeat deployer ({n} in window)" if n > 1 else ""),
                key=f"deployment:{ctx.chain}:{created}",
                observed_at=when,
                tx_ids=[tx_id],
                magnitude=clamp(0.15 + 0.85 * clamp((n - 1) / 9)),
                baseline_reliability=clamp(n / 10),
                confidence_cap=0.6 if complete else 0.4,
                z=float(n),
                explanation={
                    "statement": "A stored creation transaction deployed a contract.",
                    "observed": {
                        "contract": created,
                        "deployer": sender,
                        "deployments_by_deployer_in_window": n,
                        "receipt_complete": complete,
                    },
                    "method": "receipt.contractAddress of stored creation transactions",
                    "caveat": "A deployment is an observation. It says nothing about intent.",
                },
                notes={
                    "magnitude": (
                        "0.15 for one deployment, rising to 1.0 at ten by the same deployer"
                    ),
                    "baseline_reliability": (
                        f"{n} deployment(s) by this deployer in the stored window"
                    ),
                },
            )
        )
    return DetectorOutput(out, len(rows), truncated)


# --------------------------------------------------------------------------- new pools


def new_liquidity_pool(ctx: Context) -> DetectorOutput:
    """Pools announced by a factory event in the lookback window; flags tokens not seen before."""
    if ctx.chain != "ethereum":
        return DetectorOutput([], 0, False)
    pe = schema.pool_events
    recent_start = ctx.now - timedelta(hours=ctx.cfg.lookback_hours)
    cap = ctx.cfg.max_rows_scanned
    with ctx.repo.transaction() as conn:
        rows = conn.execute(
            select(pe)
            .where(pe.c.chain == ctx.chain, pe.c.block_time >= recent_start)
            .order_by(pe.c.block_time.desc())
            .limit(cap + 1)
        ).all()
        truncated = len(rows) > cap
        rows = rows[:cap]
        # Earliest stored pool per token, in a few chunked queries (not two queries per row): a
        # token is "unseen" for a pool when that pool is the first stored one to involve it.
        first: dict[str, tuple[int, int]] = {}
        tokens = sorted({str(t) for r in rows for t in (r.token0, r.token1)})
        for i in range(0, len(tokens), 400):
            chunk = tokens[i : i + 400]
            for col in (pe.c.token0, pe.c.token1):
                for tok, blk, log_ix in conn.execute(
                    select(col, pe.c.block_number, pe.c.log_index).where(
                        pe.c.chain == ctx.chain, col.in_(chunk)
                    )
                ):
                    key = (int(blk), int(log_ix))
                    first[str(tok)] = min(first.get(str(tok), key), key)
        out: list[RawCandidate] = []
        for r in rows:
            here = (int(r.block_number), int(r.log_index))
            unseen = [t for t in (r.token0, r.token1) if first.get(str(t), here) == here]
            out.append(
                RawCandidate(
                    detector_id="new_liquidity_pool",
                    detector_version="1",
                    family="pools",
                    chain=ctx.chain,
                    subject_kind="pool",
                    subject_ref=str(r.pool),
                    title="New liquidity pool announced"
                    + (" with a token not seen in earlier stored pools" if unseen else ""),
                    key=f"pool:{ctx.chain}:{r.pool}",
                    observed_at=r.block_time,
                    tx_ids=[str(r.tx_id)],
                    evidence_finalized=True,
                    magnitude=0.6 if unseen else 0.25,
                    baseline_reliability=0.3,
                    confidence_cap=0.4,
                    z=1.0 if unseen else 0.0,
                    explanation={
                        "statement": (
                            "A finalized block contains a pool-creation event from this factory."
                        ),
                        "observed": {
                            "pool": r.pool,
                            "factory": r.factory,
                            "event_layout": r.protocol,
                            "token0": r.token0,
                            "token1": r.token1,
                            "fee": r.fee,
                            "tokens_not_in_earlier_stored_pools": unseen,
                        },
                        "method": "Uniswap v2/v3-style PairCreated/PoolCreated factory logs",
                        "caveat": (
                            "The event layout matched; that does not verify a DEX, liquidity, "
                            "price or that the tokens are legitimate. A token is 'unseen' only "
                            "relative to the stored pool events."
                        ),
                    },
                    notes={
                        "magnitude": "0.6 when a token has no earlier stored pool, otherwise 0.25",
                        "baseline_reliability": (
                            "fixed 0.3: there is no baseline of pool creation to compare with"
                        ),
                    },
                )
            )
    return DetectorOutput(out, len(rows), truncated)


# --------------------------------------------------------------------------- new tokens


def new_token_activity(ctx: Context) -> DetectorOutput:
    """Tokens the collector first saw in the lookback window and that are being transferred."""
    if ctx.chain != "ethereum":
        return DetectorOutput([], 0, False)
    ts, pe = schema.token_stats, schema.pool_events
    recent_start = ctx.now - timedelta(hours=ctx.cfg.lookback_hours)
    cap = ctx.cfg.max_rows_scanned
    with ctx.repo.transaction() as conn:
        rows = conn.execute(
            select(ts)
            .where(
                ts.c.chain == ctx.chain,
                ts.c.first_seen_at >= recent_start,
                ts.c.transfers_seen >= ctx.cfg.new_token_min_transfers,
            )
            .order_by(ts.c.transfers_seen.desc())
            .limit(cap + 1)
        ).all()
        truncated = len(rows) > cap
        rows = rows[:cap]
        with_pool: set[str] = set()
        tokens = [str(r.token) for r in rows]
        for i in range(0, len(tokens), 400):
            chunk = tokens[i : i + 400]
            for col in (pe.c.token0, pe.c.token1):
                with_pool.update(
                    str(x)
                    for x in conn.execute(
                        select(col).where(pe.c.chain == ctx.chain, col.in_(chunk))
                    ).scalars()
                )
    out: list[RawCandidate] = []
    for r in rows:
        pooled = str(r.token) in with_pool
        out.append(
            RawCandidate(
                detector_id="new_token_activity",
                detector_version="1",
                family="tokens",
                chain=ctx.chain,
                subject_kind="token",
                subject_ref=str(r.token),
                title="Token newly seen by the collector"
                + (" and paired in a stored pool" if pooled else ""),
                key=f"newtoken:{ctx.chain}:{r.token}",
                observed_at=r.first_seen_at,
                tx_ids=[str(r.first_tx_id)],
                evidence_finalized=True,
                magnitude=clamp(math.log10(max(1, r.transfers_seen)) / 3 + (0.2 if pooled else 0)),
                baseline_reliability=0.2,
                confidence_cap=0.35,
                z=float(r.transfers_seen),
                explanation={
                    "statement": (
                        "The collector counted ERC-20 transfers of a token it had not seen before."
                    ),
                    "observed": {
                        "token": r.token,
                        "first_seen_block": r.first_seen_block,
                        "transfers_counted": r.transfers_seen,
                        "paired_in_stored_pool": pooled,
                    },
                    "method": "Per-token counts of finalized ERC-20 Transfer logs",
                    "caveat": (
                        "'New' means new to this database, not newly created: the token may be "
                        "years old. Nothing here says the token is legitimate or a scam."
                    ),
                },
                notes={
                    "magnitude": (
                        "log10(transfers counted) / 3, plus 0.2 when a stored pool pairs it"
                    ),
                    "baseline_reliability": "fixed 0.2: there is no baseline of token launches",
                },
            )
        )
    return DetectorOutput(out, len(rows), truncated)


# --------------------------------------------------------------------------- pool reserves


def pool_reserve_change(ctx: Context) -> DetectorOutput:
    """Pools whose stated reserves moved sharply between two stored ``Sync`` updates."""
    if ctx.chain != "ethereum":
        return DetectorOutput([], 0, False)
    pr = schema.pool_reserves
    recent_start = ctx.now - timedelta(hours=ctx.cfg.lookback_hours)
    since = ctx.now - timedelta(days=ctx.cfg.baseline_days)
    cap = ctx.cfg.max_rows_scanned
    with ctx.repo.transaction() as conn:
        # Pools with a recent update first, then their series: truncating the whole table by pool
        # name would leave every pool late in the alphabet permanently unevaluated.
        recent_pools = [
            str(x)
            for x in conn.execute(
                select(pr.c.pool)
                .where(pr.c.chain == ctx.chain, pr.c.block_time >= recent_start)
                .distinct()
                .limit(500)
            ).scalars()
        ]
        rows = conn.execute(
            select(pr)
            .where(
                pr.c.chain == ctx.chain,
                pr.c.pool.in_(recent_pools),
                pr.c.block_time >= since,
            )
            .order_by(pr.c.pool, pr.c.block_number, pr.c.log_index)
            .limit(cap + 1)
        ).all()
    truncated = len(rows) > cap
    rows = rows[:cap]
    by_pool: dict[str, list[Any]] = defaultdict(list)
    for r in rows:
        by_pool[str(r.pool)].append(r)
    out: list[RawCandidate] = []
    threshold = ctx.cfg.reserve_change_threshold
    for pool, series in by_pool.items():
        changes: list[tuple[float, Any, Any]] = []
        for prev, cur in pairwise(series):
            p0, p1 = int(prev.reserve0), int(prev.reserve1)
            c0, c1 = int(cur.reserve0), int(cur.reserve1)
            if p0 <= 0 or p1 <= 0:
                continue
            changes.append((max(abs(c0 - p0) / p0, abs(c1 - p1) / p1), prev, cur))
        recent = [c for c in changes if c[2].block_time >= recent_start and c[0] >= threshold]
        if not recent:
            continue
        change, prev, cur = max(recent, key=lambda c: c[0])
        history = [c[0] for c in changes]
        if len(history) >= ctx.cfg.min_baseline:
            z, median, mad = robust_z(change, history, 0.01)
            magnitude = magnitude_from_z(z, ctx.cfg.mad_threshold)
            method = "median/MAD of this pool's stored reserve changes"
            baseline = {"median_change": median, "scaled_mad": mad, "robust_z": z}
        else:
            z = change / threshold
            magnitude = clamp(change / (2 * threshold))
            method = "fixed relative-change threshold (too little history for median/MAD)"
            baseline = {"threshold": threshold}
        out.append(
            RawCandidate(
                detector_id="pool_reserve_change",
                detector_version="1",
                family="liquidity",
                chain=ctx.chain,
                subject_kind="pool",
                subject_ref=pool,
                title=f"Pool reserves moved {change:.0%} between two stored updates",
                key=f"reserve:{ctx.chain}:{pool}",
                observed_at=cur.block_time,
                tx_ids=[str(prev.tx_id), str(cur.tx_id)],
                evidence_finalized=True,
                magnitude=magnitude,
                baseline_reliability=reliability_from_count(len(history)),
                confidence_cap=0.5,
                z=z,
                explanation={
                    "statement": "A v2-style Sync event states reserves far from the previous one.",
                    "observed": {
                        "pool": pool,
                        "relative_change": change,
                        "before": [prev.reserve0, prev.reserve1],
                        "after": [cur.reserve0, cur.reserve1],
                        "updates_compared": len(history),
                        **baseline,
                    },
                    "method": method,
                    "caveat": (
                        "Reserves are what the event states, not a price or total value locked. "
                        "Only the last update per block is stored, and reserves are raw token "
                        "units. A large move can be an ordinary trade."
                    ),
                },
                notes={
                    "magnitude": "robust z / (3 x MAD threshold), or change / (2 x threshold)",
                    "baseline_reliability": (
                        f"{len(history)} stored reserve change(s) for this pool"
                    ),
                },
            )
        )
    return DetectorOutput(out, len(rows), truncated)


# --------------------------------------------------------------------------- holder concentration


def holder_concentration(ctx: Context) -> DetectorOutput:
    """Tokens whose stored transfers show most net inflow going to very few addresses."""
    if ctx.chain != "ethereum":
        return DetectorOutput([], 0, False)
    tr, pe = schema.transfers, schema.pool_events
    since = ctx.now - timedelta(days=ctx.cfg.baseline_days)
    cap = ctx.cfg.max_rows_scanned
    zero = "0x" + "0" * 40
    with ctx.repo.transaction() as conn:
        pooled = {
            str(t)
            for r in conn.execute(select(pe.c.token0, pe.c.token1).where(pe.c.chain == ctx.chain))
            for t in (r.token0, r.token1)
        }
        pools = {
            str(x) for x in conn.execute(select(pe.c.pool).where(pe.c.chain == ctx.chain)).scalars()
        }
        rows = conn.execute(
            select(
                tr.c.tx_id,
                tr.c.token,
                tr.c.from_address,
                tr.c.to_address,
                tr.c.amount_raw,
                tr.c.block_time,
                tr.c.provenance_json,
            )
            .where(
                tr.c.chain == ctx.chain,
                tr.c.token_id.is_(None),
                tr.c.token.is_not(None),
                tr.c.block_time.is_not(None),
                tr.c.block_time >= since,
                tr.c.tx_id.not_in(orphaned_tx_ids(ctx.chain)),
            )
            .order_by(tr.c.block_time.desc())
            .limit(cap + 1)
        ).all()
    truncated = len(rows) > cap
    rows = rows[:cap]
    per_token: dict[str, list[Any]] = defaultdict(list)
    for r in rows:
        if str(r.token) in pooled:
            per_token[str(r.token)].append(r)
    out: list[RawCandidate] = []
    excluded = pools | {zero}
    for token, transfers in per_token.items():
        if len(transfers) < ctx.cfg.holder_min_transfers:
            continue
        net: dict[str, int] = defaultdict(int)
        for t in transfers:
            try:
                amount = int(t.amount_raw)
            except ValueError:
                continue
            if t.from_address:
                net[str(t.from_address)] -= amount
            if t.to_address:
                net[str(t.to_address)] += amount
        holders = {a: v for a, v in net.items() if v > 0 and a not in excluded}
        total = sum(holders.values())
        if len(holders) < ctx.cfg.holder_min_addresses or total <= 0:
            continue
        top = sorted(holders.items(), key=lambda kv: kv[1], reverse=True)[:3]
        share = sum(v for _, v in top) / total
        if share < ctx.cfg.holder_concentration_share:
            continue
        top_set = {a for a, _ in top}
        evidence = [str(t.tx_id) for t in transfers if t.to_address in top_set][:5]
        finalized = all(
            '"finality":"finalized"' in str(t.provenance_json).replace(" ", "") for t in transfers
        )
        newest = max(t.block_time for t in transfers)
        out.append(
            RawCandidate(
                detector_id="holder_concentration",
                detector_version="1",
                family="holders",
                chain=ctx.chain,
                subject_kind="token",
                subject_ref=token,
                title=f"Top 3 addresses took {share:.0%} of net inflow in stored transfers",
                key=f"holders:{ctx.chain}:{token}",
                observed_at=newest,
                tx_ids=evidence,
                evidence_finalized=finalized,
                magnitude=clamp((share - 0.5) / 0.5),
                baseline_reliability=reliability_from_count(len(transfers)),
                confidence_cap=0.4,
                z=share,
                explanation={
                    "statement": (
                        "Most of the net inflow visible in stored transfers went to a few "
                        "addresses."
                    ),
                    "observed": {
                        "token": token,
                        "top_share": share,
                        "top_addresses": [{"address": a, "net_inflow_raw": str(v)} for a, v in top],
                        "addresses_with_positive_net_inflow": len(holders),
                        "transfers_used": len(transfers),
                    },
                    "method": (
                        "Net inflow per address over stored transfers; pools and the zero "
                        "address are excluded"
                    ),
                    "caveat": (
                        "This is observed net flow inside the stored window, not balances or "
                        "holdings: earlier balances, unstored transfers and contracts that hold "
                        "tokens for many users are invisible. It says nothing about who owns the "
                        "addresses."
                    ),
                },
                notes={
                    "magnitude": "(top-3 share - 0.5) / 0.5",
                    "baseline_reliability": f"{len(transfers)} stored transfers of this token",
                },
            )
        )
    return DetectorOutput(out, len(rows), truncated)


# --------------------------------------------------------------------------- solana entities

_SOLANA_KINDS = {
    "mint": ("token", "tokens", "New token mint initialised", 0.35),
    "program": ("contract", "contracts", "New program deployed", 0.3),
    "pool": ("pool", "pools", "New liquidity pool initialised", 0.45),
}


def solana_new_entities(ctx: Context) -> DetectorOutput:
    """Mints, programs and pools announced by instructions in finalized slots."""
    if ctx.chain != "solana":
        return DetectorOutput([], 0, False)
    sd = schema.solana_discoveries
    recent_start = ctx.now - timedelta(hours=ctx.cfg.lookback_hours)
    cap = ctx.cfg.max_rows_scanned
    with ctx.repo.transaction() as conn:
        rows = conn.execute(
            select(sd)
            .where(sd.c.block_time.is_not(None), sd.c.block_time >= recent_start)
            .order_by(sd.c.block_time.desc())
            .limit(cap + 1)
        ).all()
    truncated = len(rows) > cap
    rows = rows[:cap]
    out: list[RawCandidate] = []
    for r in rows:
        subject_kind, family, title, cap_conf = _SOLANA_KINDS[str(r.kind)]
        detail = json.loads(str(r.detail_json))
        out.append(
            RawCandidate(
                detector_id="solana_new_entities",
                detector_version="1",
                family=family,
                chain=ctx.chain,
                subject_kind=subject_kind,
                subject_ref=str(r.ref),
                title=title,
                key=f"solana:{r.kind}:{r.ref}",
                observed_at=r.block_time,
                tx_ids=[str(r.signature)],
                evidence_finalized=True,
                magnitude=0.3,
                baseline_reliability=0.2,
                confidence_cap=cap_conf,
                z=1.0,
                explanation={
                    "statement": (
                        f"A finalized slot contains an instruction creating this {r.kind}."
                    ),
                    "observed": {
                        "kind": r.kind,
                        "ref": r.ref,
                        "slot": r.slot,
                        "blockhash": r.blockhash,
                        **detail,
                    },
                    "method": "Compiled-instruction layouts verified against official sources",
                    "caveat": (
                        "An instruction was seen; this does not show the mint, program or pool "
                        "is legitimate, funded, tradable or unique."
                    ),
                },
                notes={
                    "magnitude": "fixed 0.3: there is no baseline of creation rates yet",
                    "baseline_reliability": "fixed 0.2: no baseline of normal creation volume",
                },
            )
        )
    return DetectorOutput(out, len(rows), truncated)


# --------------------------------------------------------------------------- common funding


def common_funding_source(ctx: Context) -> DetectorOutput:
    """One wallet that sent native funds to several recipients that were new in the stored data."""
    tr, t = schema.transfers, schema.transactions
    cfg = ctx.cfg
    window_start = ctx.now - timedelta(hours=cfg.funding_window_hours)
    with ctx.repo.transaction() as conn:
        rows = conn.execute(
            select(tr.c.tx_id, tr.c.from_address, tr.c.to_address, tr.c.block_time)
            .where(
                tr.c.chain == ctx.chain,
                tr.c.token.is_(None),
                tr.c.from_address.is_not(None),
                tr.c.to_address.is_not(None),
                tr.c.block_time.is_not(None),
                tr.c.block_time >= window_start,
                tr.c.tx_id.not_in(orphaned_tx_ids(ctx.chain)),
            )
            .order_by(tr.c.block_time.desc())
            .limit(cfg.max_rows_scanned + 1)
        ).all()
        truncated = len(rows) > cfg.max_rows_scanned
        rows = rows[: cfg.max_rows_scanned]
        by_source: dict[str, dict[str, tuple[str, datetime]]] = defaultdict(dict)
        for r in rows:
            if r.from_address != r.to_address:
                by_source[str(r.from_address)].setdefault(
                    str(r.to_address), (str(r.tx_id), r.block_time)
                )
        out: list[RawCandidate] = []
        for source, recipients in list(by_source.items())[:MAX_SUBJECTS_PER_DETECTOR]:
            if len(recipients) < cfg.funding_min_recipients:
                continue
            fresh: dict[str, tuple[str, datetime]] = {}
            for recipient, (tx_id, when) in list(recipients.items())[:50]:
                signed = conn.execute(
                    select(t.c.id)
                    .where(
                        t.c.chain == ctx.chain,
                        t.c.from_address == recipient,
                        t.c.block_time < window_start,
                        canonical_transaction(),
                    )
                    .limit(1)
                ).first()
                received = conn.execute(
                    select(tr.c.id)
                    .where(
                        tr.c.chain == ctx.chain,
                        tr.c.to_address == recipient,
                        tr.c.block_time < window_start,
                        tr.c.tx_id.not_in(orphaned_tx_ids(ctx.chain)),
                    )
                    .limit(1)
                ).first()
                if signed is None and received is None:
                    fresh[recipient] = (tx_id, when)
            if len(fresh) < cfg.funding_min_recipients:
                continue
            newest = max(w for _t, w in fresh.values())
            out.append(
                RawCandidate(
                    detector_id="common_funding_source",
                    detector_version="1",
                    family="related_wallets",
                    chain=ctx.chain,
                    subject_kind="wallet",
                    subject_ref=source,
                    title=(
                        f"Wallet funded {len(fresh)} newly seen wallets "
                        f"within {cfg.funding_window_hours} hours"
                    ),
                    key=f"funding:{ctx.chain}:{source}:{window_start.date().isoformat()}",
                    observed_at=newest,
                    tx_ids=[tx for tx, _w in list(fresh.values())[:MAX_EVIDENCE_TXS]],
                    magnitude=clamp(len(fresh) / (3 * cfg.funding_min_recipients)),
                    baseline_reliability=clamp(len(fresh) / 10),
                    confidence_cap=0.5,
                    z=float(len(fresh)),
                    explanation={
                        "statement": (
                            "One wallet sent native funds to several wallets that have no earlier "
                            "stored activity. The transfers are observed; that they were the "
                            "recipients' first funding is a hypothesis, because collectors store "
                            "only transfers above a threshold and earlier activity may be missing."
                        ),
                        "observed": {
                            "source": source,
                            "newly_seen_recipients": sorted(fresh)[:MAX_EVIDENCE_TXS],
                            "recipient_count": len(fresh),
                            "window_hours": cfg.funding_window_hours,
                        },
                        "method": "native transfers from one sender to recipients with no earlier "
                        "stored transaction or transfer",
                        "data_censoring": (
                            "Only native transfers at or above the collector threshold are stored, "
                            "so 'no earlier activity' is relative to censored data."
                        ),
                        "not_claimed": (
                            "Shared funding does not show that the wallets have the same owner. "
                            "Exchanges, faucets and payroll produce the same pattern."
                        ),
                    },
                    notes={
                        "magnitude": (
                            f"{len(fresh)} recipients against a minimum of "
                            f"{cfg.funding_min_recipients}"
                        ),
                        "baseline_reliability": f"{len(fresh)} recipients (10 = full)",
                    },
                )
            )
    return DetectorOutput(out, len(rows), truncated)


@dataclass(frozen=True, slots=True)
class Detector:
    info: DetectorInfo
    run: Run


STORED_ONLY_COST = "Local database reads only. No RPC calls, no provider credits."

DETECTORS: tuple[Detector, ...] = (
    Detector(
        DetectorInfo(
            id="large_transfer_mad",
            version="1",
            family="large_transfers",
            title="Unusually large transfer",
            method=(
                "Median/MAD robust z-score of log10(amount) per asset against the stored baseline"
            ),
            data_required="Stored transfers with block time",
            coverage="Transfers stored by ingestion for ingested wallets and their token accounts",
            limitations=(
                "Needs a baseline of stored transfers for the same asset. Says nothing about "
                "transfers BIP never stored. Large is relative to the stored baseline, which "
                "for native assets omits transfers below the collector threshold."
            ),
            refresh="On demand or per scheduled pass; reads the last baseline_days",
            cost=STORED_ONLY_COST,
            confidence_cap=0.8,
            chains=("ethereum", "solana"),
        ),
        large_transfers,
    ),
    Detector(
        DetectorInfo(
            id="dormant_reactivation",
            version="1",
            family="wallet_activity",
            title="Dormant wallet active again",
            method="Gap between consecutive stored transactions signed by the wallet",
            data_required="Stored transactions with sender and block time",
            coverage="Wallets that signed a stored transaction in the lookback window",
            limitations=(
                "Dormancy is judged on stored history only. The wallet may have been active "
                "elsewhere. A wallet with no earlier stored transaction is not reported."
            ),
            refresh="On demand or per scheduled pass",
            cost=STORED_ONLY_COST,
            confidence_cap=0.5,
            chains=("ethereum", "solana"),
        ),
        dormant_reactivation,
    ),
    Detector(
        DetectorInfo(
            id="activity_burst",
            version="1",
            family="abnormal_frequency",
            title="Abnormal transaction frequency",
            method="Median/MAD of daily signed-transaction counts on active days",
            data_required="Stored transactions with sender and block time",
            coverage="Wallets with enough active days in the stored baseline",
            limitations=(
                "Requires min_baseline active days; ignores wallets with short stored history."
            ),
            refresh="On demand or per scheduled pass",
            cost=STORED_ONLY_COST,
            confidence_cap=0.8,
            chains=("ethereum", "solana"),
        ),
        activity_burst,
    ),
    Detector(
        DetectorInfo(
            id="new_contract_deployment",
            version="1",
            family="contracts",
            title="New contract deployment",
            method="receipt.contractAddress of stored contract-creation transactions",
            data_required="Stored Ethereum transactions with complete receipts",
            coverage="Deployments inside stored transactions only",
            limitations=(
                "Ethereum only; Solana program deployment is not detected. Deployments by wallets "
                "BIP does not ingest are invisible."
            ),
            refresh="On demand or per scheduled pass",
            cost=STORED_ONLY_COST,
            confidence_cap=0.6,
            chains=("ethereum",),
        ),
        new_contract_deployment,
    ),
    Detector(
        DetectorInfo(
            id="new_liquidity_pool",
            version="1",
            family="pools",
            title="New liquidity pool",
            method="Uniswap v2/v3-style pool-creation logs read from finalized blocks",
            data_required="Pool events stored by the block collector",
            coverage="Pools created by factories whose event layout BIP recognises",
            limitations=(
                "Needs the block collector to have run. Liquidity, reserves, price and holder "
                "changes are not observed. The event layout is not proof of a DEX."
            ),
            refresh="Per collector run, then per discovery pass",
            cost=STORED_ONLY_COST,
            confidence_cap=0.4,
            chains=("ethereum",),
        ),
        new_liquidity_pool,
    ),
    Detector(
        DetectorInfo(
            id="new_token_activity",
            version="1",
            family="tokens",
            title="Token newly seen by the collector",
            method="Per-token counts of finalized ERC-20 Transfer logs",
            data_required="Token statistics stored by the block collector",
            coverage="Tokens with transfers in blocks the collector read",
            limitations=(
                "New means new to this database, not newly created. Needs the block collector "
                "to have run. Says nothing about legitimacy."
            ),
            refresh="Per collector run, then per discovery pass",
            cost=STORED_ONLY_COST,
            confidence_cap=0.35,
            chains=("ethereum",),
        ),
        new_token_activity,
    ),
    Detector(
        DetectorInfo(
            id="pool_reserve_change",
            version="1",
            family="liquidity",
            title="Sharp change in pool reserves",
            method=(
                "Relative change between stored v2-style Sync updates, median/MAD when "
                "history allows"
            ),
            data_required="Pool reserves stored by the block collector",
            coverage="Known v2-style pools; last update per block only",
            limitations=(
                "Reserves are not price or total value locked. v3-style pools are not covered. "
                "A large move can be an ordinary trade."
            ),
            refresh="Per collector run, then per discovery pass",
            cost=STORED_ONLY_COST,
            confidence_cap=0.5,
            chains=("ethereum",),
        ),
        pool_reserve_change,
    ),
    Detector(
        DetectorInfo(
            id="holder_concentration",
            version="1",
            family="holders",
            title="Concentrated net inflow",
            method=(
                "Share of positive net inflow held by the top three addresses over stored transfers"
            ),
            data_required="Stored ERC-20 transfers of tokens with a known pool",
            coverage="Tokens with enough stored transfers and addresses",
            limitations=(
                "Observed net flow inside the stored window, not balances or holdings. Cannot "
                "see contracts that hold tokens for many users. Says nothing about ownership."
            ),
            refresh="On demand or per scheduled pass",
            cost=STORED_ONLY_COST,
            confidence_cap=0.4,
            chains=("ethereum",),
        ),
        holder_concentration,
    ),
    Detector(
        DetectorInfo(
            id="solana_new_entities",
            version="1",
            family="solana_discovery",
            title="New Solana mint, program or pool",
            method="Compiled-instruction layouts of finalized slots, verified against sources",
            data_required="Discoveries stored by the Solana collector",
            coverage=(
                "Mints, upgradeable-loader deployments, Raydium AMM v4 and Orca Whirlpools pools"
            ),
            limitations=(
                "Needs the Solana collector to have run, and it covers only the slots it read. "
                "Other pool programs are not recognised. A creation instruction says nothing "
                "about legitimacy, liquidity or trading."
            ),
            refresh="Per collector run, then per discovery pass",
            cost=STORED_ONLY_COST,
            confidence_cap=0.45,
            chains=("solana",),
        ),
        solana_new_entities,
    ),
    Detector(
        DetectorInfo(
            id="common_funding_source",
            version="1",
            family="related_wallets",
            title="Common funding source",
            method="One sender funding several recipients that have no earlier stored activity",
            data_required="Stored native transfers",
            coverage="Funding visible in stored transfers inside the funding window",
            limitations=(
                "Observed transfers; 'first funding' is a hypothesis, not an attribution. "
                "Exchanges, faucets and payroll look the same. Recipients are 'new' only relative "
                "to stored data, which keeps only transfers above the collector threshold."
            ),
            refresh="On demand or per scheduled pass",
            cost=STORED_ONLY_COST,
            confidence_cap=0.5,
            chains=("ethereum", "solana"),
        ),
        common_funding_source,
    ),
)
