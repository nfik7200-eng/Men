"""Reproducible wallet feature extraction (feature version 1.0.0).

Input: the scoped dataset from :func:`bip.intel.dataset.load_wallet` (stored observations
only) and an ``as_of`` time. **Leakage control:** only records with a block time at or before
``as_of`` are used; records without a block time are excluded from every feature and counted in
``coverage.untimed_records``.

Every feature has an availability:

* ``observed``: computed from at least the required data;
* ``unavailable``: the inputs needed do not exist (value ``None``, never 0). A count of 0 is
  ``observed`` only when the observation window exists (at least one timed record).

Values are plain floats so the vector is model-ready; models see ``None`` as missing plus an
explicit missing-indicator feature, so "missing" can never be mistaken for "zero".
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

from bip.intel.dataset import TransferRow, TxRow, WalletDataset

FEATURE_VERSION = "1.0.0"

# Order is part of the version: models depend on it.
FEATURE_NAMES: tuple[str, ...] = (
    "tx_count",
    "active_days",
    "span_days",
    "tx_per_active_day",
    "in_transfer_count",
    "out_transfer_count",
    "in_share",
    "distinct_counterparties",
    "counterparty_top_share",
    "counterparty_hhi",
    "distinct_assets",
    "token_transfer_share",
    "failed_tx_rate",
    "repeated_amount_share",
    "self_transfer_share",
    "burst_max_10min",
    "median_inter_tx_s",
    "inter_tx_cv",
    "hour_entropy",
    "weekend_share",
    "deployments",
    "distinct_call_targets",
    "call_target_top_share",
    "unknown_instruction_share",
    "nft_transfer_share",
    "days_since_first_seen",
    "recent_activity_ratio",
)

DESCRIPTIONS: dict[str, str] = {
    "tx_count": "Transactions observed in the window.",
    "active_days": "Distinct UTC days with a transaction.",
    "span_days": "Days between the first and last observed transaction.",
    "tx_per_active_day": "Transactions per active day.",
    "in_transfer_count": "Incoming transfers (native and token).",
    "out_transfer_count": "Outgoing transfers.",
    "in_share": "Incoming / (incoming + outgoing) transfers.",
    "distinct_counterparties": "Distinct transfer counterparties (owners where resolvable).",
    "counterparty_top_share": "Share of counterparty transfers with the top counterparty.",
    "counterparty_hhi": "Herfindahl index of counterparty transfer shares.",
    "distinct_assets": "Distinct assets transferred (native counts as one).",
    "token_transfer_share": "Share of transfers that are token (not native) transfers.",
    "failed_tx_rate": "Failed / transactions with a known status.",
    "repeated_amount_share": "Share of transfers whose (asset, amount) occurs at least twice.",
    "self_transfer_share": "Share of transfers between the wallet's own accounts.",
    "burst_max_10min": "Most transactions within any 10-minute window.",
    "median_inter_tx_s": "Median seconds between consecutive transactions.",
    "inter_tx_cv": "Coefficient of variation of inter-transaction gaps (low = regular).",
    "hour_entropy": "Entropy of the UTC hour-of-day distribution (bits).",
    "weekend_share": "Share of transactions on Saturday/Sunday (UTC).",
    "deployments": "Contract-creation transactions sent by the wallet.",
    "distinct_call_targets": "Distinct EVM call targets / Solana programs.",
    "call_target_top_share": "Share of calls to the most frequent target.",
    "unknown_instruction_share": "Solana: unknown instructions / instructions.",
    "nft_transfer_share": "Share of transfers that are ERC-721.",
    "days_since_first_seen": "Days between first observed activity and as_of.",
    "recent_activity_ratio": "Transactions in the last 7 days / in the 7 days before.",
}


@dataclass
class FeatureVector:
    chain: str
    address: str
    as_of: datetime
    values: dict[str, float | None]
    availability: dict[str, str]
    window: dict[str, str | None]
    coverage: dict[str, Any]
    feature_version: str = FEATURE_VERSION
    data_kind: str = "stored_observations"
    record_ids: list[str] = field(default_factory=list[str])

    def digest(self) -> str:
        body = json.dumps(
            {
                "v": self.feature_version,
                "chain": self.chain,
                "address": self.address,
                "as_of": self.as_of.isoformat(),
                "values": self.values,
            },
            sort_keys=True,
        )
        return hashlib.sha256(body.encode()).hexdigest()

    def public(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "address": self.address,
            "as_of": self.as_of.isoformat(),
            "feature_version": self.feature_version,
            "data_kind": self.data_kind,
            "values": self.values,
            "availability": self.availability,
            "window": self.window,
            "coverage": self.coverage,
            "digest": self.digest(),
            "descriptions": {k: DESCRIPTIONS[k] for k in FEATURE_NAMES},
        }


def _entropy(counts: list[int]) -> float:
    total = sum(counts)
    return -sum((c / total) * math.log2(c / total) for c in counts if c)


def extract(
    ds: WalletDataset, as_of: datetime, *, data_kind: str = "stored_observations"
) -> FeatureVector:
    txs = [t for t in ds.transactions if t.block_time is not None and t.block_time <= as_of]
    trs = [r for r in ds.transfers if r.block_time is not None and r.block_time <= as_of]
    untimed = sum(1 for t in ds.transactions if t.block_time is None) + sum(
        1 for r in ds.transfers if r.block_time is None
    )
    future = sum(1 for t in ds.transactions if t.block_time is not None and t.block_time > as_of)
    v: dict[str, float | None] = dict.fromkeys(FEATURE_NAMES)
    have_window = bool(txs or trs)

    def put(name: str, value: float | None) -> None:
        v[name] = None if value is None else float(value)

    times = sorted(t.block_time for t in txs if t.block_time is not None)
    if have_window:
        put("tx_count", len(txs))
        days = {t.date() for t in times}
        put("active_days", len(days))
        put("span_days", (times[-1] - times[0]).total_seconds() / 86400 if times else None)
        put("tx_per_active_day", len(txs) / len(days) if days else None)
        ins = [r for r in trs if r.direction == "in"]
        outs = [r for r in trs if r.direction == "out"]
        put("in_transfer_count", len(ins))
        put("out_transfer_count", len(outs))
        put("in_share", len(ins) / (len(ins) + len(outs)) if ins or outs else None)
        parties = Counter(r.party for r in ins + outs if r.party)
        total = sum(parties.values())
        put("distinct_counterparties", len(parties))
        put("counterparty_top_share", max(parties.values()) / total if total else None)
        put("counterparty_hhi", sum((c / total) ** 2 for c in parties.values()) if total else None)
        put("distinct_assets", len({r.asset for r in trs}))
        put(
            "token_transfer_share",
            sum(r.asset != "native" for r in trs) / len(trs) if trs else None,
        )
        put(
            "nft_transfer_share",
            sum(r.standard == "erc721" for r in trs) / len(trs) if trs else None,
        )
        known = [t for t in txs if t.status in {"success", "failed"}]
        put(
            "failed_tx_rate",
            sum(t.status == "failed" for t in known) / len(known) if known else None,
        )
        amounts = Counter((r.asset, r.amount_raw) for r in ins + outs)
        put(
            "repeated_amount_share",
            sum(c for c in amounts.values() if c >= 2) / len(ins + outs) if ins or outs else None,
        )
        put(
            "self_transfer_share",
            sum(r.direction == "self" for r in trs) / len(trs) if trs else None,
        )
        put(
            "deployments",
            sum(1 for t in txs if t.created_contract and t.from_address == ds.address),
        )
        targets = _targets(ds, txs)
        put("distinct_call_targets", len(targets))
        put(
            "call_target_top_share",
            max(targets.values()) / sum(targets.values()) if targets else None,
        )
        if ds.chain == "solana":
            ins_total = sum(
                t.instruction_count or 0 for t in txs if t.instruction_count is not None
            )
            unk = sum(
                t.unknown_instruction_count or 0
                for t in txs
                if t.unknown_instruction_count is not None
            )
            put("unknown_instruction_share", unk / ins_total if ins_total else None)
        if times:
            put("days_since_first_seen", (as_of - times[0]).total_seconds() / 86400)
            put("hour_entropy", _entropy(list(Counter(t.hour for t in times).values())))
            put("weekend_share", sum(t.weekday() >= 5 for t in times) / len(times))
            put("burst_max_10min", _burst(times, timedelta(minutes=10)))
            recent = sum(1 for t in times if t > as_of - timedelta(days=7))
            prior = sum(
                1 for t in times if as_of - timedelta(days=14) < t <= as_of - timedelta(days=7)
            )
            put("recent_activity_ratio", recent / prior if prior else None)
        if len(times) >= 2:
            gaps = [(b - a).total_seconds() for a, b in pairwise(times)]
            put("median_inter_tx_s", statistics.median(gaps))
            mean = statistics.fmean(gaps)
            put("inter_tx_cv", statistics.pstdev(gaps) / mean if mean > 0 else None)
    availability = {k: "unavailable" if v[k] is None else "observed" for k in FEATURE_NAMES}
    first = times[0] if times else None
    return FeatureVector(
        chain=ds.chain,
        address=ds.address,
        as_of=as_of,
        values=v,
        availability=availability,
        window={
            "start": first.isoformat() if first else None,
            "end": times[-1].isoformat() if times else None,
            "as_of": as_of.isoformat(),
        },
        coverage={
            "transactions_observed": len(txs),
            "transfers_observed": len(trs),
            "counterparties_observed": int(v["distinct_counterparties"] or 0)
            if v["distinct_counterparties"] is not None
            else None,
            "untimed_records": untimed,
            "records_after_as_of_excluded": future,
            "record_limit_reached": ds.truncated,
            "ingestion_runs": ds.runs,
            "observation_period_days": round(v["span_days"], 3)
            if v["span_days"] is not None
            else None,
            "data_completeness": "none"
            if not have_window
            else "partial"
            if ds.truncated or untimed or ds.chain == "ethereum"
            else "retrieved_window",
            "features_available": sum(a == "observed" for a in availability.values()),
            "features_total": len(FEATURE_NAMES),
        },
        data_kind=data_kind,
        record_ids=[t.tx_id for t in txs][:200],
    )


def _targets(ds: WalletDataset, txs: list[TxRow]) -> Counter[str]:
    out: Counter[str] = Counter()
    for t in txs:
        if ds.chain == "solana":
            out.update(set(t.program_ids))
        elif t.from_address == ds.address and t.to_address:
            out[t.to_address] += 1
    return out


def _burst(times: list[datetime], span: timedelta) -> int:
    best = j = 0
    for i in range(len(times)):
        while times[i] - times[j] > span:
            j += 1
        best = max(best, i - j + 1)
    return best


def transfer_parties(trs: list[TransferRow]) -> set[str]:
    return {r.party for r in trs if r.party}
