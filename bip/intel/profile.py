"""Wallet profile: descriptive metrics over the retrieved dataset only.

Every metric is scope-labelled ("N transactions observed in retrieved dataset"). Nothing here
is a lifetime figure: BIP only knows what its bounded ingestion runs retrieved.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from bip.chains.solana.parse import SYSTEM_PROGRAM, TOKEN_2022_PROGRAM, TOKEN_PROGRAM
from bip.intel.common import J, iso, label, transfer_evidence, tx_evidence, verification_summary
from bip.intel.dataset import TransferRow, WalletDataset, scale

SCOPE = "retrieved dataset"

# Protocol-defined program ids (constants of the Solana runtime / SPL), not heuristics.
KNOWN_PROGRAMS = {
    SYSTEM_PROGRAM: "System Program",
    TOKEN_PROGRAM: "SPL Token Program",
    TOKEN_2022_PROGRAM: "SPL Token-2022 Program",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL": "SPL Associated Token Account Program",
    "ComputeBudget111111111111111111111111111111": "Compute Budget Program",
}


def metric(mid: str, value: Any, text: str, unit: str | None = None) -> J:
    return {"id": mid, "value": value, "unit": unit, "label": f"{text} in {SCOPE}"}


def observed_range(ds: WalletDataset) -> tuple[datetime | None, datetime | None]:
    times = [t.block_time for t in ds.transactions if t.block_time is not None]
    times += [r.block_time for r in ds.transfers if r.block_time is not None]
    return (min(times), max(times)) if times else (None, None)


def scope(ds: WalletDataset) -> J:
    first, last = observed_range(ds)
    return {
        "kind": "retrieved_dataset",
        "statement": (
            f"Computed only from records BIP retrieved for this wallet in {ds.runs} ingestion "
            "run(s). This is not the wallet's lifetime history."
        ),
        "first_observed": iso(first),
        "last_observed": iso(last),
        "transactions": len(ds.transactions),
        "transfers": len(ds.transfers),
        "balance_changes": len(ds.balance_changes),
        "truncated": ds.truncated,
        "ingestion_runs": ds.runs,
        "last_ingestion_at": iso(ds.last_run_at),
    }


def asset_volumes(transfers: list[TransferRow]) -> list[J]:
    agg: dict[str, J] = {}
    for r in transfers:
        if r.direction not in {"in", "out", "self"}:
            continue
        a = agg.setdefault(
            r.asset,
            {
                "asset": r.asset,
                "standard": r.standard,
                "decimals": r.decimals,
                "in_raw": 0,
                "out_raw": 0,
                "in_count": 0,
                "out_count": 0,
                "self_count": 0,
            },
        )
        if a["decimals"] is None and r.decimals is not None:
            a["decimals"] = r.decimals
        if r.direction == "self":
            a["self_count"] += 1
        else:
            a[f"{r.direction}_raw"] += r.amount_raw
            a[f"{r.direction}_count"] += 1
    out: list[J] = []
    for a in sorted(agg.values(), key=lambda x: -(x["in_count"] + x["out_count"])):
        net = a["in_raw"] - a["out_raw"]
        dec = a["decimals"]
        nft = a["standard"] == "erc721"
        out.append(
            {
                **a,
                "in_raw": str(a["in_raw"]),
                "out_raw": str(a["out_raw"]),
                "net_raw": str(net),
                "in": None if nft else scale(a["in_raw"], dec),
                "out": None if nft else scale(a["out_raw"], dec),
                "net": None if nft else scale(net, dec),
                "unit_note": "token ids (count only)"
                if nft
                else "raw units; scaled amount omitted because decimals are unknown"
                if dec is None
                else None,
            }
        )
    return out


def counterparties(ds: WalletDataset, limit: int = 50) -> J:
    agg: dict[str, J] = {}
    for r in ds.transfers:
        party = r.party
        if party is None or r.direction not in {"in", "out"}:
            continue
        c = agg.setdefault(
            party,
            {
                "address": party,
                "transfers": 0,
                "in": 0,
                "out": 0,
                "assets": set(),
                "tx_ids": set(),
                "first_seen": None,
                "last_seen": None,
                "statuses": [],
                "via_token_account": r.counterparty_owner is not None,
            },
        )
        c["transfers"] += 1
        c[r.direction] += 1
        c["assets"].add(r.asset)
        c["tx_ids"].add(r.tx_id)
        c["statuses"].append(r.verification)
        if r.block_time is not None:
            c["first_seen"] = min(c["first_seen"] or r.block_time, r.block_time)
            c["last_seen"] = max(c["last_seen"] or r.block_time, r.block_time)
    ranked = sorted(agg.values(), key=lambda c: (-c["transfers"], c["address"]))
    items = [
        {
            "address": c["address"],
            "transfers": c["transfers"],
            "in": c["in"],
            "out": c["out"],
            "assets": sorted(c["assets"]),
            "transactions": len(c["tx_ids"]),
            "first_seen": iso(c["first_seen"]),
            "last_seen": iso(c["last_seen"]),
            "resolved_from_token_account": c["via_token_account"],
            "verification": verification_summary(c["statuses"]),
            "relationship": "observed transfer counterparty (no ownership or identity implied)",
        }
        for c in ranked[:limit]
    ]
    return {"total": len(agg), "items": items, "truncated": len(agg) > limit}


def interactions(ds: WalletDataset, limit: int = 50) -> J:
    """Ethereum: transaction targets sent by the wallet. Solana: programs invoked."""
    tokens = {r.asset for r in ds.transfers if r.asset != "native"}
    created = {t.created_contract for t in ds.transactions if t.created_contract}
    agg: dict[str, J] = {}
    for t in ds.transactions:
        if ds.chain == "solana":
            targets = [(p, "program") for p in t.program_ids]
        elif t.from_address == ds.address and t.to_address is not None:
            kind = (
                "token contract"
                if t.to_address in tokens
                else (
                    "contract deployed by this wallet"
                    if t.to_address in created
                    else "address (contract status not checked)"
                )
            )
            targets = [(t.to_address, kind)]
        else:
            targets = []
        for target, kind in targets:
            a = agg.setdefault(target, {"target": target, "kind": kind, "count": 0, "tx": []})
            a["count"] += 1
            if len(a["tx"]) < 5:
                a["tx"].append(t.tx_id)
    ranked = sorted(agg.values(), key=lambda a: (-a["count"], a["target"]))
    for a in ranked:
        a["name"] = KNOWN_PROGRAMS.get(a["target"]) if ds.chain == "solana" else None
        a["name_basis"] = "protocol-defined program id" if a["name"] else None
    return {
        "total": len(agg),
        "items": ranked[:limit],
        "truncated": len(agg) > limit,
        "note": "Unknown programs and contracts are left unnamed; names are only given for "
        "protocol-defined program ids. Token metadata is never treated as proof of "
        "legitimacy.",
    }


def deployments(ds: WalletDataset) -> list[J]:
    return [
        {**tx_evidence(t), "created_contract": t.created_contract}
        for t in ds.transactions
        if t.created_contract and t.from_address == ds.address
    ]


def activity(ds: WalletDataset) -> list[J]:
    days: dict[str, J] = defaultdict(
        lambda: {"transactions": 0, "transfers_in": 0, "transfers_out": 0}
    )
    for t in ds.transactions:
        if t.block_time is not None:
            days[t.block_time.date().isoformat()]["transactions"] += 1
    for r in ds.transfers:
        if r.block_time is not None and r.direction in {"in", "out"}:
            days[r.block_time.date().isoformat()][f"transfers_{r.direction}"] += 1
    return [{"date": d, **v} for d, v in sorted(days.items())]


def coverage(ds: WalletDataset) -> J:
    partial = sum(1 for t in ds.transactions if t.parse_status != "complete")
    no_time = sum(1 for t in ds.transactions if t.block_time is None)
    unknown_ix = sum(t.unknown_instruction_count or 0 for t in ds.transactions)
    return {
        "ingestion_runs": ds.runs,
        "last_ingestion_at": iso(ds.last_run_at),
        "cursor": {
            k: iso(v) if isinstance(v, datetime) else v
            for k, v in ds.cursor.items()
            if k not in {"chain", "address"}
        }
        or None,
        "record_limit_reached": ds.truncated,
        "partially_parsed_transactions": partial,
        "transactions_without_block_time": no_time,
        "unknown_instructions": unknown_ix,
        "discovery_method": (
            "Transfer logs (ERC-20/721) within scanned block windows; plain ETH transfers "
            "without logs and internal transfers are not discovered"
            if ds.chain == "ethereum"
            else "getSignaturesForAddress, most recent signatures per run"
        ),
    }


def limitations(ds: WalletDataset) -> list[str]:
    out = [
        "Metrics describe only the retrieved dataset, not lifetime activity.",
        "No USD values: BIP has no verified price source.",
        "Counterparties are observed transfer parties; no ownership, identity or intent is "
        "implied.",
    ]
    if ds.chain == "ethereum":
        out.append(
            "Ethereum activity is discovered through Transfer logs only; plain ETH "
            "transfers and internal transfers can be missing."
        )
    if ds.truncated:
        out.append("The record limit was reached; older retrieved records were not analysed.")
    if not any(label(t.verification) == "corroborated" for t in ds.transactions):
        out.append("No evidence in this dataset has been corroborated by an independent provider.")
    return out


def build_profile(ds: WalletDataset) -> J:
    n_tx = len(ds.transactions)
    statuses = Counter(t.status or "unknown" for t in ds.transactions)
    cps = counterparties(ds)
    vols = asset_volumes(ds.transfers)
    deps = deployments(ds)
    act = activity(ds)
    inter = interactions(ds)
    metrics = [
        metric("transactions", n_tx, f"{n_tx} transactions observed"),
        metric(
            "transfers_in",
            sum(1 for r in ds.transfers if r.direction == "in"),
            "incoming transfers observed",
        ),
        metric(
            "transfers_out",
            sum(1 for r in ds.transfers if r.direction == "out"),
            "outgoing transfers observed",
        ),
        metric("counterparties", cps["total"], f"{cps['total']} distinct counterparties observed"),
        metric("assets", len(vols), f"{len(vols)} distinct assets observed"),
        metric("active_days", len(act), f"{len(act)} active days observed"),
        metric(
            "interaction_targets",
            inter["total"],
            f"{inter['total']} distinct {'programs' if ds.chain == 'solana' else 'call targets'}"
            " observed",
        ),
        metric("deployments", len(deps), f"{len(deps)} contract deployments observed"),
        metric("failed_transactions", statuses.get("failed", 0), "failed transactions observed"),
    ]
    return {
        "chain": ds.chain,
        "address": ds.address,
        "scope": scope(ds),
        "metrics": metrics,
        "transaction_status": dict(statuses),
        "volumes": vols,
        "counterparties": cps,
        "interactions": inter,
        "deployments": deps,
        "activity": act,
        "coverage": coverage(ds),
        "verification": verification_summary(t.verification for t in ds.transactions),
        "limitations": limitations(ds),
        "evidence_sample": [tx_evidence(t) for t in ds.transactions[:10]],
        "transfer_sample": [transfer_evidence(r) for r in ds.transfers[:10]],
    }
