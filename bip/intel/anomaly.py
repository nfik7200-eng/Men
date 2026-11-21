"""Deterministic, explainable anomaly detectors.

Every detector declares its id, version, window, minimum data, baseline, thresholds, severity
rule and limitations. A run returns exactly one of:

* ``insufficient_data``: the minimum data requirement was not met; nothing is claimed;
* ``no_anomaly``: the detector ran and nothing crossed its threshold;
* ``anomaly``: a threshold was crossed; evidence and an explanation are attached;
* ``could_not_run``: an input the detector needs is unavailable (e.g. no timestamps).

Severity is never ``high`` when fewer than :data:`~bip.intel.common.FULL_SAMPLE` observations
back the baseline.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from bip.config.settings import IntelSettings
from bip.intel.common import (
    FULL_SAMPLE,
    MAD_SCALE,
    MAX_EVIDENCE,
    J,
    confidence,
    iso,
    median_mad,
    transfer_evidence,
    tx_evidence,
    verification_summary,
)
from bip.intel.dataset import TransferRow, TxRow, WalletDataset


@dataclass(frozen=True, slots=True)
class DetectorSpec:
    detector_id: str
    version: str
    title: str
    window: str
    minimum_data: str
    baseline: str
    thresholds: str
    severity_rule: str
    limitations: tuple[str, ...]

    def describe(self) -> J:
        return {
            "detector_id": self.detector_id,
            "version": self.version,
            "title": self.title,
            "window": self.window,
            "minimum_data": self.minimum_data,
            "baseline": self.baseline,
            "thresholds": self.thresholds,
            "severity_rule": self.severity_rule,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class Context:
    """Inputs beyond the wallet's own dataset."""

    now: datetime
    first_seen_targets: dict[str, datetime | None]
    """Earliest time each contract/program appears anywhere in the BIP database."""


def _out(
    spec: DetectorSpec,
    status: str,
    explanation: str,
    *,
    window: tuple[datetime | None, datetime | None] = (None, None),
    severity: str | None = None,
    sample: int = 0,
    baseline: J | None = None,
    observed: J | None = None,
    support_tx: list[TxRow] | None = None,
    support_tr: list[TransferRow] | None = None,
) -> J:
    support_tx = support_tx or []
    support_tr = support_tr or []
    statuses = [t.verification for t in support_tx] + [r.verification for r in support_tr]
    if severity == "high" and sample < FULL_SAMPLE:
        severity = "medium"  # never high from a small sample
    return {
        **spec.describe(),
        "status": status,
        "severity": severity if status == "anomaly" else None,
        "confidence": confidence(sample, statuses) if status == "anomaly" else None,
        "window_start": iso(window[0]),
        "window_end": iso(window[1]),
        "baseline_values": baseline,
        "observed": observed,
        "evidence": {
            "transactions": [tx_evidence(t) for t in support_tx[:MAX_EVIDENCE]],
            "transfers": [transfer_evidence(r) for r in support_tr[:MAX_EVIDENCE]],
            "total": len({t.tx_id for t in support_tx} | {r.tx_id for r in support_tr}),
        },
        "verification": verification_summary(statuses),
        "explanation": explanation,
    }


def _daily(items: list[tuple[date, object]], first: date, last: date) -> list[int]:
    counts: dict[date, int] = defaultdict(int)
    for d, _ in items:
        counts[d] += 1
    return [counts.get(first + timedelta(days=i), 0) for i in range((last - first).days + 1)]


SPIKE = DetectorSpec(
    "tx_frequency_spike",
    "1.0.0",
    "Transaction frequency spike",
    window="Daily buckets from the first to the last retrieved transaction; the last day is "
    "tested against the preceding days.",
    minimum_data="At least 8 calendar days spanned and 10 timestamped transactions.",
    baseline="Median and MAD of daily counts before the last day.",
    thresholds="Last day >= max(5, spike_factor x median, median + 6 x 1.4826 x MAD).",
    severity_rule="low < 2x the threshold <= medium; high only if >= 3x and >= 30 baseline "
    "transactions.",
    limitations=(
        "Days without retrieved transactions count as zero only inside the retrieved "
        "range; a bounded retrieval can make earlier days look quiet.",
    ),
)


