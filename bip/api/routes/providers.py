from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Path

from bip.api.deps import AppContext, Context, WritableContext
from bip.api.errors import error_response
from bip.api.schemas import (
    HealthCheckOut,
    ProviderDetail,
    ProviderOut,
    ProviderSummary,
    ProviderUpdate,
)
from bip.chains.common import integer
from bip.chains.ethereum.parse import quantity
from bip.chains.identity import EXPECTED_IDENTITY, identity_matches
from bip.core.exceptions import BIPError
from bip.db.types import utcnow
from bip.providers.state import HealthCheckRecord
from bip.providers.status import ProviderView, all_views, compute_view
from bip.router.router import NoEligibleProviderError
from bip.security.secrets import redact_text

router = APIRouter(tags=["providers"])
ProviderId = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9\-]{1,63}$")]


def to_out(v: ProviderView) -> ProviderOut:
    s = v.spec
    st = v.state
    endpoint = v.resolution.endpoint
    return ProviderOut(
        id=s.id,
        name=s.name,
        operator=s.operator,
        kind=s.kind,
        protocol=s.protocol,
        chains=s.chains,
        network=s.network,
        access=s.access,
        lifecycle=v.lifecycle,
        enabled=v.enabled,
        adapter_available=s.has_adapter,
        configuration_status=v.resolution.configuration,
        configuration_note=redact_text(v.resolution.reason),
        endpoint_host=endpoint.host
        if endpoint is not None and not endpoint.from_environment
        else None,
        methods=s.methods,
        archive=s.archive or "unknown",
        max_log_block_range=s.max_log_block_range,
        rate_limit=s.rate_limit or "unknown",
        client_rps=s.client_rps,
        limitations=v.limitations,
        docs_url=s.docs_url,
        terms_url=s.terms_url,
        sources=s.sources,
        notes=s.notes,
        last_check_at=st.last_check_at,
        last_success_at=st.last_success_at,
        last_failure_at=st.last_failure_at,
        consecutive_failures=st.consecutive_failures,
        last_latency_ms=st.last_latency_ms,
        last_outcome=st.last_outcome,
    )


def provider_views(ctx: AppContext) -> list[ProviderView]:
    return all_views(ctx.registry, ctx.provider_state.states(), ctx.router.environ)


def summarize(views: list[ProviderView]) -> ProviderSummary:
    def count(state: str) -> int:
        return sum(v.lifecycle == state for v in views)

    return ProviderSummary(
        total=len(views),
        rpc_providers=sum(v.spec.has_adapter for v in views),
        configured=sum(v.resolution.endpoint is not None for v in views if v.spec.has_adapter),
        adapters_available=sum(v.spec.has_adapter for v in views),
        healthy=count("healthy"),
        degraded=count("degraded"),
        unavailable=count("unavailable"),
        untested=count("untested"),
        disabled=count("disabled"),
    )


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(ctx: Context) -> list[ProviderOut]:
    return [to_out(v) for v in provider_views(ctx)]


@router.get("/providers/status", response_model=list[ProviderOut])
def provider_status(ctx: Context) -> list[ProviderOut]:
    """Same data as ``/providers`` (kept for BIP 0.1 clients)."""
    return list_providers(ctx)


@router.get("/providers/{provider_id}", response_model=ProviderDetail)
def get_provider(ctx: Context, provider_id: ProviderId) -> ProviderDetail:
    spec = ctx.registry.get(provider_id)
    view = compute_view(spec, ctx.provider_state.states().get(spec.id), ctx.router.environ)
    checks = ctx.provider_state.recent_health_checks(provider_id, 10)
    return ProviderDetail(
        **to_out(view).model_dump(),
        recent_checks=tuple(
            HealthCheckOut(
                provider_id=c.provider_id,
                checked_at=c.checked_at,
                ok=c.ok,
                latency_ms=c.latency_ms,
                outcome=c.outcome,
                detail=c.detail,
                observed_network=c.observed_network,
                head_block=c.head_block,
                head_slot=c.head_slot,
            )
            for c in checks
        ),
    )


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
def update_provider(
    ctx: WritableContext, provider_id: ProviderId, body: ProviderUpdate
) -> ProviderOut:
    spec = ctx.registry.get(provider_id)
    ctx.provider_state.set_enabled(spec.id, body.enabled, body.reason)
    return to_out(compute_view(spec, ctx.provider_state.states().get(spec.id), ctx.router.environ))


@router.post("/providers/{provider_id}/health-check", response_model=HealthCheckOut)
def health_check(ctx: WritableContext, provider_id: ProviderId):
    """Bounded read-only check: network identity + head position, on this provider only."""
    spec = ctx.registry.get(provider_id)
    if not spec.has_adapter:
        return error_response(409, "provider_not_checkable", "This source has no RPC adapter yet.")
    chain = spec.chains[0]
    started = time.monotonic()
    ok, outcome, detail = False, "error", None
    head_block = head_slot = None
    observed = None
    try:
        identity = ctx.router.call(chain, EXPECTED_IDENTITY[chain].method, [], provider_id=spec.id)
        observed = str(identity.result)[:100]
        if not identity_matches(chain, identity.result):
            outcome, detail = "network_mismatch", f"expected {EXPECTED_IDENTITY[chain].label}"
        elif chain == "ethereum":
            head = ctx.router.call(chain, "eth_blockNumber", [], provider_id=spec.id)
            head_block = quantity(head.result, "eth_blockNumber")
            ok, outcome = True, "ok"
        else:
            head = ctx.router.call(
                chain, "getSlot", [{"commitment": "finalized"}], provider_id=spec.id
            )
            head_slot = integer(head.result, "getSlot")
            ok, outcome = True, "ok"
    except NoEligibleProviderError as exc:
        reason = exc.reasons.get(spec.id, "not usable")
        return error_response(409, "provider_not_usable", redact_text(f"{spec.id}: {reason}"))
    except BIPError as exc:
        outcome, detail = type(exc).__name__, redact_text(str(exc))[:300]
    record = HealthCheckRecord(
        provider_id=spec.id,
        checked_at=utcnow(),
        ok=ok,
        latency_ms=int((time.monotonic() - started) * 1000),
        outcome=outcome,
        detail=detail,
        observed_network=observed,
        head_block=head_block,
        head_slot=head_slot,
    )
    ctx.provider_state.record_health_check(record)
    if ok:
        ctx.ingestor.store.record_network_observation(
            chain=chain,
            network=spec.network or "",
            provider_id=spec.id,
            identity=EXPECTED_IDENTITY[chain].label,
            head_block=head_block,
            head_slot=head_slot,
            commitment="finalized" if chain == "solana" else None,
        )
    return HealthCheckOut(
        provider_id=record.provider_id,
        checked_at=record.checked_at,
        ok=record.ok,
        latency_ms=record.latency_ms,
        outcome=record.outcome,
        detail=record.detail,
        observed_network=record.observed_network,
        head_block=record.head_block,
        head_slot=record.head_slot,
    )
