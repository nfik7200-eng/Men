"""Data-quality labelling: every figure is verified / estimated / missing / stale."""
from datetime import datetime, timedelta

from .models import Listing, Source, utcnow


def freshness(listing: Listing, source: Source, now: datetime | None = None) -> str:
    now = now or utcnow()
    if listing.last_observed_at is None:
        return "unknown"
    return "stale" if now - listing.last_observed_at > timedelta(hours=source.stale_after_hours) else "fresh"


def price_status(listing: Listing, source: Source, now: datetime | None = None) -> str:
    """'missing' | 'stale' | 'verified'. A price is 'verified' = observed on the source page, recently."""
    if listing.price_minor is None:
        return "missing"
    return "stale" if freshness(listing, source, now) != "fresh" else "verified"
