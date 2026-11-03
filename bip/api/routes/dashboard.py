from __future__ import annotations

from fastapi import APIRouter

from bip.api.deps import Context
from bip.api.routes.chains import chain_statuses
from bip.api.routes.providers import provider_rows, summarize
from bip.api.schemas import DashboardSummary
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
        providers=summarize(provider_rows(ctx)),
        **counts,
    )
