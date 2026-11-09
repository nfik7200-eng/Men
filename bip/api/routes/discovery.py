"""Discovery feed, signals, alerts and alert rules.

Signals are *candidates*: scored, explained and deduplicated observations over data BIP already
stored. They are not findings and not attribution. Every response says what the data covers.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, Response, status

from bip.api.deps import INGEST_RESERVE_BYTES, Context, WritableContext
from bip.api.errors import error_response
from bip.api.schemas import (
    AlertRuleCreate,
    AlertRuleUpdate,
    DiscoveryRunRequest,
    SignalDismiss,
    SignalPromote,
)
from bip.discovery import DISCOVERY_VERSION

router = APIRouter(tags=["discovery"])
Id = Annotated[int, Path(ge=1, le=2**53)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0, le=100_000)]


def _envelope(ctx: Context, body: dict[str, Any]) -> dict[str, Any]:
    origin = ctx.repo.get_meta("data_origin") or "stored_observations"
    return {"data_origin": origin, "discovery_version": DISCOVERY_VERSION, **body}


# ----------------------------------------------------------------------------- discovery


@router.get("/discovery/detectors")
def detectors(ctx: Context) -> dict[str, Any]:
    """Every detector with its method, coverage, limitations, refresh frequency and cost."""
    return _envelope(ctx, {"detectors": ctx.discovery.detectors()})


@router.post("/discovery/run", status_code=status.HTTP_201_CREATED)
def run_discovery(ctx: WritableContext, body: DiscoveryRunRequest) -> Any:
    """Run all detectors once over stored data. Reads the database only: no provider is called."""
    if not ctx.discovery_lock.acquire(blocking=False):
        return error_response(409, "discovery_busy", "A discovery pass is already running.")
    try:
        return _envelope(ctx, ctx.discovery.run(body.chain))
    finally:
        ctx.discovery_lock.release()


@router.get("/discovery/collector")
def collector_status(ctx: Context) -> dict[str, Any]:
    """Cursor, limits, the last run's manifest and what the collector does not cover."""
    return _envelope(ctx, ctx.collector.status())


@router.post("/discovery/collect/ethereum", status_code=status.HTTP_201_CREATED)
def collect_ethereum(ctx: WritableContext) -> Any:
    """Read the next few finalized Ethereum blocks once. Admin only: this CONTACTS PROVIDERS.

    Bounded by blocks, receipts, an RPC call budget and a deadline; shares the ingestion lock, so
    it never runs alongside a wallet ingestion.
    """
    ctx.disk_guard.require(INGEST_RESERVE_BYTES)
    if not ctx.ingest_lock.acquire(blocking=False):
        return error_response(
            409, "ingestion_busy", "Another ingestion run is in progress. Try again shortly."
        )
    try:
        return _envelope(ctx, ctx.collector.run())
    finally:
        ctx.ingest_lock.release()


@router.get("/discovery/collector/solana")
def solana_collector_status(ctx: Context) -> dict[str, Any]:
    """Cursor, coverage gaps, limits, recognised programs (with sources) and the last run."""
    return _envelope(ctx, ctx.solana_collector.status())


@router.post("/discovery/collect/solana", status_code=status.HTTP_201_CREATED)
def collect_solana(ctx: WritableContext) -> Any:
    """Read the next few finalized Solana slots once. Admin only: this CONTACTS PROVIDERS.

    Bounded by slots, an RPC call budget and a deadline; shares the ingestion lock.
    """
    ctx.disk_guard.require(INGEST_RESERVE_BYTES)
    if not ctx.ingest_lock.acquire(blocking=False):
        return error_response(
            409, "ingestion_busy", "Another ingestion run is in progress. Try again shortly."
        )
    try:
        return _envelope(ctx, ctx.solana_collector.run())
    finally:
        ctx.ingest_lock.release()


@router.get("/discovery/performance")
def detector_performance(ctx: Context) -> dict[str, Any]:
    """Per-detector outcomes of analyst review (a false-positive proxy, with its caveats)."""
    return _envelope(ctx, ctx.discovery.performance())


@router.get("/discovery/runs")
def discovery_runs(ctx: Context, limit: Annotated[int, Query(ge=1, le=50)] = 10) -> dict[str, Any]:
    return _envelope(ctx, {"items": ctx.discovery.runs(limit)})


