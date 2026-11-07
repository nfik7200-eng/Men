"""Capital flows for one wallet.

**Canonical source: the ``transfers`` table** (decoded native and token transfers). Solana
balance changes are *not* added on top: they describe the same movements and would double
count. No USD values: amounts stay in raw units, scaled only where decimals are known.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from bip.intel.common import J, iso, transfer_evidence, verification_summary
from bip.intel.dataset import TransferRow, WalletDataset, scale

CANONICAL_SOURCE = "transfers"


def _scaled(raw: int, decimals: int | None, standard: str) -> str | None:
    return None if standard == "erc721" else scale(raw, decimals)


def build_flows(
    ds: WalletDataset,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    max_window_days: int = 365,
    top: int = 10,
) -> J:
    rows = [r for r in ds.transfers if r.direction in {"in", "out", "self"}]
    # counted before windowing: a time filter excludes untimed rows, and that must be visible
    untimed = sum(1 for r in rows if r.block_time is None)
    windowed = since is not None or until is not None
    if since is not None and until is not None and until - since > timedelta(days=max_window_days):
        since = until - timedelta(days=max_window_days)
    if since is not None:
        rows = [r for r in rows if r.block_time is not None and r.block_time >= since]
    if until is not None:
        rows = [r for r in rows if r.block_time is not None and r.block_time <= until]

    assets: dict[str, J] = {}
    for r in rows:
        a = assets.setdefault(
            r.asset,
            {
                "asset": r.asset,
                "standard": r.standard,
                "decimals": r.decimals,
                "in": 0,
                "out": 0,
                "in_count": 0,
                "out_count": 0,
                "self_count": 0,
            },
        )
        if r.direction == "self":
            a["self_count"] += 1
            continue
        a[r.direction] += r.amount_raw
        a[f"{r.direction}_count"] += 1
    per_asset = [
        {
            "asset": a["asset"],
            "standard": a["standard"],
            "decimals": a["decimals"],
            "in_raw": str(a["in"]),
            "out_raw": str(a["out"]),
            "net_raw": str(a["in"] - a["out"]),
            "in": _scaled(a["in"], a["decimals"], a["standard"]),
            "out": _scaled(a["out"], a["decimals"], a["standard"]),
            "net": _scaled(a["in"] - a["out"], a["decimals"], a["standard"]),
            "in_count": a["in_count"],
            "out_count": a["out_count"],
            "self_count": a["self_count"],
        }
        for a in sorted(assets.values(), key=lambda a: -(a["in_count"] + a["out_count"]))
    ]

    cps: dict[tuple[str, str], J] = {}
    for r in rows:
        if r.direction == "self" or r.party is None:
            continue
        c = cps.setdefault(
            (r.party, r.asset),
            {
                "counterparty": r.party,
                "asset": r.asset,
                "standard": r.standard,
                "decimals": r.decimals,
                "in": 0,
                "out": 0,
                "count": 0,
            },
        )
        c[r.direction] += r.amount_raw
        c["count"] += 1
    counterparties = [
        {
            **{k: v for k, v in c.items() if k not in {"in", "out"}},
            "in_raw": str(c["in"]),
            "out_raw": str(c["out"]),
            "net_raw": str(c["in"] - c["out"]),
            "in": _scaled(c["in"], c["decimals"], c["standard"]),
            "out": _scaled(c["out"], c["decimals"], c["standard"]),
        }
        for c in sorted(cps.values(), key=lambda c: (-c["count"], c["counterparty"]))[:50]
    ]

    buckets: dict[tuple[str, str], J] = {}
    for r in rows:
        if r.block_time is None or r.direction == "self":
            continue
        day = r.block_time.date().isoformat()
        b = buckets.setdefault(
            (day, r.asset),
            {
                "date": day,
                "asset": r.asset,
                "in": 0,
                "out": 0,
                "count": 0,
                "decimals": r.decimals,
                "standard": r.standard,
            },
        )
        b[r.direction] += r.amount_raw
        b["count"] += 1
    time_buckets = [
        {
            "date": b["date"],
            "asset": b["asset"],
            "count": b["count"],
            "in_raw": str(b["in"]),
            "out_raw": str(b["out"]),
            "in": _scaled(b["in"], b["decimals"], b["standard"]),
            "out": _scaled(b["out"], b["decimals"], b["standard"]),
        }
        for _, b in sorted(buckets.items())
    ]

    by_asset: dict[str, list[TransferRow]] = defaultdict(list)
    for r in rows:
        if r.direction != "self" and r.standard != "erc721":
            by_asset[r.asset].append(r)
    largest = {
        asset: [
            {
                **transfer_evidence(r),
                "direction": r.direction,
                "counterparty": r.party,
                "amount": scale(r.amount_raw, r.decimals),
            }
            for r in sorted(rs, key=lambda r: (-r.amount_raw, r.tx_id))[:top]
        ]
        for asset, rs in by_asset.items()
    }

    paths = Counter(
        (r.from_address, r.to_address, r.asset) for r in rows if r.from_address and r.to_address
    )
    repeated = [
        {"from": f, "to": t, "asset": a, "count": n}
        for (f, t, a), n in paths.most_common()
        if n >= 2
    ][:top]

    times = [r.block_time for r in rows if r.block_time is not None]
    return {
        "chain": ds.chain,
        "address": ds.address,
        "canonical_source": CANONICAL_SOURCE,
        "window": {
            "since": iso(since),
            "until": iso(until),
            "first_transfer": iso(min(times)) if times else None,
            "last_transfer": iso(max(times)) if times else None,
            "max_window_days": max_window_days,
        },
        "transfers_considered": len(rows),
        "transfers_without_time": untimed,
        "untimed_excluded_by_window": untimed if windowed else 0,
        "assets": per_asset,
        "counterparties": counterparties,
        "time_buckets": time_buckets,
        "largest_transfers": largest,
        "repeated_paths": repeated,
        "verification": verification_summary(r.verification for r in rows),
        "usd_values": None,
        "limitations": [
            "Computed from transfers retrieved by BIP only (not lifetime flows).",
            "No USD values: no verified price source is configured.",
            "Solana balance changes are not added to transfers (they would double count).",
            "ERC-721 transfers are counted, not summed: token ids are not amounts.",
            *(
                [
                    "Ethereum: plain ETH transfers without logs and internal transfers can be "
                    "missing."
                ]
                if ds.chain == "ethereum"
                else []
            ),
            *(
                ["The record limit was reached; older retrieved transfers were not included."]
                if ds.truncated
                else []
            ),
        ],
    }
