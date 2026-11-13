from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, status

from bip.api.deps import INGEST_RESERVE_BYTES, Context, WritableContext
from bip.api.errors import error_response
from bip.api.routes.chains import _require_chain  # pyright: ignore[reportPrivateUsage]
from bip.api.schemas import Out
from bip.db.checkpoints import GAP_STATUSES, Checkpoint, CheckpointStore, StreamRef
from bip.db.errors import RecordNotFoundError
from bip.db.repository import canonical_address
from bip.ingest.backfill import VERSIONS

router = APIRouter(tags=["ingestion"])


class IngestionError(Out):
    occurred_at: datetime
    stage: str
    item_ref: str | None
    error_code: str
    detail: str


class IngestionJob(Out):
    id: int
    chain: str
    address: str
    status: str
    requested_limit: int
    started_at: datetime
    finished_at: datetime | None
    providers: tuple[str, ...]
    rpc_calls: int
    transactions_seen: int
    transactions_stored: int
    transfers_stored: int
    balance_changes_stored: int
    error_count: int
    coverage: dict[str, object] | None
    message: str | None
    mode: str = "synchronous"
    errors: tuple[IngestionError, ...] = ()


def to_job(row: dict[str, object]) -> IngestionJob:
    cov = row.get("coverage_json")
    data = {k: v for k, v in row.items() if k in IngestionJob.model_fields}
    data["providers"] = json.loads(str(row.get("providers_json") or "[]"))
    data["coverage"] = json.loads(str(cov)) if cov else None
    data["errors"] = row.get("errors", [])
    return IngestionJob.model_validate(data)


@router.post(
    "/ingestion/wallet/{chain}/{address}",
    response_model=IngestionJob,
    status_code=status.HTTP_201_CREATED,
)
def ingest_wallet(
    ctx: WritableContext,
    chain: Annotated[str, Path(min_length=1, max_length=32)],
    address: Annotated[str, Path(min_length=1, max_length=100)],
    limit: Annotated[int | None, Query(ge=1, le=50)] = None,
):
    """Run one bounded, synchronous, read-only ingestion for a wallet.

    Only one run executes at a time; concurrent requests get 409. Providers are chosen by the
    router; callers cannot pass URLs or provider ids.
    """
    _require_chain(ctx, chain)
    ref = canonical_address(chain, address)
    ctx.disk_guard.require(INGEST_RESERVE_BYTES)
    if limit is not None and limit > ctx.settings.ingest.max_limit:
        limit = ctx.settings.ingest.max_limit
    if not ctx.ingest_lock.acquire(blocking=False):
        return error_response(
            409, "ingestion_busy", "Another ingestion run is in progress. Try again shortly."
        )
    try:
        run_id = ctx.ingestor.run(ref.chain, ref.address, limit)
    finally:
        ctx.ingest_lock.release()
    row = ctx.ingestor.store.get_run(run_id)
    assert row is not None  # noqa: S101
    return to_job(row)


@router.get("/ingestion/jobs/{job_id}", response_model=IngestionJob)
def get_job(ctx: Context, job_id: Annotated[int, Path(ge=1, le=2**53)]) -> IngestionJob:
    row = ctx.ingestor.store.get_run(job_id)
    if row is None:
        raise RecordNotFoundError(f"ingestion job {job_id} not found")
    return to_job(row)


@router.get("/ingestion/jobs", response_model=list[IngestionJob])
def list_jobs(
    ctx: Context,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    chain: Annotated[str | None, Query(max_length=32)] = None,
    address: Annotated[str | None, Query(max_length=100)] = None,
) -> list[IngestionJob]:
    if chain is not None and address is not None:
        ref = canonical_address(chain, address)
        chain, address = ref.chain, ref.address
    return [to_job(r) for r in ctx.ingestor.store.recent_runs(limit, chain, address)]


# ------------------------------------------------------------- checkpoints and coverage (v16)


class CheckpointOut(Out):
    chain: str
    network: str
    stream: str
    direction: str
    position_kind: str
    cursor: int | None
    cursor_hash: str | None
    boundary: int | None
    last_batch_from: int | None
    last_batch_to: int | None
    last_committed_at: datetime | None
    status: str
    last_error_category: str | None
    last_error_at: datetime | None
    last_providers: list[str]
    decoder_version: str
    head_as_of_last_run: int | None
    lag_as_of_last_run: int | None
    updated_at: datetime


def _checkpoint_out(cp: Checkpoint) -> CheckpointOut:
    return CheckpointOut(
        chain=cp.chain,
        network=cp.network,
        stream=cp.stream,
        direction=cp.direction,
        position_kind=cp.position_kind,
        cursor=cp.cursor,
        cursor_hash=cp.cursor_hash,
        boundary=cp.boundary,
        last_batch_from=cp.last_batch_from,
        last_batch_to=cp.last_batch_to,
        last_committed_at=cp.last_committed_at,
        status=cp.status,
        last_error_category=cp.last_error_category,
        last_error_at=cp.last_error_at,
        last_providers=cp.last_providers,
        decoder_version=cp.decoder_version,
        head_as_of_last_run=cp.head_seen,
        lag_as_of_last_run=cp.lag,
        updated_at=cp.updated_at,
    )


@router.get("/ingestion/checkpoints", response_model=list[CheckpointOut])
def list_checkpoints(
    ctx: Context, chain: Annotated[str | None, Query(max_length=32)] = None
) -> list[CheckpointOut]:
    """BIP-owned ingestion progress per chain, network, stream and direction (read-only)."""
    if chain is not None:
        _require_chain(ctx, chain)
    out: list[CheckpointOut] = []
    for cp in CheckpointStore(ctx.repo).all(chain):
        out.append(_checkpoint_out(cp))
    return out


