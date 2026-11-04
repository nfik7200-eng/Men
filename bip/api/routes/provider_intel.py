"""Provider capability and reliability intelligence (Stage 4).

Reading endpoints never contact a provider. Only ``POST /providers/probe-runs`` sends requests,
and only within the server-side budget (``BIP_PROBE__*`` ceilings). Callers can select
providers and capabilities by registry id and lower the per-provider request cap; they can never
supply URLs, raise budgets or add methods.

Registered before ``providers`` so that ``/providers/profiles`` etc. are not captured by
``/providers/{provider_id}``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query
from pydantic import Field

from bip.api.deps import AppContext, Context, WritableContext
from bip.api.errors import error_response
from bip.api.schemas import Body
from bip.db.types import utcnow
from bip.providers import capabilities as caps
from bip.providers.intelligence import coverage_counts, historical_coverage
from bip.providers.prober import ENVIRONMENT_RESTRICTION, eligibility
from bip.providers.status import compute_view
from bip.security.secrets import redact_text

router = APIRouter(tags=["provider intelligence"])
J = dict[str, Any]
ProviderId = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9\-]{1,63}$")]
CapId = Annotated[str, Field(pattern=r"^[a-z0-9_]{2,48}$")]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]


def _env(ctx: AppContext, body: J) -> J:
    return {"data_origin": ctx.probe_store.data_origin(), **body}


def _catalog_out(chain: str) -> list[J]:
    return [
        {
            "id": c.id,
            "label": c.label,
            "method": c.method,
            "description": c.description,
            "requires": c.requires,
            "derived_from": c.derived_from,
        }
        for c in caps.catalog(chain)
    ]


def _profile(ctx: AppContext, provider_id: str, *, detail: bool) -> J:
    spec = ctx.registry.get(provider_id)
    states = ctx.provider_state.states()
    view = compute_view(spec, states.get(spec.id), ctx.router.environ)
    chain = spec.chains[0] if spec.chains else ""
    latest = ctx.probe_store.latest_by_capability(spec.id) if spec.has_adapter else {}
    reason = eligibility(spec, states.get(spec.id), ctx.router)
    requested = [o for o in latest.values() if o["method"] and o["status"] != "not_tested"]
    last = max(requested, key=lambda o: o["observed_at"], default=None)
    errors = [o for o in requested if o["connectivity"] != "reachable"]
    last_error = max(errors, key=lambda o: o["observed_at"], default=None)
    successes = [o for o in requested if o["connectivity"] == "reachable"]
    last_run = ctx.probe_store.latest_provider_run(spec.id) if spec.has_adapter else None
    if not spec.has_adapter:
        connectivity = "not_tested"
    elif view.resolution.endpoint is None and spec.requires_credentials:
        connectivity = "authentication_required"
    elif last_run is None:
        connectivity = "not_tested"
    else:
        connectivity = last_run["connectivity"]
    rel = ctx.probe_store.reliability(spec.id) if spec.has_adapter else None
    out: J = {
        "id": spec.id,
        "name": spec.name,
        "operator": spec.operator,
        "kind": spec.kind,
        "chain": chain,
        "network": spec.network,
        "infrastructure": spec.infrastructure,
        "access": spec.access,
        "authentication_required": spec.requires_credentials,
        "endpoint_reference": spec.endpoint_env or "built-in public endpoint"
        if spec.has_adapter
        else None,
        "enabled": view.enabled,
        "configured": view.resolution.endpoint is not None,
        "configuration_note": redact_text(view.resolution.reason),
        "adapter_available": spec.has_adapter,
        "lifecycle": view.lifecycle,
        "probe_eligible": reason is None,
        "probe_ineligible_reason": reason,
        "connectivity": connectivity,
        "environment_restricted": bool(last_run and last_run["environment_restricted"]),
        "environment_note": ENVIRONMENT_RESTRICTION
        if last_run and last_run["environment_restricted"]
        else None,
        "last_run": last_run,
        "last_probe_at": last["observed_at"] if last else None,
        "last_successful_probe_at": max((o["observed_at"] for o in successes), default=None),
        "last_error": {
            k: last_error[k]
            for k in ("capability", "connectivity", "failure_kind", "explanation", "observed_at")
        }
        if last_error
        else None,
        "coverage": coverage_counts(chain, latest) if spec.has_adapter else None,
        "historical_coverage": historical_coverage(chain, latest) if spec.has_adapter else None,
        "reliability_state": rel["state"] if rel and last_run is not None else None,
        "advertised_methods": list(spec.methods),
        "documented": {
            "rate_limit": spec.rate_limit or "unknown",
            "archive": spec.archive or "unknown",
            "max_log_block_range": spec.max_log_block_range,
            "client_rps": spec.client_rps,
            "client_rps_note": "BIP's own politeness throttle, not a documented provider limit.",
            "basis": spec.documentation_basis,
            "checked": spec.documentation_checked,
            "sources": list(spec.sources),
            "docs_url": spec.docs_url,
            "note": "Documented facts are not verified operational capabilities; observed "
            "behaviour comes only from probe runs.",
        },
    }
    if detail:
        out["capabilities"] = (
            [{**c, "observation": latest.get(c["id"])} for c in _catalog_out(chain)]
            if spec.has_adapter
            else []
        )
        out["reliability"] = rel
        checks, _ = ctx.probe_store.consistency(chain=None, provider_id=spec.id, limit=20, offset=0)
        out["consistency_checks"] = checks
    return out


# ---------------------------------------------------------------------------- read


@router.get("/providers/capabilities")
def capability_catalog(ctx: Context) -> J:
    return _env(
        ctx,
        {
            "chains": {chain: _catalog_out(chain) for chain in caps.CATALOG},
            "statuses": {
                "supported": "The method returned a valid response for the tested request.",
                "unsupported": "The provider explicitly reported the method as not available.",
                "inconclusive": "The request failed in a way that establishes neither support nor "
                "non-support.",
                "not_tested": "Deliberately skipped or could not be tested safely.",
            },
        },
    )


@router.get("/providers/profiles")
def list_profiles(ctx: Context) -> J:
    items = [_profile(ctx, s.id, detail=False) for s in ctx.registry.all()]
    return _env(
        ctx,
        {
            "items": items,
            "generated_at": utcnow().isoformat(),
            "last_environment_block": ctx.probe_store.last_environment_block(),
        },
    )


@router.get("/providers/{provider_id}/profile")
def provider_profile(ctx: Context, provider_id: ProviderId) -> J:
    return _env(ctx, _profile(ctx, provider_id, detail=True))


@router.get("/providers/{provider_id}/capabilities/history")
def capability_history(
    ctx: Context,
    provider_id: ProviderId,
    limit: Limit = 50,
    offset: Offset = 0,
    capability: Annotated[str | None, Query(pattern=r"^[a-z0-9_]{2,48}$")] = None,
) -> Any:
    spec = ctx.registry.get(provider_id)
    if (
        capability is not None
        and spec.has_adapter
        and capability not in caps.capability_ids(spec.chains[0])
    ):
        return error_response(422, "invalid_input", f"unknown capability {capability!r}")
    items, total = ctx.probe_store.history(spec.id, capability, limit, offset)
    return _env(
        ctx,
        {"provider_id": spec.id, "total": total, "limit": limit, "offset": offset, "items": items},
    )


@router.get("/providers/{provider_id}/reliability")
def provider_reliability(ctx: Context, provider_id: ProviderId) -> J:
    spec = ctx.registry.get(provider_id)
    return _env(ctx, ctx.probe_store.reliability(spec.id))


@router.get("/providers/{provider_id}/consistency")
def provider_consistency(
    ctx: Context, provider_id: ProviderId, limit: Limit = 50, offset: Offset = 0
) -> J:
    spec = ctx.registry.get(provider_id)
    items, total = ctx.probe_store.consistency(
        chain=None, provider_id=spec.id, limit=limit, offset=offset
    )
    return _env(
        ctx,
        {
            "provider_id": spec.id,
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": items,
            "note": "Provider diagnostics only: these never change evidence verification status.",
        },
    )


# ---------------------------------------------------------------------------- probe


class ProbeRequest(Body):
    providers: (
        Annotated[
            list[Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9\-]{1,63}$")]],
            Field(min_length=1, max_length=20),
        ]
        | None
    ) = None
    capabilities: Annotated[list[CapId], Field(min_length=1, max_length=30)] | None = None
    max_calls_per_provider: Annotated[int, Field(ge=1, le=8)] | None = None
    """Can only lower the server's per-provider cap."""


