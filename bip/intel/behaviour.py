"""Descriptive behaviour rules.

Each rule describes *what was observed* in the retrieved dataset. Rules never assign motive or
identity. Where a pattern resembles a known category, the label says "possible ... pattern"
and the claim type is ``hypothesis``.

Statuses: ``detected``, ``not_detected``, ``not_enough_data``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from bip.config.settings import IntelSettings
from bip.intel.common import (
    MAX_EVIDENCE,
    J,
    confidence,
    iso,
    transfer_evidence,
    tx_evidence,
    verification_summary,
)
from bip.intel.dataset import TransferRow, TxRow, WalletDataset
from bip.intel.profile import observed_range

RULES_VERSION = "1.0.0"


def _result(
    rule_id: str,
    name: str,
    status: str,
    *,
    inputs: J,
    window: J,
    explanation: str,
    support_tx: list[TxRow] | None = None,
    support_tr: list[TransferRow] | None = None,
    magnitude: J | None = None,
    claim_type: str = "statistical_observation",
) -> J:
    support_tx = support_tx or []
    support_tr = support_tr or []
    statuses = [t.verification for t in support_tx] + [r.verification for r in support_tr]
    n = len({t.tx_id for t in support_tx} | {r.tx_id for r in support_tr})
    return {
        "rule_id": rule_id,
        "rule_version": RULES_VERSION,
        "name": name,
        "status": status,
        "claim_type": claim_type if status == "detected" else "statistical_observation",
        "inputs": inputs,
        "window": window,
        "magnitude": magnitude,
        "supporting_count": n,
        "supporting_transactions": [tx_evidence(t) for t in support_tx[:MAX_EVIDENCE]],
        "supporting_transfers": [transfer_evidence(r) for r in support_tr[:MAX_EVIDENCE]],
        "confidence": confidence(n, statuses) if status == "detected" else None,
        "verification": verification_summary(statuses),
        "explanation": explanation,
    }


def _window(ds: WalletDataset) -> J:
    first, last = observed_range(ds)
    return {"start": iso(first), "end": iso(last), "basis": "retrieved dataset"}


def high_frequency(ds: WalletDataset, cfg: IntelSettings) -> J:
    timed = [t for t in ds.transactions if t.block_time is not None]
    days = Counter(t.block_time.date() for t in timed if t.block_time)
    inputs = {"min_transactions": 10, "threshold_per_active_day": 10}
    name = "High transaction frequency"
    if len(timed) < 10:
        return _result(
            "high_frequency",
            name,
            "not_enough_data",
            inputs=inputs,
            window=_window(ds),
            explanation=f"Only {len(timed)} timestamped "
            "transactions retrieved; at least 10 are needed.",
        )
    rate = len(timed) / len(days)
    detected = rate >= 10
    return _result(
        "high_frequency",
        name,
        "detected" if detected else "not_detected",
        inputs=inputs,
        window=_window(ds),
        support_tx=timed if detected else [],
        magnitude={"transactions_per_active_day": round(rate, 2), "active_days": len(days)},
        explanation=f"{len(timed)} transactions over {len(days)} active days "
        f"({rate:.1f} per active day) in the retrieved dataset.",
    )


def repeated_interactions(ds: WalletDataset, cfg: IntelSettings) -> J:
    by_target: dict[str, list[TxRow]] = defaultdict(list)
    for t in ds.transactions:
        if ds.chain == "solana":
            for p in set(t.program_ids):
                by_target[p].append(t)
        elif t.from_address == ds.address and t.to_address:
            by_target[t.to_address].append(t)
    inputs = {"min_interactions": 5}
    name = "Repeated contract / program interactions"
    if not by_target:
        return _result(
            "repeated_interactions",
            name,
            "not_enough_data",
            inputs=inputs,
            window=_window(ds),
            explanation="No outgoing calls or program invocations retrieved.",
        )
    target, txs = max(by_target.items(), key=lambda kv: (len(kv[1]), kv[0]))
    detected = len(txs) >= 5
    return _result(
        "repeated_interactions",
        name,
        "detected" if detected else "not_detected",
        inputs=inputs,
        window=_window(ds),
        support_tx=txs if detected else [],
        magnitude={"target": target, "interactions": len(txs), "distinct_targets": len(by_target)},
        explanation=f"Most frequent target {target} appears in {len(txs)} retrieved "
        "transactions. Repetition alone says nothing about intent.",
    )


def _party_transfers(ds: WalletDataset) -> dict[str, list[TransferRow]]:
    out: dict[str, list[TransferRow]] = defaultdict(list)
    for r in ds.transfers:
        if r.party and r.direction in {"in", "out"}:
            out[r.party].append(r)
    return out


def repeated_counterparties(ds: WalletDataset, cfg: IntelSettings) -> J:
    parties = _party_transfers(ds)
    inputs = {"min_transfers_with_one_counterparty": 5}
    name = "Repeated counterparties"
    if not parties:
        return _result(
            "repeated_counterparties",
            name,
            "not_enough_data",
            inputs=inputs,
            window=_window(ds),
            explanation="No counterparty transfers retrieved.",
        )
    repeated = {p: rs for p, rs in parties.items() if len(rs) >= 5}
    support = [r for rs in repeated.values() for r in rs]
    return _result(
        "repeated_counterparties",
        name,
        "detected" if repeated else "not_detected",
        inputs=inputs,
        window=_window(ds),
        support_tr=support,
        magnitude={
            "repeated_counterparties": len(repeated),
            "max_transfers_with_one": max(len(rs) for rs in parties.values()),
        },
        explanation=f"{len(repeated)} counterparties appear in 5 or more retrieved transfers.",
    )


def concentration(ds: WalletDataset, cfg: IntelSettings) -> J:
    parties = _party_transfers(ds)
    total = sum(len(rs) for rs in parties.values())
    inputs = {"min_transfers": 10, "share_threshold": cfg.concentration_share}
    name = "Counterparty concentration"
    if total < 10:
        return _result(
            "concentration",
            name,
            "not_enough_data",
            inputs=inputs,
            window=_window(ds),
            explanation=f"Only {total} counterparty transfers; at least 10 are needed.",
        )
    top, rs = max(parties.items(), key=lambda kv: (len(kv[1]), kv[0]))
    share = len(rs) / total
    detected = share >= cfg.concentration_share
    return _result(
        "concentration",
        name,
        "detected" if detected else "not_detected",
        inputs=inputs,
        window=_window(ds),
        support_tr=rs if detected else [],
        magnitude={"top_counterparty": top, "share": round(share, 4), "transfers": total},
        explanation=f"The top counterparty accounts for {share:.0%} of {total} retrieved "
        "counterparty transfers.",
    )


def bursts(ds: WalletDataset, cfg: IntelSettings) -> J:
    timed = sorted(
        (t for t in ds.transactions if t.block_time),
        key=lambda t: t.block_time or datetime.min.replace(tzinfo=UTC),
    )
    span = timedelta(minutes=cfg.burst_window_minutes)
    inputs = {"burst_count": cfg.burst_count, "window_minutes": cfg.burst_window_minutes}
    name = "Activity bursts"
    if len(timed) < cfg.burst_count:
        return _result(
            "bursts",
            name,
            "not_enough_data",
            inputs=inputs,
            window=_window(ds),
            explanation=f"Fewer than {cfg.burst_count} timestamped transactions.",
        )
    found: list[list[TxRow]] = []
    start = 0
    while start < len(timed):
        end = start
        t0 = timed[start].block_time
        assert t0 is not None  # noqa: S101 - filtered above
        while end + 1 < len(timed) and (timed[end + 1].block_time or t0) - t0 <= span:
            end += 1
        if end - start + 1 >= cfg.burst_count:
            found.append(timed[start : end + 1])
            start = end + 1
        else:
            start += 1
    best = max(found, key=len) if found else []
    bursts_found = len(found)
    return _result(
        "bursts",
        name,
        "detected" if best else "not_detected",
        inputs=inputs,
        window=_window(ds),
        support_tx=best,
        magnitude={"bursts": bursts_found, "largest_burst": len(best)},
        explanation=f"{bursts_found} non-overlapping bursts of at least {cfg.burst_count} "
        f"transactions within {cfg.burst_window_minutes} minutes.",
    )


def newly_observed(ds: WalletDataset, cfg: IntelSettings, now: datetime) -> J:
    first, _ = observed_range(ds)
    inputs = {"days": cfg.newly_observed_days}
    name = "Newly observed wallet"
    if first is None:
        return _result(
            "newly_observed",
            name,
            "not_enough_data",
            inputs=inputs,
            window=_window(ds),
            explanation="No timestamped records retrieved.",
        )
    age = now - first
    detected = age <= timedelta(days=cfg.newly_observed_days)
    return _result(
        "newly_observed",
        name,
        "detected" if detected else "not_detected",
        inputs=inputs,
        window=_window(ds),
        support_tx=[t for t in ds.transactions if t.block_time == first] if detected else [],
        magnitude={"first_observed": iso(first), "days_since_first_observed": age.days},
        explanation=f"Earliest retrieved record is {age.days} days old. This is the first "
        "*observation* in BIP's data, not proof of the wallet's creation date.",
    )


def repeated_token_transfers(ds: WalletDataset, cfg: IntelSettings) -> J:
    by_token: dict[str, list[TransferRow]] = defaultdict(list)
    for r in ds.transfers:
        if r.asset != "native" and r.direction in {"in", "out"}:
            by_token[r.asset].append(r)
    inputs = {"min_transfers_per_token": 5}
    name = "Repeated token transfers"
    if not by_token:
        return _result(
            "repeated_token_transfers",
            name,
            "not_enough_data",
            inputs=inputs,
            window=_window(ds),
            explanation="No token transfers retrieved.",
        )
    repeated = {k: v for k, v in by_token.items() if len(v) >= 5}
    return _result(
        "repeated_token_transfers",
        name,
        "detected" if repeated else "not_detected",
        inputs=inputs,
        window=_window(ds),
        support_tr=[r for v in repeated.values() for r in v],
        magnitude={"tokens": sorted(repeated), "max_transfers": max(map(len, by_token.values()))},
        explanation=f"{len(repeated)} tokens moved in 5 or more retrieved transfers.",
    )


def large_value_changes(ds: WalletDataset, cfg: IntelSettings) -> J:
    by_asset: dict[str, list[TransferRow]] = defaultdict(list)
    for r in ds.transfers:
        if r.direction in {"in", "out"} and r.standard != "erc721" and r.amount_raw > 0:
            by_asset[r.asset].append(r)
    inputs = {"min_transfers_per_asset": 5, "factor_over_median": 10}
    name = "Large value changes"
    eligible = {a: rs for a, rs in by_asset.items() if len(rs) >= 5}
    if not eligible:
        return _result(
            "large_value_changes",
            name,
            "not_enough_data",
            inputs=inputs,
            window=_window(ds),
            explanation="No asset has 5 or more retrieved transfers with a non-zero amount.",
        )
    large: list[TransferRow] = []
    for rs in eligible.values():
        amounts = sorted(r.amount_raw for r in rs)
        med = amounts[len(amounts) // 2]
        large += [r for r in rs if med > 0 and r.amount_raw >= 10 * med]
    return _result(
        "large_value_changes",
        name,
        "detected" if large else "not_detected",
        inputs=inputs,
        window=_window(ds),
        support_tr=large,
        magnitude={"large_transfers": len(large), "assets_checked": len(eligible)},
        explanation=f"{len(large)} transfers are at least 10x their asset's median retrieved "
        "transfer size (raw units, per asset; no prices used).",
    )


def service_like_pattern(ds: WalletDataset, cfg: IntelSettings) -> J:
    parties = _party_transfers(ds)
    ins = {p for p, rs in parties.items() if any(r.direction == "in" for r in rs)}
    outs = {p for p, rs in parties.items() if any(r.direction == "out" for r in rs)}
    inputs = {"min_distinct_senders": 25, "min_distinct_recipients": 25}
    name = "Possible exchange-like or service-like activity pattern"
    if len(parties) < 10:
        return _result(
            "service_like_pattern",
            name,
            "not_enough_data",
            inputs=inputs,
            window=_window(ds),
            explanation="Fewer than 10 counterparties.",
        )
    detected = len(ins) >= 25 and len(outs) >= 25
    support = [r for p in (ins | outs) for r in parties[p]] if detected else []
    return _result(
        "service_like_pattern",
        name,
        "detected" if detected else "not_detected",
        inputs=inputs,
        window=_window(ds),
        support_tr=support,
        claim_type="hypothesis",
        magnitude={"distinct_senders": len(ins), "distinct_recipients": len(outs)},
        explanation=(
            f"{len(ins)} distinct senders and {len(outs)} distinct recipients. Many-to-many "
            "flows can resemble a service (exchange, bridge, payment processor), but this is "
            "a hypothesis only: BIP does not identify who operates the wallet."
        ),
    )


RULES: tuple[Callable[[WalletDataset, IntelSettings], J], ...] = (
    high_frequency,
    repeated_interactions,
    repeated_counterparties,
    concentration,
    bursts,
    repeated_token_transfers,
    large_value_changes,
    service_like_pattern,
)


def evaluate(ds: WalletDataset, cfg: IntelSettings, now: datetime) -> list[J]:
    return (
        [rule(ds, cfg) for rule in RULES[:5]]
        + [newly_observed(ds, cfg, now)]
        + [rule(ds, cfg) for rule in RULES[5:]]
    )