def tx_frequency_spike(ds: WalletDataset, cfg: IntelSettings, ctx: Context) -> J:
    timed = [t for t in ds.transactions if t.block_time is not None]
    if not ds.transactions:
        return _out(SPIKE, "insufficient_data", "No transactions retrieved.")
    if not timed:
        return _out(SPIKE, "could_not_run", "No retrieved transaction has a block time.")
    first = min(t.block_time for t in timed if t.block_time).date()
    last = max(t.block_time for t in timed if t.block_time).date()
    if (last - first).days + 1 < 8 or len(timed) < 10:
        return _out(
            SPIKE,
            "insufficient_data",
            f"{len(timed)} transactions over "
            f"{(last - first).days + 1} days; needs 10 over at least 8 days.",
        )
    series = _daily([(t.block_time.date(), t) for t in timed if t.block_time], first, last)
    base, today = series[:-1], series[-1]
    med, mad = median_mad([float(x) for x in base])
    threshold = max(5.0, cfg.spike_factor * med, med + 6 * MAD_SCALE * mad)
    times = [t.block_time for t in timed if t.block_time]
    window = (min(times), max(times))
    baseline = {"median": med, "mad": mad, "days": len(base), "transactions": sum(base)}
    observed = {"last_day": last.isoformat(), "count": today, "threshold": round(threshold, 2)}
    if today < threshold:
        return _out(
            SPIKE,
            "no_anomaly",
            f"Last day had {today} transactions; threshold {threshold:.1f}.",
            window=window,
            baseline=baseline,
            observed=observed,
        )
    ratio = today / threshold
    severity = "high" if ratio >= 3 else "medium" if ratio >= 2 else "low"
    support = [t for t in timed if t.block_time and t.block_time.date() == last]
    return _out(
        SPIKE,
        "anomaly",
        f"{today} transactions on {last} versus a median of {med:g} per day "
        f"over {len(base)} prior days (threshold {threshold:.1f}).",
        window=window,
        severity=severity,
        sample=sum(base),
        baseline=baseline,
        observed=observed,
        support_tx=support,
    )


LARGE = DetectorSpec(
    "large_transfer",
    "1.0.0",
    "Unusually large transfer",
    window="All retrieved transfers per asset (raw units; ERC-721 excluded).",
    minimum_data="At least 10 earlier non-zero transfers of the same asset.",
    baseline="Median and MAD of log10(amount) over earlier transfers of that asset.",
    thresholds="Robust z = (log10(x) - median) / (1.4826 x MAD) >= large_transfer_mad_z; when "
    "MAD is 0, x >= 10 x the median amount.",
    severity_rule="low; medium at 1.5x the z threshold; high only with >= 30 baseline transfers "
    "and 2x the z threshold.",
    limitations=(
        "Raw token units only; no prices, so sizes are not comparable across assets.",
        "Only transfers retrieved by BIP form the baseline.",
    ),
)


def large_transfer(ds: WalletDataset, cfg: IntelSettings, ctx: Context) -> J:
    by_asset: dict[str, list[TransferRow]] = defaultdict(list)
    for r in sorted(ds.transfers, key=lambda r: (r.position or 0, r.event_key)):
        if r.direction in {"in", "out"} and r.standard != "erc721" and r.amount_raw > 0:
            by_asset[r.asset].append(r)
    eligible = {a: rs for a, rs in by_asset.items() if len(rs) >= 11}
    if not eligible:
        return _out(
            LARGE,
            "insufficient_data",
            "No asset has 10 earlier non-zero transfers to form a baseline.",
        )
    hits: list[tuple[float, TransferRow, J, int]] = []
    for asset, rs in eligible.items():
        for i in range(10, len(rs)):
            prior = [math.log10(r.amount_raw) for r in rs[:i]]
            med, mad = median_mad(prior)
            x = math.log10(rs[i].amount_raw)
            if mad > 0:
                z = (x - med) / (MAD_SCALE * mad)
                hit = z >= cfg.large_transfer_mad_z
            else:
                z = math.inf if x > med else 0.0
                hit = x >= med + 1  # 10x the (constant) median
            if hit:
                base = {
                    "asset": asset,
                    "median_log10": round(med, 4),
                    "mad_log10": round(mad, 4),
                    "baseline_transfers": i,
                }
                hits.append((z, rs[i], base, i))
    if not hits:
        return _out(
            LARGE,
            "no_anomaly",
            f"Checked {sum(map(len, eligible.values()))} transfers "
            f"across {len(eligible)} assets; none exceeded the threshold.",
        )
    z, top, base, n = max(hits, key=lambda h: (h[0], h[1].tx_id))
    zt = cfg.large_transfer_mad_z
    severity = "high" if z >= 2 * zt else "medium" if z >= 1.5 * zt else "low"
    return _out(
        LARGE,
        "anomaly",
        f"{len(hits)} transfer(s) far above their asset's earlier sizes; the "
        f"largest deviation is in {top.tx_id} (robust z {'inf' if math.isinf(z) else f'{z:.1f}'})"
        ".",
        window=(top.block_time, top.block_time),
        severity=severity,
        sample=n,
        baseline=base,
        observed={
            "amount_raw": str(top.amount_raw),
            "robust_z": None if math.isinf(z) else round(z, 2),
            "hits": len(hits),
        },
        support_tr=[h[1] for h in hits],
    )


