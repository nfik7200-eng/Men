"""Temporal graph explorer and evidence trail.

A pure post-processing layer over :func:`bip.intel.graph.build_graph` output: it never reads the
database or the network. It adds

* a **time window** (``since`` / ``until``) applied to each observation's block time,
* an **evidence-status filter** (``verified`` / ``single_source`` / ``hypothesis``),
* a **timeline** (observation histogram) to drive a time slider,
* a **node trail**: exact transaction hashes, block range and the provider response references
  behind a node.

Evidence classes (derived from stored verification status, never guessed):

* ``verified``       cross-verified: matching observations from independent providers.
* ``single_source``  observed through one provider (stored ``single_source`` or ``unverified``).
* ``hypothesis``     conflicted or invalidated evidence; it must not be presented as established.

Filtering is over observations, so counts, amounts, seen-times and verification summaries are
recomputed from the observations that remain. When an edge carries fewer retained observations
than its total count, recomputation is impossible and the edge is flagged ``evidence_complete:
false`` with its original aggregate left in ``unfiltered``; nothing is silently extrapolated.
Observations without a block time are excluded by an active time window and counted.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable
from datetime import UTC, datetime
from typing import Any

from bip.intel.common import J, iso, verification_summary

EXPLORER_VERSION = "1.0.0"
EVIDENCE_STATUSES = ("verified", "single_source", "hypothesis")
EXPLORER_EVIDENCE_CAP = 500
TIMELINE_BUCKETS = 24

_CLASS_BY_STATUS = {
    "cross_verified": "verified",
    "single_source": "single_source",
    "unverified": "single_source",
    "conflict": "hypothesis",
    "invalidated": "hypothesis",
}


def evidence_class(raw_status: str) -> str:
    """Map a stored verification status to an evidence class. Unknown values are hypotheses."""
    return _CLASS_BY_STATUS.get(raw_status, "hypothesis")


def parse_statuses(values: Iterable[str] | None) -> frozenset[str]:
    """Validate a status filter; empty or missing means all classes."""
    if not values:
        return frozenset(EVIDENCE_STATUSES)
    chosen = frozenset(values)
    unknown = chosen - set(EVIDENCE_STATUSES)
    if unknown:
        raise ValueError(f"unknown evidence status {sorted(unknown)}; use {EVIDENCE_STATUSES}")
    return chosen


def _t(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)  # stored block times are UTC
    return value


def _keep(ev: J, since: datetime | None, until: datetime | None, statuses: Collection[str]) -> str:
    """Return ``"keep"`` or the reason the observation is dropped."""
    if evidence_class(ev.get("verification_status", "")) not in statuses:
        return "status"
    if since is not None or until is not None:
        t = _t(ev.get("block_time"))
        if t is None:
            return "undated"
        if (since is not None and t < since) or (until is not None and t > until):
            return "time"
    return "keep"


def timeline(graph: J, buckets: int = TIMELINE_BUCKETS) -> J:
    """Histogram of dated observations over the unfiltered graph, for the time slider."""
    times: list[datetime] = []
    undated = 0
    for e in graph["edges"]:
        for ev in e["evidence"]:
            t = _t(ev.get("block_time"))
            if t is None:
                undated += 1
            else:
                times.append(t)
    if not times:
        return {"start": None, "end": None, "buckets": [], "undated": undated}
    lo, hi = min(times), max(times)
    span = (hi - lo).total_seconds()
    n = max(1, min(buckets, len(times))) if span > 0 else 1
    counts = [0] * n
    for t in times:
        i = 0 if span == 0 else min(n - 1, int((t - lo).total_seconds() / span * n))
        counts[i] += 1
    step = span / n
    out = [
        {
            "start": iso(datetime.fromtimestamp(lo.timestamp() + i * step, UTC)),
            "end": iso(datetime.fromtimestamp(lo.timestamp() + (i + 1) * step, UTC)),
            "count": c,
        }
        for i, c in enumerate(counts)
    ]
    return {"start": iso(lo), "end": iso(hi), "buckets": out, "undated": undated}


def _refilter_edge(
    e: J,
    since: datetime | None,
    until: datetime | None,
    statuses: Collection[str],
    dropped: Counter[str],
) -> J | None:
    kept: list[J] = []
    for ev in e["evidence"]:
        verdict = _keep(ev, since, until, statuses)
        if verdict == "keep":
            kept.append(ev)
        else:
            dropped[verdict] += 1
    if not kept:
        return None
    complete = e["count"] <= len(e["evidence"])
    out = {k: v for k, v in e.items() if k != "evidence"}
    times = [t for t in (_t(x.get("block_time")) for x in kept) if t is not None]
    out["evidence"] = kept
    out["evidence_complete"] = complete
    out["classes"] = dict(
        sorted(Counter(evidence_class(x["verification_status"]) for x in kept).items())
    )
    if complete:
        amounts: list[str] = [a for a in (x.get("amount_raw") for x in kept) if a is not None]
        out["count"] = len(kept)
        out["amount_raw"] = str(sum(int(a) for a in amounts)) if len(amounts) == len(kept) else None
        out["first_seen"] = iso(min(times)) if times else None
        out["last_seen"] = iso(max(times)) if times else None
        out["verification"] = verification_summary(x["verification_status"] for x in kept)
    else:
        # Aggregates cover observations that were not retained: report, do not recompute.
        out["unfiltered"] = {k: e[k] for k in ("count", "amount_raw", "first_seen", "last_seen")}
    return out


def explore(
    graph: J,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    statuses: Collection[str] = EVIDENCE_STATUSES,
) -> J:
    """Filter a built graph by time window and evidence class; return a new graph dict."""
    if since is not None and until is not None and until < since:
        raise ValueError("until is before since")
    since, until = _t(since), _t(until)
    dropped: Counter[str] = Counter()
    edges: list[J] = []
    for e in graph["edges"]:
        f = _refilter_edge(e, since, until, statuses, dropped)
        if f is not None:
            edges.append(f)
    live = {x for e in edges for x in (e["source"], e["target"])}
    center = graph["center"]
    nodes: list[J] = []
    for n in graph["nodes"]:
        if n["id"] in live or n["id"] == center:
            degree = sum(1 for e in edges if n["id"] in (e["source"], e["target"]))
            nodes.append({**n, "degree": degree})
    return {
        **{k: v for k, v in graph.items() if k not in {"nodes", "edges", "verification"}},
        "explorer_version": EXPLORER_VERSION,
        "nodes": nodes,
        "edges": edges,
        "verification": verification_summary(
            x["verification_status"] for e in edges for x in e["evidence"]
        ),
        "filters": {
            "since": iso(since),
            "until": iso(until),
            "statuses": [s for s in EVIDENCE_STATUSES if s in statuses],
        },
        "filtered_out": {
            "total": sum(dropped.values()),
            "by_status": dropped["status"],
            "outside_time_window": dropped["time"],
            "undated": dropped["undated"],
            "nodes_hidden": len(graph["nodes"]) - len(nodes),
            "edges_hidden": len(graph["edges"]) - len(edges),
        },
        "timeline": timeline(graph),
        "statuses_legend": {
            "verified": "cross-verified by independent providers",
            "single_source": "observed through one provider; not independently corroborated",
            "hypothesis": "conflicted or invalidated evidence; not established",
        },
    }


def inspect_node(graph: J, node_id: str) -> J | None:
    """Evidence trail for one node in an (already filtered) graph, or ``None`` if absent."""
    node = next((n for n in graph["nodes"] if n["id"] == node_id), None)
    if node is None:
        return None
    incident = [e for e in graph["edges"] if node_id in (e["source"], e["target"])]
    seen: dict[str, J] = {}
    for e in incident:
        for ev in e["evidence"]:
            row = seen.setdefault(
                ev["tx_id"],
                {
                    "tx_id": ev["tx_id"],
                    "position": ev.get("position"),
                    "block_time": iso(_t(ev.get("block_time"))),
                    "verification_status": ev["verification_status"],
                    "evidence_class": evidence_class(ev["verification_status"]),
                    "provider_response": ev.get("provider_response"),
                    "edges": [],
                },
            )
            row["edges"].append({"edge_id": e["id"], "kind": e["kind"], "asset": ev.get("asset")})
    trail = sorted(
        seen.values(),
        key=lambda r: (r["block_time"] is None, r["block_time"] or "", r["tx_id"]),
    )
    positions = [r["position"] for r in trail if r["position"] is not None]
    providers = Counter(r["provider_response"]["provider"] for r in trail if r["provider_response"])
    return {
        "node": node,
        "edge_count": len(incident),
        "transactions": trail,
        "transaction_count": len(trail),
        "block_range": ({"min": min(positions), "max": max(positions)} if positions else None),
        "providers": dict(sorted(providers.items())),
        "classes": dict(sorted(Counter(r["evidence_class"] for r in trail).items())),
        "incomplete_edges": [e["id"] for e in incident if not e.get("evidence_complete", True)],
        "statement": "Transactions listed are observed interactions involving this node. "
        "They do not establish ownership, control or identity.",
    }
