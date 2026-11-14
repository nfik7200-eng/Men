"""Wallet intelligence, graph, flows, anomalies, research leads and verification.

Every response carries ``data_origin`` (``offline_fixture`` when the database was filled by the
offline fixture backend, otherwise ``stored_observations``), scope/coverage and verification.
Analysis endpoints read stored observations only; the only network access is the explicit
corroboration POST, which goes through the provider router.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query
from pydantic import Field
from sqlalchemy import func, select

from bip.api.deps import AppContext, Context, WritableContext
from bip.api.errors import error_response
from bip.api.routes.chains import _require_chain  # pyright: ignore[reportPrivateUsage]
from bip.api.schemas import Body
from bip.contracts.provenance import Provenance
from bip.db import schema
from bip.db.errors import InvalidRecordError, RecordNotFoundError
from bip.db.repository import canonical_address
from bip.intel import ANALYZER_VERSION, behaviour, flows, graph, opportunities, profile
from bip.intel.anomaly import DETECTORS
from bip.intel.common import J, iso, label
from bip.intel.dataset import VERIFICATION_LABEL
from bip.intel.service import as_of_now
from bip.intel.verify import stored_observation

router = APIRouter(prefix="/intelligence", tags=["intelligence"])

ChainP = Annotated[str, Path(min_length=1, max_length=32)]
AddressP = Annotated[str, Path(min_length=1, max_length=100)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0, le=100_000)]
MAX_WALLET_VERIFY = 20
MAX_WATCHLIST_ANALYZE = 50
WALLET_VERIFY_DEADLINE_S = 60.0


def origin(ctx: AppContext) -> str:
    return ctx.repo.get_meta("data_origin") or "stored_observations"


def envelope(ctx: AppContext, body: J) -> J:
    return {"data_origin": origin(ctx), "analyzer_version": ANALYZER_VERSION, **body}


def _wallet(ctx: AppContext, chain: str, address: str) -> tuple[str, str]:
    _require_chain(ctx, chain)
    ref = canonical_address(chain, address)
    return ref.chain, ref.address


def _page(items: list[Any], limit: int, offset: int) -> J:
    return {
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "items": items[offset : offset + limit],
    }


# ---------------------------------------------------------------------------- status


@router.get("/status")
def intel_status(ctx: Context) -> J:
    """Counts for the Overview page (no network access)."""
    ev, ar, ao, oc = (
        schema.evidence_verifications,
        schema.analysis_runs,
        schema.anomaly_observations,
        schema.opportunity_candidates,
    )
    with ctx.repo.transaction() as conn:
        latest = (
            select(ev.c.chain, ev.c.tx_id, func.max(ev.c.id).label("mid"))
            .group_by(ev.c.chain, ev.c.tx_id)
            .subquery()
        )
        outcomes = dict(
            conn.execute(
                select(ev.c.outcome, func.count())
                .join(latest, ev.c.id == latest.c.mid)
                .group_by(ev.c.outcome)
            ).all()
        )
        stored_tx = int(
            conn.execute(select(func.count()).select_from(schema.transactions)).scalar_one()
        )
        runs = int(conn.execute(select(func.count()).select_from(ar)).scalar_one())
        analysed = int(
            conn.execute(
                select(func.count(func.distinct(ar.c.chain + ":" + ar.c.address)))
            ).scalar_one()
        )
        anomalies = int(
            conn.execute(
                select(func.count()).select_from(ao).where(ao.c.status == "anomaly")
            ).scalar_one()
        )
        candidates = int(conn.execute(select(func.count()).select_from(oc)).scalar_one())
        findings = int(conn.execute(select(func.count()).select_from(schema.findings)).scalar_one())
        provider_obs = int(
            conn.execute(
                select(func.count()).select_from(schema.provider_observations)
            ).scalar_one()
        )
    checked = sum(int(v) for v in outcomes.values())
    return envelope(
        ctx,
        {
            "verification": {
                "stored_transactions": stored_tx,
                "checked": checked,
                "corroborated": int(outcomes.get("cross_verified", 0)),
                "conflicted": int(outcomes.get("conflict", 0)),
                "invalidated": int(outcomes.get("invalidated", 0)),
                "unverified": stored_tx - checked + int(outcomes.get("unverified", 0)),
                "provider_observations": provider_obs,
            },
            "analysis_runs": runs,
            "analysed_wallets": analysed,
            "anomalies": anomalies,
            "findings": findings,
            "opportunity_candidates": candidates,
            "detectors": [spec.describe() for spec, _ in DETECTORS],
            "verification_labels": VERIFICATION_LABEL,
        },
    )


# ---------------------------------------------------------------------------- wallets


@router.get("/wallets/{chain}/{address}/profile")
def wallet_profile(ctx: Context, chain: ChainP, address: AddressP) -> J:
    c, a = _wallet(ctx, chain, address)
    ds = ctx.intel.dataset(c, a)
    prof = profile.build_profile(ds)
    prof["activity"] = prof["activity"][-90:]
    return envelope(
        ctx,
        {
            **prof,
            "latest_analysis": ctx.intel.latest_run(c, a),
            "findings": ctx.intel.wallet_findings(c, a),
        },
    )


@router.get("/wallets/{chain}/{address}/activity")
def wallet_activity(
    ctx: Context, chain: ChainP, address: AddressP, limit: Limit = 90, offset: Offset = 0
) -> J:
    c, a = _wallet(ctx, chain, address)
    ds = ctx.intel.dataset(c, a)
    days = list(reversed(profile.activity(ds)))
    return envelope(
        ctx,
        {
            "chain": c,
            "address": a,
            "scope": profile.scope(ds),
            "order": "newest first",
            **_page(days, limit, offset),
        },
    )


@router.get("/wallets/{chain}/{address}/counterparties")
def wallet_counterparties(
    ctx: Context, chain: ChainP, address: AddressP, limit: Limit = 50, offset: Offset = 0
) -> J:
    c, a = _wallet(ctx, chain, address)
    ds = ctx.intel.dataset(c, a)
    cps = profile.counterparties(ds, limit=10_000)
    return envelope(
        ctx,
        {
            "chain": c,
            "address": a,
            "scope": profile.scope(ds),
            **_page(cps["items"], limit, offset),
        },
    )


@router.get("/wallets/{chain}/{address}/behaviour")
def wallet_behaviour(ctx: Context, chain: ChainP, address: AddressP) -> J:
    c, a = _wallet(ctx, chain, address)
    ds = ctx.intel.dataset(c, a)
    rules = behaviour.evaluate(ds, ctx.settings.intel, as_of_now())
    return envelope(
        ctx,
        {
            "chain": c,
            "address": a,
            "scope": profile.scope(ds),
            "rules_version": behaviour.RULES_VERSION,
            "items": rules,
            "note": "Descriptive rules only. No motive, identity or ownership is "
            "inferred; pattern labels are hypotheses.",
        },
    )


@router.post("/analyze/{chain}/{address}", status_code=201)
def analyze(ctx: WritableContext, chain: ChainP, address: AddressP) -> Any:
    c, a = _wallet(ctx, chain, address)
    if not ctx.analysis_lock.acquire(blocking=False):
        return error_response(409, "analysis_busy", "Another analysis is running. Try again.")
    try:
        return envelope(ctx, ctx.intel.analyze(c, a))
    finally:
        ctx.analysis_lock.release()


@router.post("/watchlists/{watchlist_id}/analyze", status_code=201)
def analyze_watchlist(
    ctx: WritableContext, watchlist_id: Annotated[int, Path(ge=1, le=2**53)]
) -> Any:
    wl = ctx.repo.get_watchlist(watchlist_id)
    if not ctx.analysis_lock.acquire(blocking=False):
        return error_response(409, "analysis_busy", "Another analysis is running. Try again.")
    try:
        results = [
            {
                "chain": e.chain,
                "address": e.address,
                **{
                    k: v
                    for k, v in ctx.intel.analyze(e.chain, e.address).items()
                    if k in {"run_id", "status", "reused", "input_hash"}
                },
            }
            for e in wl.entries[:MAX_WATCHLIST_ANALYZE]
        ]
    finally:
        ctx.analysis_lock.release()
    return envelope(
        ctx,
        {
            "watchlist_id": watchlist_id,
            "analysed": len(results),
            "skipped": max(0, len(wl.entries) - MAX_WATCHLIST_ANALYZE),
            "items": results,
        },
    )


# ---------------------------------------------------------------------------- graph / flows


@router.get("/graph/{chain}/{address}")
def wallet_graph(
    ctx: Context,
    chain: ChainP,
    address: AddressP,
    depth: Annotated[int, Query(ge=1, le=3)] = 1,
    max_nodes: Annotated[int | None, Query(ge=2, le=1000)] = None,
    max_edges: Annotated[int | None, Query(ge=1, le=3000)] = None,
) -> J:
    c, a = _wallet(ctx, chain, address)
    return envelope(
        ctx,
        graph.build_graph(
            ctx.repo,
            c,
            a,
            ctx.settings.intel,
            depth=depth,
            max_nodes=max_nodes,
            max_edges=max_edges,
        ),
    )


@router.get("/flows/{chain}/{address}")
def wallet_flows(
    ctx: Context,
    chain: ChainP,
    address: AddressP,
    since: datetime | None = None,
    until: datetime | None = None,
) -> J:
    c, a = _wallet(ctx, chain, address)
    for v in (since, until):
        if v is not None and v.tzinfo is None:
            raise InvalidRecordError("since/until must include a timezone (e.g. Z)")
    if since and until and until < since:
        raise InvalidRecordError("until is before since")
    ds = ctx.intel.dataset(c, a)
    return envelope(
        ctx,
        {
            **flows.build_flows(
                ds,
                since=since,
                until=until,
                max_window_days=ctx.settings.intel.flow_max_window_days,
            ),
            "scope": profile.scope(ds),
        },
    )


# ---------------------------------------------------------------------------- anomalies


@router.get("/anomalies")
def list_anomalies(
    ctx: Context,
    limit: Limit = 50,
    offset: Offset = 0,
    chain: Annotated[str | None, Query(max_length=32)] = None,
    address: Annotated[str | None, Query(max_length=100)] = None,
    status: Annotated[
        str | None, Query(pattern="^(anomaly|no_anomaly|insufficient_data|could_not_run)$")
    ] = None,
    watchlist_id: Annotated[int | None, Query(ge=1, le=2**53)] = None,
) -> J:
    items, total = ctx.intel.anomalies(
        chain=chain,
        address=address,
        status=status,
        watchlist_id=watchlist_id,
        limit=limit,
        offset=offset,
    )
    return envelope(ctx, {"total": total, "limit": limit, "offset": offset, "items": items})


@router.get("/anomalies/{finding_id}")
def get_anomaly(
    ctx: Context,
    finding_id: Annotated[
        str, Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:\-]*$")
    ],
) -> J:
    return envelope(ctx, ctx.intel.anomaly(finding_id))


class AttachBody(Body):
    investigation_id: Annotated[int, Field(ge=1, le=2**53)]


@router.post("/findings/{finding_id}/attach")
def attach_finding(
    ctx: WritableContext,
    body: AttachBody,
    finding_id: Annotated[
        str, Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:\-]*$")
    ],
) -> J:
    return envelope(ctx, ctx.intel.attach_finding(finding_id, body.investigation_id))


# ---------------------------------------------------------------------------- opportunities


@router.get("/opportunities")
def list_opportunities(
    ctx: Context,
    limit: Limit = 50,
    offset: Offset = 0,
    chain: Annotated[str | None, Query(max_length=32)] = None,
    current_only: bool = True,
) -> J:
    items, total = opportunities.list_candidates(
        ctx.repo, chain=chain, current_only=current_only, limit=limit, offset=offset
    )
    return envelope(
        ctx,
        {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": items,
            "disclaimer": "Research leads only. Not investment advice.",
        },
    )


@router.post("/opportunities/refresh/{chain}")
def refresh_opportunities(ctx: WritableContext, chain: ChainP) -> Any:
    _require_chain(ctx, chain)
    if not ctx.analysis_lock.acquire(blocking=False):
        return error_response(409, "analysis_busy", "Another analysis is running. Try again.")
    try:
        result = opportunities.evaluate(ctx.repo, chain)
        opportunities.persist(ctx.repo, chain, result["candidates"])
    finally:
        ctx.analysis_lock.release()
    return envelope(ctx, result)


@router.get("/opportunities/{candidate_id}")
def get_opportunity(
    ctx: Context, candidate_id: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]
) -> J:
    c = opportunities.get_candidate(ctx.repo, candidate_id)
    if c is None:
        raise RecordNotFoundError(f"candidate {candidate_id} not found")
    return envelope(ctx, c)


# ---------------------------------------------------------------------------- verification

TxIdP = Annotated[str, Path(min_length=10, max_length=100, pattern=r"^[0-9A-Za-z]+$")]


def _tx_id(chain: str, tx_id: str) -> str:
    return tx_id.lower() if chain == "ethereum" else tx_id


@router.get("/verification/{chain}/{transaction_id}")
def get_verification(ctx: Context, chain: ChainP, transaction_id: TxIdP) -> J:
    _require_chain(ctx, chain)
    tx_id = _tx_id(chain, transaction_id)
    ev, po = schema.evidence_verifications, schema.provider_observations
    stored, row = stored_observation(ctx.repo, chain, tx_id)
    with ctx.repo.transaction() as conn:
        checks = (
            conn.execute(
                select(ev)
                .where(ev.c.chain == chain, ev.c.tx_id == tx_id)
                .order_by(ev.c.id.desc())
                .limit(20)
            )
            .mappings()
            .all()
        )
        obs = (
            conn.execute(
                select(po)
                .where(po.c.chain == chain, po.c.tx_id == tx_id)
                .order_by(po.c.id.desc())
                .limit(40)
            )
            .mappings()
            .all()
        )
    prov = Provenance.model_validate_json(row["provenance_json"])
    effective = checks[0]["outcome"] if checks else prov.verification_status.value
    return envelope(
        ctx,
        {
            "chain": chain,
            "tx_id": tx_id,
            "effective_status": effective,
            "label": label(effective),
            "source_provider": prov.source_provider,
            "stored": stored,
            "checks": [
                {
                    "id": c["id"],
                    "checked_at": iso(c["checked_at"]),
                    "outcome": c["outcome"],
                    "label": label(c["outcome"]),
                    "independent_providers": c["independent_providers"],
                    "providers": json.loads(c["providers_json"]),
                    "differences": json.loads(c["differences_json"]),
                    "note": c["note"],
                }
                for c in checks
            ],
            "provider_observations": [
                {
                    "provider_id": o["provider_id"],
                    "infrastructure": o["infrastructure"],
                    "fetched_at": iso(o["fetched_at"]),
                    "found": o["found"],
                    "fields": json.loads(o["fields_json"] or "null"),
                    "raw_ref": o["raw_ref"],
                    "verification_id": o["verification_id"],
                }
                for o in obs
            ],
            "rule": "Corroborated only when providers on at least two independent infrastructure "
            "groups report identical normalized fields.",
        },
    )


@router.post("/verification/{chain}/{transaction_id}")
def verify_transaction(ctx: WritableContext, chain: ChainP, transaction_id: TxIdP) -> Any:
    _require_chain(ctx, chain)
    if not ctx.verify_lock.acquire(blocking=False):
        return error_response(409, "verification_busy", "A verification is running. Try again.")
    try:
        return envelope(ctx, ctx.corroborator.verify(chain, _tx_id(chain, transaction_id)))
    finally:
        ctx.verify_lock.release()


@router.post("/verification/{chain}/wallet/{address}")
def verify_wallet(
    ctx: WritableContext,
    chain: ChainP,
    address: AddressP,
    limit: Annotated[int, Query(ge=1, le=MAX_WALLET_VERIFY)] = 5,
) -> Any:
    """Corroborate the most recent not-yet-corroborated transactions of one wallet (bounded:
    one verification at a time, at most ``limit`` transactions and WALLET_VERIFY_DEADLINE_S)."""
    c, a = _wallet(ctx, chain, address)
    if not ctx.verify_lock.acquire(blocking=False):
        return error_response(409, "verification_busy", "A verification is running. Try again.")
    try:
        ds = ctx.intel.dataset(c, a)
        todo = [t.tx_id for t in ds.transactions if t.verification != "cross_verified"][:limit]
        results: list[J] = []
        deadline = time.monotonic() + WALLET_VERIFY_DEADLINE_S
        for tx in todo:
            if time.monotonic() > deadline:
                break
            r = ctx.corroborator.verify(c, tx)
            results.append(r)
            if r["status"] == "could_not_run" and not r.get("observations"):
                break  # no independent provider is eligible: repeating cannot help
    finally:
        ctx.verify_lock.release()
    summary: dict[str, int] = {}
    for r in results:
        key = r["outcome"] or r["status"]
        summary[key] = summary.get(key, 0) + 1
    return envelope(
        ctx,
        {
            "chain": c,
            "address": a,
            "requested": len(todo),
            "checked": len(results),
            "summary": summary,
            "items": [
                {
                    k: r.get(k)
                    for k in (
                        "tx_id",
                        "status",
                        "outcome",
                        "label",
                        "note",
                        "reason",
                        "independent_providers",
                    )
                }
                for r in results
            ],
        },
    )