SURGE = DetectorSpec(
    "new_counterparty_surge",
    "1.0.0",
    "New counterparty surge",
    window="Daily buckets; the last day is tested against the preceding days.",
    minimum_data="At least 8 calendar days spanned and 10 counterparty transfers.",
    baseline="Median and MAD of first-time counterparties per day before the last day.",
    thresholds="New counterparties on the last day >= max(3, spike_factor x median, median + "
    "6 x 1.4826 x MAD).",
    severity_rule="low; medium at 2x the threshold; high only at 3x with >= 30 baseline transfers.",
    limitations=("'New' means first seen in the retrieved dataset, not first ever.",),
)


def new_counterparty_surge(ds: WalletDataset, cfg: IntelSettings, ctx: Context) -> J:
    rs = sorted(
        (r for r in ds.transfers if r.party and r.block_time and r.direction in {"in", "out"}),
        key=lambda r: (r.position or 0, r.event_key),
    )
    if len(rs) < 10:
        return _out(
            SURGE, "insufficient_data", f"{len(rs)} timestamped counterparty transfers; needs 10."
        )
    first_seen: dict[str, TransferRow] = {}
    for r in rs:
        first_seen.setdefault(r.party or "", r)
    first = rs[0].block_time.date()  # type: ignore[union-attr]
    last = rs[-1].block_time.date()  # type: ignore[union-attr]
    if (last - first).days + 1 < 8:
        return _out(SURGE, "insufficient_data", "Retrieved transfers span fewer than 8 days.")
    news = list(first_seen.values())
    series = _daily([(r.block_time.date(), r) for r in news if r.block_time], first, last)
    base, today = series[:-1], series[-1]
    med, mad = median_mad([float(x) for x in base])
    threshold = max(3.0, cfg.spike_factor * med, med + 6 * MAD_SCALE * mad)
    baseline = {"median": med, "mad": mad, "days": len(base)}
    observed = {
        "last_day": last.isoformat(),
        "new_counterparties": today,
        "threshold": round(threshold, 2),
    }
    window = (rs[0].block_time, rs[-1].block_time)
    if today < threshold:
        return _out(
            SURGE,
            "no_anomaly",
            f"{today} new counterparties on the last day; threshold {threshold:.1f}.",
            window=window,
            baseline=baseline,
            observed=observed,
        )
    ratio = today / threshold
    support = [r for r in news if r.block_time and r.block_time.date() == last]
    return _out(
        SURGE,
        "anomaly",
        f"{today} counterparties first seen on {last}, versus a median of {med:g} per day before.",
        window=window,
        severity="high" if ratio >= 3 else "medium" if ratio >= 2 else "low",
        sample=len(rs) - len(support),
        baseline=baseline,
        observed=observed,
        support_tr=support,
    )


REPEAT = DetectorSpec(
    "rapid_repeat_transfers",
    "1.0.0",
    "Rapid repeated transfers",
    window="Sliding window of burst_window_minutes per (counterparty, asset, direction).",
    minimum_data="At least burst_count timestamped counterparty transfers.",
    baseline="None (absolute rule): repetition within a short window.",
    thresholds=">= burst_count transfers with the same counterparty, asset and direction "
    "within burst_window_minutes.",
    severity_rule="low; medium at 2x burst_count; high only at 4x and >= 30 transfers.",
    limitations=("Batching, payroll and automated services also produce this pattern.",),
)


