from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from bip.api.deps import INGEST_RESERVE_BYTES, Context, WritableContext
from bip.api.errors import error_response
from bip.api.routes.chains import _require_chain  # pyright: ignore[reportPrivateUsage]
from bip.api.schemas import Out
from bip.db.errors import RecordNotFoundError
from bip.db.repository import canonical_address

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