def _preview(ctx: AppContext, body: ProbeRequest) -> J:
    plan = ctx.prober.plan(
        body.providers, body.capabilities, max_calls_per_provider=body.max_calls_per_provider
    )
    block = ctx.probe_store.last_environment_block()
    return {
        "budget": plan.budget,
        "max_requests": plan.max_requests,
        "max_duration_s": plan.budget["max_run_seconds"],
        "providers": [
            {
                "provider_id": p.spec.id,
                "chain": p.spec.chains[0],
                "infrastructure": p.spec.infrastructure,
                "authentication_required": p.spec.requires_credentials,
                "planned": [
                    {
                        "capability": c.id,
                        "method": c.method,
                        "derived": [d.id for d in caps.derived(c.chain, c.id)],
                    }
                    for c in p.requests
                ],
                "over_budget": [c.id for c in p.over_budget],
                "max_requests": len(p.requests),
            }
            for p in plan.providers
        ],
        "ineligible": [
            {"provider_id": k, "reason": redact_text(v)} for k, v in plan.ineligible.items()
        ],
        "known_network_restriction": block,
        "network_note": ENVIRONMENT_RESTRICTION if block else None,
        "notes": [
            "Each request is sent once (no retries), spaced at least "
            f"{plan.budget['min_interval_s']:g}s apart per provider.",
            "A provider that is rate-limited, denies access, serves another network or cannot "
            "be reached is not probed further in the run.",
            "Transaction lookups use a hash or signature taken from the provider's own live "
            "answer in the same run.",
        ],
    }


