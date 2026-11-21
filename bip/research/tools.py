"""Typed, read-only research tools with per-specialist permissions.

Each tool reads BIP's own stored data or services for **the run's subject wallet only**. No
tool runs SQL, shell commands, HTTP requests or raw RPC. The only tool that takes an argument,
``transaction_evidence``, accepts transaction ids that must belong to the subject's stored
records. The permission table is fixed in code; nothing an LLM writes can change it, because
the LLM is never given tool access (the plan is built before any model call).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from bip.db import schema
from bip.db.types import utcnow
from bip.intel import anomaly, behaviour, flows, graph, opportunities, profile

J = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolContext:
    ctx: Any  # bip.api.deps.AppContext (kept untyped to avoid an import cycle)
    chain: str
    address: str


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    specialists: frozenset[str]
    handler: Callable[[ToolContext, J], J]


def _classification(t: ToolContext, args: J) -> J:
    svc = t.ctx.classifier
    result = svc.classify(t.chain, t.address)
    history = svc.history(t.chain, t.address, 10)
    return {
        "classification": result,
        "history": [
            {
                "classification_id": h["classification_id"],
                "timestamp": h["classification_timestamp"],
                "status": h["status"],
                "account_type": h["account_type"]["label"],
                "roles": [(r["label"], r["status"]) for r in h["behavioural_roles"]],
                "model": h["model"],
            }
            for h in history
        ],
    }


def _features(t: ToolContext, args: J) -> J:
    return t.ctx.classifier.latest_features(t.chain, t.address) or {"status": "not_computed"}


def _profile(t: ToolContext, args: J) -> J:
    p = profile.build_profile(t.ctx.intel.dataset(t.chain, t.address))
    p["activity"] = p["activity"][-60:]
    return p


def _behaviour(t: ToolContext, args: J) -> J:
    ds = t.ctx.intel.dataset(t.chain, t.address)
    return {"rules": behaviour.evaluate(ds, t.ctx.settings.intel, utcnow())}


def _anomalies(t: ToolContext, args: J) -> J:
    ds = t.ctx.intel.dataset(t.chain, t.address)
    now = utcnow()
    ctx = anomaly.Context(now=now, first_seen_targets={})
    return {"detectors": anomaly.run_detectors(ds, t.ctx.settings.intel, ctx)}


def _flows(t: ToolContext, args: J) -> J:
    ds = t.ctx.intel.dataset(t.chain, t.address)
    return flows.build_flows(ds, max_window_days=t.ctx.settings.intel.flow_max_window_days)


def _graph(t: ToolContext, args: J) -> J:
    g = graph.build_graph(
        t.ctx.repo, t.chain, t.address, t.ctx.settings.intel, depth=1, max_nodes=60, max_edges=120
    )
    return {
        k: g[k] for k in ("center", "nodes", "edges", "truncation", "verification", "statement")
    }


def _opportunities(t: ToolContext, args: J) -> J:
    items, total = opportunities.list_candidates(
        t.ctx.repo, chain=t.chain, current_only=True, limit=50, offset=0
    )
    ds = t.ctx.intel.dataset(t.chain, t.address)
    touched = {r.asset for r in ds.transfers} | {
        x.to_address for x in ds.transactions if x.to_address
    }
    return {"related": [i for i in items if i["subject"] in touched], "total_leads": total}


def _provider_status(t: ToolContext, args: J) -> J:
    store = t.ctx.probe_store
    out: list[J] = []
    for spec in t.ctx.registry.rpc_providers(t.chain):
        run = store.latest_provider_run(spec.id)
        out.append(
            {
                "provider_id": spec.id,
                "infrastructure": spec.infrastructure,
                "connectivity": run["connectivity"] if run else "not_tested",
                "environment_restricted": bool(run and run.get("environment_restricted")),
                "reliability": store.reliability(spec.id)["state"] if run else None,
            }
        )
    return {
        "providers": out,
        "data_origin": store.data_origin(),
        "live_observation_exists": any(p["connectivity"] == "reachable" for p in out)
        and store.data_origin() != "offline_fixture",
    }


def _tx_evidence(t: ToolContext, args: J) -> J:
    wanted = [str(x) for x in args.get("tx_ids", [])][:20]
    ds = t.ctx.intel.dataset(t.chain, t.address)
    own = {x.tx_id: x for x in ds.transactions}
    allowed = [x for x in wanted if x in own]  # only the subject's own records
    v = schema.evidence_verifications
    with t.ctx.repo.transaction() as conn:
        checks = {
            r[0]: r[1]
            for r in conn.execute(
                select(v.c.tx_id, v.c.outcome)
                .where(v.c.chain == t.chain, v.c.tx_id.in_(allowed))
                .order_by(v.c.id)
            ).all()
        }
    return {
        "transactions": [
            {
                "tx_id": x,
                "block_number": own[x].block_number,
                "slot": own[x].slot,
                "block_time": own[x].block_time.isoformat() if own[x].block_time else None,
                "status": own[x].status,
                "provider": own[x].provider,
                "verification": own[x].verification,
                "latest_corroboration_check": checks.get(x),
            }
            for x in allowed
        ],
        "rejected_ids": [x for x in wanted if x not in own],
    }


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            "wallet_classification",
            "Classify (rules + active model) and return history.",
            frozenset({"classification_analyst"}),
            _classification,
        ),
        Tool(
            "wallet_features",
            "Latest stored feature vector (values, availability).",
            frozenset({"classification_analyst", "behaviour_analyst"}),
            _features,
        ),
        Tool(
            "wallet_profile",
            "Scope-labelled profile, counterparties, interactions.",
            frozenset({"behaviour_analyst", "contract_token_analyst", "graph_analyst"}),
            _profile,
        ),
        Tool(
            "behaviour_rules",
            "Descriptive behaviour rules.",
            frozenset({"behaviour_analyst"}),
            _behaviour,
        ),
        Tool(
            "anomaly_detectors",
            "Deterministic anomaly detectors.",
            frozenset({"anomaly_analyst"}),
            _anomalies,
        ),
        Tool(
            "capital_flows",
            "Per-asset flows from the transfers table (no USD).",
            frozenset({"flow_analyst"}),
            _flows,
        ),
        Tool(
            "relationship_graph",
            "Depth-1 evidence-backed graph (bounded).",
            frozenset({"graph_analyst"}),
            _graph,
        ),
        Tool(
            "opportunity_leads",
            "Research leads touching this wallet's assets/targets.",
            frozenset({"opportunity_analyst"}),
            _opportunities,
        ),
        Tool(
            "provider_status",
            "Probe-based provider connectivity/reliability for the chain.",
            frozenset({"evidence_reviewer"}),
            _provider_status,
        ),
        Tool(
            "transaction_evidence",
            "Stored records and corroboration for given tx ids.",
            frozenset({"evidence_reviewer"}),
            _tx_evidence,
        ),
    )
}