@router.get("/discovery/feed")
def discovery_feed(ctx: Context, limit: Limit = 50, offset: Offset = 0) -> dict[str, Any]:
    """Open signals ranked by score, with the freshness of the pass behind them."""
    return _envelope(ctx, ctx.discovery.feed(limit, offset))


# ----------------------------------------------------------------------------- signals


@router.get("/signals")
def list_signals(
    ctx: Context,
    status_: Annotated[
        Literal["open", "dismissed", "promoted", "invalidated", "expired"] | None,
        Query(alias="status"),
    ] = None,
    family: Annotated[str | None, Query(max_length=40)] = None,
    chain: Annotated[str | None, Query(max_length=32)] = None,
    severity: Literal["low", "medium", "high"] | None = None,
    detector: Annotated[str | None, Query(max_length=64)] = None,
    subject: Annotated[str | None, Query(max_length=100)] = None,
    min_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    sort: Literal["score", "recent"] = "score",
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    return _envelope(
        ctx,
        ctx.discovery.list_signals(
            status=status_,
            family=family,
            chain=chain,
            severity=severity,
            detector_id=detector,
            subject_ref=subject,
            min_score=min_score,
            sort=sort,
            limit=limit,
            offset=offset,
        ),
    )


@router.get("/signals/{signal_id}")
def get_signal(ctx: Context, signal_id: Id) -> dict[str, Any]:
    return _envelope(ctx, ctx.discovery.get_signal(signal_id))


@router.post("/signals/{signal_id}/dismiss")
def dismiss_signal(ctx: WritableContext, signal_id: Id, body: SignalDismiss) -> dict[str, Any]:
    return _envelope(ctx, ctx.discovery.dismiss(signal_id, body.note))


@router.post("/signals/{signal_id}/promote")
def promote_signal(ctx: WritableContext, signal_id: Id, body: SignalPromote) -> dict[str, Any]:
    """Move a signal into a watchlist (the subject is watched) or a case (it becomes an
    unverified finding with its evidence). The signal is marked ``promoted``."""
    if body.target == "watchlist":
        assert body.watchlist_id is not None  # noqa: S101 - enforced by the schema
        result = ctx.discovery.promote_to_watchlist(signal_id, body.watchlist_id, body.note)
    else:
        assert body.investigation_id is not None  # noqa: S101 - enforced by the schema
        result = ctx.discovery.promote_to_investigation(signal_id, body.investigation_id)
    return _envelope(ctx, result)


# ----------------------------------------------------------------------------- alerts


@router.get("/alerts")
def list_alerts(
    ctx: Context, unread: bool = False, limit: Limit = 50, offset: Offset = 0
) -> dict[str, Any]:
    return _envelope(ctx, ctx.discovery.list_alerts(unread_only=unread, limit=limit, offset=offset))


@router.get("/alerts/channels")
def alert_channels(ctx: Context) -> dict[str, Any]:
    return _envelope(ctx, ctx.discovery.channels())


@router.post("/alerts/read-all")
def read_all_alerts(ctx: WritableContext) -> dict[str, Any]:
    return _envelope(ctx, {"marked_read": ctx.discovery.mark_all_read()})


@router.post("/alerts/{alert_id}/read")
def read_alert(ctx: WritableContext, alert_id: Id) -> dict[str, Any]:
    return _envelope(ctx, ctx.discovery.mark_read(alert_id))


@router.get("/alert-rules")
def list_rules(ctx: Context) -> dict[str, Any]:
    return _envelope(ctx, {"items": ctx.discovery.list_rules()})


@router.post("/alert-rules", status_code=status.HTTP_201_CREATED)
def create_rule(ctx: WritableContext, body: AlertRuleCreate) -> dict[str, Any]:
    return _envelope(
        ctx,
        ctx.discovery.create_rule(
            body.name,
            min_severity=body.min_severity,
            min_score=body.min_score,
            families=body.families,
            chains=body.chains,
            enabled=body.enabled,
        ),
    )


@router.patch("/alert-rules/{rule_id}")
def update_rule(ctx: WritableContext, rule_id: Id, body: AlertRuleUpdate) -> dict[str, Any]:
    return _envelope(ctx, ctx.discovery.update_rule(rule_id, body.model_dump(exclude_none=True)))


@router.delete("/alert-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(ctx: WritableContext, rule_id: Id) -> Response:
    ctx.discovery.delete_rule(rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
