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

import math
import statistics
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import NamedTuple

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
        out: list[RawCandidate] = []
        for r in rows:
            pair = [r.token0, r.token1]
            earlier_tokens: set[str] = set()
            for col in (pe.c.token0, pe.c.token1):
                earlier_tokens.update(
                    str(x)
                    for x in conn.execute(
                        select(col).where(
                            pe.c.chain == ctx.chain,
                            pe.c.block_time < r.block_time,
                            col.in_(pair),
                        )
                    ).scalars()
                )
            unseen = [t for t in (r.token0, r.token1) if t not in earlier_tokens]
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
                            "stored activity. This is an observed funding relationship."
                        ),
                        "observed": {
                            "source": source,
                            "newly_seen_recipients": sorted(fresh)[:MAX_EVIDENCE_TXS],
                            "recipient_count": len(fresh),
                            "window_hours": cfg.funding_window_hours,
                        },
                        "method": "native transfers from one sender to recipients with no earlier "
                        "stored transaction or transfer",
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
                "transfers BIP never stored. Large is relative to the stored baseline."
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
            id="common_funding_source",
            version="1",
            family="related_wallets",
            title="Common funding source",
            method="One sender funding several recipients that have no earlier stored activity",
            data_required="Stored native transfers",
            coverage="Funding visible in stored transfers inside the funding window",
            limitations=(
                "An observed relationship, not an attribution. Exchanges, faucets and payroll "
                "look the same. Recipients are 'new' only relative to the stored data."
            ),
            refresh="On demand or per scheduled pass",
            cost=STORED_ONLY_COST,
            confidence_cap=0.5,
            chains=("ethereum", "solana"),
        ),
        common_funding_source,
    ),
)
