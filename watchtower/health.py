"""Collection health per source: success rate, throughput, error pattern, freshness. Feeds the admin page and
(later) alerting; shared by consumer and business-monitoring products."""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import CollectionRun, Listing, Observation, Source, utcnow


@dataclass
class SourceHealth:
    key: str
    kind: str
    runs: int
    ok_rate: float | None  # share of the last `window` runs that finished ok/partial
    failed: int
    blocked: int
    avg_duration_s: float | None
    pages_per_min: float | None
    items_per_run: float | None
    invalid_rate: float | None
    hours_since_success: float | None
    listings: int
    observations: int
    state: str  # healthy | degraded | stale | blocked | never-run
    last_error: str | None


def source_health(s: Session, src: Source, window: int = 10, now: datetime | None = None) -> SourceHealth:
    now = now or utcnow()
    runs = s.scalars(select(CollectionRun).where(CollectionRun.source_id == src.id, CollectionRun.finished_at.is_not(None))
                     .order_by(CollectionRun.id.desc()).limit(window)).all()
    n = len(runs)
    ok = sum(1 for r in runs if r.status in ("ok", "partial"))
    dur = [r.duration_s for r in runs if r.duration_s]
    pages = sum(r.pages_fetched for r in runs)
    seen = sum(r.items_seen for r in runs)
    since = (now - src.last_success_at).total_seconds() / 3600 if src.last_success_at else None
    state = "never-run" if not n else ("blocked" if runs[0].status == "blocked" else
            ("stale" if since is None or since > src.stale_after_hours else ("degraded" if ok / n < 0.8 else "healthy")))
    return SourceHealth(
        src.key, src.kind, n, (ok / n if n else None), sum(1 for r in runs if r.status == "failed"),
        sum(1 for r in runs if r.status == "blocked"), (sum(dur) / len(dur) if dur else None),
        (pages / (sum(dur) / 60) if dur and pages else None), (seen / n if n else None),
        (sum(r.items_invalid for r in runs) / seen if seen else None), since,
        s.scalar(select(func.count()).select_from(Listing).where(Listing.source_id == src.id)) or 0,
        s.scalar(select(func.count()).select_from(Observation).join(Listing, Listing.id == Observation.listing_id)
                 .where(Listing.source_id == src.id)) or 0,
        state, src.last_error)