class CoverageRangeOut(Out):
    from_pos: int
    to_pos: int
    status: str
    provider_id: str | None
    decoder_version: str
    attempts: int
    reason: str | None
    first_recorded_at: datetime
    last_recorded_at: datetime
    resolved_at: datetime | None


class CoverageOut(Out):
    chain: str
    network: str
    stream: str
    position_kind: str
    summary: dict[str, object]
    not_yet_processed: dict[str, int | None]
    historical: dict[str, object]
    gaps: list[CoverageRangeOut]
    ranges: list[CoverageRangeOut]
    meaning: dict[str, str]


_MEANING = {
    "processed": "read and handled under the stream's rules; may hold zero matches",
    "confirmed_empty": "the chain has no block here (for example a skipped Solana slot)",
    "incomplete": "read, but not everything was stored (a cap or an oversized response)",
    "failed": "given up after repeated failed reads",
    "skipped": "deliberately not read (for example the collector was too far behind)",
    "provider_unavailable": "no provider served this range",
    "not observed": "no row at all: BIP knows nothing about it; never treat as empty",
    "not yet processed": "beyond the live cursor",
    "resolved": "a gap that a later read fully processed; kept as history, not listed as a gap",
}


def _range(r: dict[str, object]) -> CoverageRangeOut:
    return CoverageRangeOut.model_validate({k: r[k] for k in CoverageRangeOut.model_fields})


@router.get("/ingestion/coverage/{chain}", response_model=CoverageOut)
def coverage(
    ctx: Context,
    chain: Annotated[str, Path(max_length=32)],
    stream: Annotated[str, Query(min_length=1, max_length=40)] = "collector",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> CoverageOut:
    """Coverage ranges and gaps for one stream, from storage only (read-only)."""
    _require_chain(ctx, chain)
    ref = StreamRef(chain, stream)
    store = CheckpointStore(ctx.repo)
    live = store.get(ref, "live")
    head = live.head_seen if live is not None else None
    hist = store.get(ref, "historical")
    after = live.cursor if live is not None else None
    return CoverageOut(
        chain=chain,
        network=ref.network,
        stream=stream,
        position_kind=ref.position_kind,
        summary=store.summary(chain, stream),
        not_yet_processed={"after": after, "through_head_as_of_last_run": head},
        historical={
            "processed_down_to": hist.cursor if hist is not None else None,
            "boundary": hist.boundary if hist is not None else None,
            "status": hist.status if hist is not None else "not started",
            "basis": (hist.recovery.get("boundary_basis") if hist is not None else None),
        },
        gaps=[
            _range(r)
            for r in store.ranges(
                chain, stream=stream, statuses=GAP_STATUSES, include_resolved=False, limit=limit
            )
        ],
        ranges=[_range(r) for r in store.ranges(chain, stream=stream, limit=limit)],
        meaning=_MEANING,
    )


# ------------------------------------------------------- backfill, recovery, pause (Stage 2)

_DECODERS = VERSIONS


def _run_bounded(ctx: WritableContext, job: Callable[[], dict[str, Any]]) -> Any:
    ctx.disk_guard.require(INGEST_RESERVE_BYTES)
    if not ctx.ingest_lock.acquire(blocking=False):
        return error_response(
            409, "ingestion_busy", "Another ingestion run is in progress. Try again shortly."
        )
    try:
        return job()
    finally:
        ctx.ingest_lock.release()


@router.post("/ingestion/backfill/{chain}", status_code=status.HTTP_201_CREATED)
def run_backfill(ctx: WritableContext, chain: Annotated[str, Path(max_length=32)]) -> Any:
    """Read one bounded window of history backward once. Admin only: this CONTACTS PROVIDERS.

    Bounded by positions, an RPC call budget and a deadline; waits (``deferred``) while live
    collection is running or behind; shares the ingestion lock.
    """
    _require_chain(ctx, chain)
    return _run_bounded(ctx, lambda: ctx.backfill.run(chain))


@router.post("/ingestion/recovery/{chain}", status_code=status.HTTP_201_CREATED)
def run_recovery(ctx: WritableContext, chain: Annotated[str, Path(max_length=32)]) -> Any:
    """Re-read a few positions of unresolved coverage gaps once. Admin only: CONTACTS PROVIDERS.

    Never moves a checkpoint; a successful re-read resolves the gap it covers.
    """
    _require_chain(ctx, chain)
    return _run_bounded(ctx, lambda: ctx.gap_recovery.run(chain))


@router.post("/ingestion/streams/{chain}/{direction}/pause", response_model=CheckpointOut)
def pause_stream(
    ctx: WritableContext,
    chain: Annotated[str, Path(max_length=32)],
    direction: Literal["live", "historical"],
) -> CheckpointOut:
    """Pause live or historical work of one chain (admin). The other direction keeps running and
    no cursor moves. Pausing ``historical`` also pauses gap recovery."""
    _require_chain(ctx, chain)
    cp = CheckpointStore(ctx.repo).pause(
        StreamRef(chain, "collector"), direction, decoder_version=_DECODERS[chain]
    )
    return _checkpoint_out(cp)


@router.post("/ingestion/streams/{chain}/{direction}/resume", response_model=CheckpointOut)
def resume_stream(
    ctx: WritableContext,
    chain: Annotated[str, Path(max_length=32)],
    direction: Literal["live", "historical"],
) -> CheckpointOut:
    """Resume a paused stream direction (admin)."""
    _require_chain(ctx, chain)
    cp = CheckpointStore(ctx.repo).resume(
        StreamRef(chain, "collector"), direction, decoder_version=_DECODERS[chain]
    )
    return _checkpoint_out(cp)
