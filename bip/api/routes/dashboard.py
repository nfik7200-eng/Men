from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from bip.api.deps import AppContext, Context
from bip.api.routes.chains import chain_statuses
from bip.api.routes.providers import provider_views, summarize
from bip.api.schemas import DashboardSummary, IngestionSummary
from bip.db import schema
from bip.db.types import utcnow

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/summary", response_model=DashboardSummary)
def get_summary(ctx: Context) -> DashboardSummary:
    counts = ctx.repo.counts()
    return DashboardSummary(
        generated_at=utcnow(),
        chains=tuple(chain_statuses(ctx)),
        recent_findings=tuple(ctx.repo.recent_findings(5)),
        recent_activity=tuple(ctx.repo.recent_activity(10)),
        providers=summarize(provider_views(ctx)),
        ingestion=_ingestion_summary(ctx),
        **counts,
    )


def _ingestion_summary(ctx: AppContext) -> IngestionSummary:
    by_status = ctx.ingestor.store.run_counts()
    with ctx.repo.transaction() as conn:
        bcs = int(
            conn.execute(select(func.count()).select_from(schema.balance_changes)).scalar_one()
        )
        blocks = int(conn.execute(select(func.count()).select_from(schema.blocks)).scalar_one())
    return IngestionSummary(
        runs_total=sum(by_status.values()),
        runs_by_status=by_status,
        balance_changes_stored=bcs,
        blocks_stored=blocks,
    )
