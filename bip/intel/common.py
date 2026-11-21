"""Shared helpers: evidence formatting, verification summaries and confidence.

Confidence is deliberately simple and explainable:

    confidence = sample_factor * verification_factor

* ``sample_factor = min(1, n / full_sample)``: small samples cannot be confident.
* ``verification_factor = 0.5 + 0.5 * corroborated_share``, halved if any cited evidence is
  conflicted. Uncorroborated (single-provider) evidence therefore caps confidence at 0.5.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from bip.intel.dataset import VERIFICATION_LABEL, TransferRow, TxRow

J = dict[str, Any]

FULL_SAMPLE = 30
"""Sample size at which the sample factor reaches 1 (also the minimum for high severity)."""

MAX_EVIDENCE = 20
"""Supporting records listed per result (the total count is always reported)."""


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def label(status: str) -> str:
    return VERIFICATION_LABEL.get(status, status)


def verification_summary(statuses: Iterable[str]) -> J:
    counts = Counter(label(s) for s in statuses)
    total = sum(counts.values())
    corroborated = counts.get("corroborated", 0)
    if total == 0:
        overall = "no evidence"
    elif counts.get("conflicted", 0) or counts.get("invalidated", 0):
        overall = "conflicted"
    elif corroborated == total:
        overall = "corroborated"
    elif corroborated:
        overall = "partially corroborated"
    else:
        overall = "unverified"
    return {
        "overall": overall,
        "total": total,
        "counts": dict(sorted(counts.items())),
        "corroborated_share": round(corroborated / total, 4) if total else None,
    }


def confidence(n: int, statuses: Sequence[str], *, full_sample: int = FULL_SAMPLE) -> J:
    sample = min(1.0, n / full_sample) if full_sample > 0 else 1.0
    labels = [label(s) for s in statuses]
    share = labels.count("corroborated") / len(labels) if labels else 0.0
    vf = 0.5 + 0.5 * share
    if "conflicted" in labels or "invalidated" in labels:
        vf *= 0.5
    value = round(sample * vf, 2)
    return {
        "value": value,
        "label": "high" if value >= 0.67 else "medium" if value >= 0.34 else "low",
        "sample_size": n,
        "sample_factor": round(sample, 2),
        "verification_factor": round(vf, 2),
        "method": f"min(1, n/{full_sample}) x (0.5 + 0.5 x corroborated share), "
        "halved if any evidence is conflicted",
    }


def tx_evidence(t: TxRow) -> J:
    return {
        "tx_id": t.tx_id,
        "block_number": t.block_number,
        "slot": t.slot,
        "block_time": iso(t.block_time),
        "provider": t.provider,
        "verification": label(t.verification),
    }


def transfer_evidence(r: TransferRow) -> J:
    return {
        "tx_id": r.tx_id,
        "event_key": r.event_key,
        "position": r.position,
        "block_time": iso(r.block_time),
        "asset": r.asset,
        "amount_raw": str(r.amount_raw),
        "decimals": r.decimals,
        "from": r.from_address,
        "to": r.to_address,
        "provider": r.provider,
        "verification": label(r.verification),
    }


def median_mad(values: Sequence[float]) -> tuple[float, float]:
    """Median and median absolute deviation (unscaled)."""
    med = statistics.median(values)
    return med, statistics.median(abs(v - med) for v in values)


MAD_SCALE = 1.4826
"""Makes MAD consistent with the standard deviation for normally distributed data."""