def rapid_repeat_transfers(ds: WalletDataset, cfg: IntelSettings, ctx: Context) -> J:
    groups: dict[tuple[str, str, str], list[TransferRow]] = defaultdict(list)
    timed = [r for r in ds.transfers if r.party and r.block_time and r.direction in {"in", "out"}]
    if len(timed) < cfg.burst_count:
        return _out(
            REPEAT,
            "insufficient_data",
            f"{len(timed)} timestamped counterparty transfers; needs {cfg.burst_count}.",
        )
    for r in timed:
        groups[(r.party or "", r.asset, r.direction)].append(r)
    span = timedelta(minutes=cfg.burst_window_minutes)
    best: list[TransferRow] = []
    for rs in groups.values():
        rs.sort(key=lambda r: (r.block_time, r.event_key))  # type: ignore[arg-type,return-value]
        j = 0
        for i in range(len(rs)):
            while rs[i].block_time - rs[j].block_time > span:  # type: ignore[operator]
                j += 1
            if i - j + 1 > len(best):
                best = rs[j : i + 1]
    if len(best) < cfg.burst_count:
        return _out(
            REPEAT,
            "no_anomaly",
            f"At most {len(best)} transfers with one counterparty "
            f"within {cfg.burst_window_minutes} minutes.",
        )
    k = len(best) / cfg.burst_count
    return _out(
        REPEAT,
        "anomaly",
        f"{len(best)} transfers of {best[0].asset} {best[0].direction} with "
        f"{best[0].party} within {cfg.burst_window_minutes} minutes.",
        window=(best[0].block_time, best[-1].block_time),
        severity="high" if k >= 4 else "medium" if k >= 2 else "low",
        sample=len(best),
        observed={"count": len(best), "counterparty": best[0].party, "asset": best[0].asset},
        support_tr=best,
    )


NEWNEW = DetectorSpec(
    "new_wallet_new_contract",
    "1.0.0",
    "Newly observed wallet using a newly observed contract",
    window="newly_observed_days before the analysis time.",
    minimum_data="At least one timestamped transaction with a call target or program.",
    baseline="First time each target appears anywhere in BIP's stored data.",
    thresholds="Wallet first observed and target first observed both within newly_observed_days.",
    severity_rule="Always low: informational, a prompt for review.",
    limitations=(
        "First observation in BIP's data depends on what was ingested; it is not a "
        "deployment date.",
    ),
)


def new_wallet_new_contract(ds: WalletDataset, cfg: IntelSettings, ctx: Context) -> J:
    timed = [t for t in ds.transactions if t.block_time]
    if not timed:
        return _out(NEWNEW, "insufficient_data", "No timestamped transactions.")
    horizon = ctx.now - timedelta(days=cfg.newly_observed_days)
    first_wallet = min(t.block_time for t in timed if t.block_time)
    if first_wallet < horizon:
        return _out(NEWNEW, "no_anomaly", "The wallet was first observed before the window.")
    support: list[TxRow] = []
    targets: set[str] = set()
    for t in timed:
        cands = (
            list(t.program_ids)
            if ds.chain == "solana"
            else ([t.to_address] if t.to_address and t.from_address == ds.address else [])
        )
        for c in cands:
            seen = ctx.first_seen_targets.get(c)
            if seen is not None and seen >= horizon:
                support.append(t)
                targets.add(c)
    if not support:
        return _out(NEWNEW, "no_anomaly", "No call target is newly observed.")
    return _out(
        NEWNEW,
        "anomaly",
        f"Wallet first observed {first_wallet.date()} interacts with "
        f"{len(targets)} target(s) also first observed within {cfg.newly_observed_days} days.",
        window=(horizon, ctx.now),
        severity="low",
        sample=len(support),
        observed={"targets": sorted(targets)},
        support_tx=support,
    )


DETECTORS: tuple[tuple[DetectorSpec, Callable[[WalletDataset, IntelSettings, Context], J]], ...] = (
    (SPIKE, tx_frequency_spike),
    (LARGE, large_transfer),
    (SURGE, new_counterparty_surge),
    (REPEAT, rapid_repeat_transfers),
    (NEWNEW, new_wallet_new_contract),
)


def run_detectors(ds: WalletDataset, cfg: IntelSettings, ctx: Context) -> list[J]:
    out: list[J] = []
    for spec, fn in DETECTORS:
        try:
            out.append(fn(ds, cfg, ctx))
        except (ValueError, ArithmeticError) as exc:  # deterministic data problems
            out.append(
                _out(spec, "could_not_run", f"Detector failed on this data: {type(exc).__name__}.")
            )
    return out
