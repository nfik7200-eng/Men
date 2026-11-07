"""Evidence-backed relationship graph, computed on demand from stored observations.

Nodes: ``address`` (type not established), ``contract`` (deployed in a retrieved transaction),
``token`` (ERC-20/721 contract or SPL mint seen in transfers), ``program`` (Solana program).
No ``protocol`` nodes: BIP cannot yet identify protocols reliably.

Edges, all aggregated per (source, target, kind, asset) with evidence:
``native_transfer``, ``token_transfer``, ``token_activity`` (wallet moved this token),
``called`` (EVM transaction target), ``invoked`` (Solana program), ``deployed``.

**An edge is an observed interaction, never a claim of common ownership.**

Traversal is bounded by depth, node and edge limits and a deadline; every bound that cut the
result short is reported in ``truncation``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import or_, select

from bip.config.settings import IntelSettings
from bip.contracts.provenance import Provenance
from bip.db import schema
from bip.db.repository import Repository, canonical_address
from bip.intel.common import J, iso, label, verification_summary
from bip.intel.dataset import account_owners, effective_verification

GRAPH_VERSION = "1.0.0"
EVIDENCE_PER_EDGE = 10


class _Builder:
    def __init__(self, chain: str, max_nodes: int, max_edges: int) -> None:
        self.chain = chain
        self.nodes: dict[str, J] = {}
        self.edges: dict[tuple[str, str, str, str], J] = {}
        self.max_nodes = max_nodes
        self.max_edges = max_edges
        self.node_limited = False
        self.edge_limited = False
        self.row_limited = False
        # stored rows already turned into edges: a row re-read at a deeper level never counts twice
        self.seen_transfers: set[int] = set()
        self.seen_activity: set[tuple[int, str]] = set()
        self.seen_txs: set[int] = set()

    def node(self, nid: str, kind: str, depth: int) -> bool:
        n = self.nodes.get(nid)
        if n is not None:
            if n["kind"] == "address" and kind != "address":
                n["kind"] = kind  # a more specific, evidence-backed type
            n["depth"] = min(n["depth"], depth)
            return True
        if len(self.nodes) >= self.max_nodes:
            self.node_limited = True
            return False
        self.nodes[nid] = {"id": nid, "kind": kind, "depth": depth, "degree": 0}
        return True

    def edge(self, src: str, dst: str, kind: str, asset: str, ev: J, amount: int | None) -> None:
        key = (src, dst, kind, asset)
        e = self.edges.get(key)
        if e is None:
            if len(self.edges) >= self.max_edges:
                self.edge_limited = True
                return
            e = self.edges[key] = {
                "id": f"e{len(self.edges)}",
                "source": src,
                "target": dst,
                "kind": kind,
                "asset": None if asset == "-" else asset,
                "count": 0,
                "amount_raw": 0 if amount is not None else None,
                "first_seen": None,
                "last_seen": None,
                "evidence": [],
                "_tx": set(),
                "_status": [],
            }
            self.nodes[src]["degree"] += 1
            self.nodes[dst]["degree"] += 1
        if ev["tx_id"] in e["_tx"] and kind in {"called", "invoked", "deployed"}:
            return
        e["_tx"].add(ev["tx_id"])
        e["count"] += 1
        if amount is not None and e["amount_raw"] is not None:
            e["amount_raw"] += amount
        e["_status"].append(ev["verification_raw"])
        t = ev["block_time"]
        if t is not None:
            e["first_seen"] = min(e["first_seen"] or t, t)
            e["last_seen"] = max(e["last_seen"] or t, t)
        if len(e["evidence"]) < EVIDENCE_PER_EDGE:
            e["evidence"].append({k: v for k, v in ev.items() if k != "verification_raw"})

    def finish(self) -> tuple[list[J], list[J]]:
        edges: list[J] = []
        for e in self.edges.values():
            statuses = e.pop("_status")
            e.pop("_tx")
            e["verification"] = verification_summary(statuses)
            if e["amount_raw"] is not None:
                e["amount_raw"] = str(e["amount_raw"])
            e["first_seen"], e["last_seen"] = iso(e["first_seen"]), iso(e["last_seen"])
            edges.append(e)
        return list(self.nodes.values()), edges


def build_graph(
    repo: Repository,
    chain: str,
    address: str,
    cfg: IntelSettings,
    *,
    depth: int = 1,
    max_nodes: int | None = None,
    max_edges: int | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> J:
    ref = canonical_address(chain, address)
    depth = max(1, min(depth, cfg.graph_max_depth))
    b = _Builder(
        ref.chain,
        min(max_nodes or cfg.graph_max_nodes, cfg.graph_max_nodes),
        min(max_edges or cfg.graph_max_edges, cfg.graph_max_edges),
    )
    deadline = clock() + cfg.graph_timeout_s
    timed_out = False
    depth_limited = False
    b.node(ref.address, "address", 0)
    b.nodes[ref.address]["center"] = True
    visited: set[str] = set()
    frontier = [ref.address]
    per_query = max(b.max_edges * 2, 50)
    for level in range(depth + 1):
        if not frontier:
            break
        if level == depth:
            depth_limited = True  # frontier nodes exist but are not expanded
            break
        if clock() > deadline:
            timed_out = True
            break
        visited.update(frontier)
        nxt: set[str] = set()
        for addr in _expand(repo, b, ref.chain, frontier, level, per_query):
            if addr not in visited:
                nxt.add(addr)
        if clock() > deadline:
            timed_out = True
            break
        frontier = sorted(nxt)
    nodes, edges = b.finish()
    return {
        "chain": ref.chain,
        "center": ref.address,
        "graph_version": GRAPH_VERSION,
        "depth": depth,
        "nodes": nodes,
        "edges": edges,
        "limits": {
            "max_depth": depth,
            "max_nodes": b.max_nodes,
            "max_edges": b.max_edges,
            "timeout_s": cfg.graph_timeout_s,
        },
        "truncation": {
            "truncated": b.node_limited or b.edge_limited or b.row_limited or timed_out,
            "row_limit_reached": b.row_limited,
            "node_limit_reached": b.node_limited,
            "edge_limit_reached": b.edge_limited,
            "timed_out": timed_out,
            "unexpanded_frontier": len(frontier) if depth_limited else 0,
        },
        "verification": verification_summary(
            x["verification"] for e in edges for x in e["evidence"]
        ),
        "statement": "Edges are observed on-chain interactions from BIP's stored data. They do "
        "not establish common ownership, control or identity.",
    }


def _ev(
    tx_id: str,
    position: int | None,
    block_time: Any,
    asset: str | None,
    amount: int | None,
    prov_json: str,
    status: str,
) -> J:
    prov = Provenance.model_validate_json(prov_json)
    return {
        "tx_id": tx_id,
        "position": position,
        "block_time": block_time,
        "asset": asset,
        "amount_raw": str(amount) if amount is not None else None,
        "provider": prov.source_provider,
        "verification": label(status),
        "verification_raw": status,
    }


def _expand(
    repo: Repository, b: _Builder, chain: str, frontier: list[str], level: int, limit: int
) -> set[str]:
    tr, t, bc = schema.transfers, schema.transactions, schema.balance_changes
    found: set[str] = set()
    with repo.transaction() as conn:
        owned = {
            r[0]: r[1]
            for r in conn.execute(
                select(bc.c.account, bc.c.owner)
                .where(bc.c.chain == chain, bc.c.owner.in_(frontier), bc.c.account != bc.c.owner)
                .distinct()
            ).all()
        }
        accounts = list(set(frontier) | set(owned))
        trs = (
            conn.execute(
                select(tr)
                .where(
                    tr.c.chain == chain,
                    or_(tr.c.from_address.in_(accounts), tr.c.to_address.in_(accounts)),
                )
                .order_by(tr.c.id.desc())
                .limit(limit + 1)
            )
            .mappings()
            .all()
        )
        txs = (
            conn.execute(
                select(t)
                .where(t.c.chain == chain, t.c.from_address.in_(frontier))
                .order_by(t.c.id.desc())
                .limit(limit + 1)
            )
            .mappings()
            .all()
        )
    if len(trs) > limit or len(txs) > limit:
        b.row_limited = True  # some stored rows for this frontier were not read
        trs, txs = trs[:limit], txs[:limit]
    verif = effective_verification(
        repo, chain, sorted({r["tx_id"] for r in trs} | {r["tx_id"] for r in txs})
    )
    others = {a for r in trs for a in (r["from_address"], r["to_address"]) if a} - set(owned)
    owners = {**account_owners(repo, chain, others), **owned}

    def who(a: str | None) -> str | None:
        return owners.get(a, a) if a else None

    def status(tx_id: str, prov_json: str) -> str:
        return (
            verif.get(tx_id) or Provenance.model_validate_json(prov_json).verification_status.value
        )

    for r in trs:
        src, dst = who(r["from_address"]), who(r["to_address"])
        if src is None or dst is None or src == dst:
            continue
        token = r["token"]
        kind = "token_transfer" if token else "native_transfer"
        amount = None if r["standard"] == "erc721" else int(r["amount_raw"])
        pos = r["block_number"] if r["block_number"] is not None else r["slot"]
        ev = _ev(
            r["tx_id"],
            pos,
            r["block_time"],
            token or "native",
            amount,
            r["provenance_json"],
            status(r["tx_id"], r["provenance_json"]),
        )
        if not (
            b.node(src, "address", level + (src not in frontier))
            and b.node(dst, "address", level + (dst not in frontier))
        ):
            continue
        if r["id"] not in b.seen_transfers:
            b.seen_transfers.add(r["id"])
            b.edge(src, dst, kind, token or "native", ev, amount)
        found.update({src, dst})
        if token and b.node(token, "token", level + 1):
            for party in (src, dst):
                if party in frontier and (r["id"], party) not in b.seen_activity:
                    b.seen_activity.add((r["id"], party))
                    b.edge(party, token, "token_activity", token, ev, amount)
    for r in txs:
        if r["id"] in b.seen_txs:
            continue
        b.seen_txs.add(r["id"])
        frm = r["from_address"]
        pos = r["block_number"] if r["block_number"] is not None else r["slot"]
        ev = _ev(
            r["tx_id"],
            pos,
            r["block_time"],
            None,
            None,
            r["provenance_json"],
            status(r["tx_id"], r["provenance_json"]),
        )
        if r["created_contract"] and b.node(r["created_contract"], "contract", level + 1):
            b.edge(frm, r["created_contract"], "deployed", "-", ev, None)
        if chain == "solana":
            for p in json.loads(r["program_ids_json"] or "[]"):
                if b.node(p, "program", level + 1):
                    b.edge(frm, p, "invoked", "-", ev, None)
        elif r["to_address"] and b.node(r["to_address"], "address", level + 1):
            b.edge(frm, r["to_address"], "called", "-", ev, None)
            found.add(r["to_address"])
    return {a for a in found if b.nodes.get(a, {}).get("kind") in {"address", "contract"}}