@router.get("/providers/probe/preview")
def probe_preview(
    ctx: Context,
    provider: Annotated[list[str] | None, Query(max_length=20)] = None,
    capability: Annotated[list[str] | None, Query(max_length=30)] = None,
    max_calls_per_provider: Annotated[int | None, Query(ge=1, le=8)] = None,
) -> J:
    body = ProbeRequest(
        providers=provider, capabilities=capability, max_calls_per_provider=max_calls_per_provider
    )
    return _env(ctx, _preview(ctx, body))


@router.post("/providers/probe-runs", status_code=201)
def start_probe(ctx: WritableContext, body: ProbeRequest) -> Any:
    """Run one bounded, synchronous probe (≤ 90 s). One run at a time (409 otherwise)."""
    plan = ctx.prober.plan(
        body.providers, body.capabilities, max_calls_per_provider=body.max_calls_per_provider
    )
    if not plan.providers:
        return error_response(
            409,
            "nothing_to_probe",
            "No registered provider is eligible for probing "
            "(enabled, adapter available, endpoint configured).",
        )
    if not ctx.probe_lock.acquire(blocking=False):
        return error_response(409, "probe_busy", "A probe run is already in progress.")
    try:
        started = utcnow()
        run_id, key = ctx.probe_store.start_run(plan, body.capabilities)
        try:
            results = ctx.prober.run(plan)
        except Exception as exc:
            ctx.probe_store.finish_run(
                run_id, [], started=started, error=f"internal error: {type(exc).__name__}"
            )
            raise
        ctx.probe_store.finish_run(run_id, results, started=started)
    finally:
        ctx.probe_lock.release()
    return _env(ctx, ctx.probe_store.run(key))


@router.get("/providers/probe-runs")
def list_probe_runs(ctx: Context, limit: Limit = 20, offset: Offset = 0) -> J:
    items, total = ctx.probe_store.runs(limit, offset)
    return _env(ctx, {"total": total, "limit": limit, "offset": offset, "items": items})


@router.get("/providers/probe-runs/{run_id}")
def get_probe_run(ctx: Context, run_id: Annotated[str, Path(pattern=r"^[0-9a-f\-]{36}$")]) -> J:
    return _env(ctx, ctx.probe_store.run(run_id))
