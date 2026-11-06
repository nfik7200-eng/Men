"""Acquisition coverage: what we know, and what we have not collected yet.

Definitions (each is computed from crawl_urls / listings, never from the number of discovered URLs alone):
  discovered      every product URL a sitemap/category page named
  rejected        can never be acquired as-is: gone (404/410 twice), disallowed (robots), retry_limit (failed MAX_FAILS times)
  duplicates      a second URL of a listing we already hold (last_outcome 'alias')
  non_product     fetched, but the page carries no schema.org Product (category/info page, soft block)
  eligible        discovered - rejected - duplicates - non_product
  acquired        eligible URLs that yielded a listing (a linked listing, or last_outcome ok/not_modified)
  fetched_ok      eligible pages that answered 200/304 (ok, not_modified, parse_error, ingest_invalid)
  eligible_coverage       acquired / eligible
  extraction_success_rate acquired / fetched_ok
A discovered URL is never an acquired product, and a fetched page is not a listing until extraction succeeded.
Rows written before mission 005 have no last_outcome: a linked listing counts as acquired; a bare fetch is 'unknown_outcome'.
"""
from datetime import datetime
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from .acquisition import CROSS_STRONG, MAX_FAILS
from .health import source_health
from .structured import normalise_url
from .models import CollectionRun, CrawlUrl, Listing, Source, utcnow

_FAILED = ("parse_error", "ingest_invalid", "fetch_error")


def duplicate_ids(s: Session, rows: list[CrawlUrl]) -> set[int]:
    """Rows that point at a listing ALREADY claimed by another row (legacy aliases). The canonical row is the one whose URL
    is the listing's own URL, else the lowest id; the rest are duplicates, not extra products."""
    by_listing: dict[int, list[CrawlUrl]] = {}
    for c in rows:
        if c.listing_id is not None and not c.gone:
            by_listing.setdefault(c.listing_id, []).append(c)
    dups: set[int] = set()
    for lid, group in by_listing.items():
        if len(group) < 2:
            continue
        l = s.get(Listing, lid)
        own = normalise_url(l.url) if l is not None else None
        keep = next((c for c in sorted(group, key=lambda c: c.id) if c.url == own), min(group, key=lambda c: c.id))
        dups |= {c.id for c in group if c.id != keep.id}
    return dups


def _classify(c: CrawlUrl, dups: frozenset[int] | set[int] = frozenset()) -> str:
    o = c.last_outcome
    if c.id in dups:
        return "duplicate"
    if c.gone or o == "gone":
        return "gone"
    if o == "disallowed":
        return "disallowed"
    if c.listing_id is None and c.last_fetched_at is None and (c.fail_count or 0) >= MAX_FAILS:
        return "retry_limit"
    if o == "alias":
        return "duplicate"
    if o == "no_product":
        return "non_product"
    if c.listing_id is not None or o in ("ok", "not_modified"):
        return "acquired"
    if o in ("parse_error", "ingest_invalid"):
        return o
    if o == "fetch_error":
        return "fetch_error"
    if c.last_fetched_at is not None:
        return "unknown_outcome"
    return "never_attempted"


def source_coverage(s: Session, src: Source, now: datetime | None = None) -> dict:
    now = now or utcnow()
    rows = list(s.scalars(select(CrawlUrl).where(CrawlUrl.source_id == src.id)))
    kinds: dict[str, int] = {}
    cls = {}
    dups = duplicate_ids(s, rows)
    for c in rows:
        cls[c.id] = k = _classify(c, dups)
        kinds[k] = kinds.get(k, 0) + 1
    n = lambda k: kinds.get(k, 0)
    rejected = {k: n(k) for k in ("gone", "disallowed", "retry_limit") if n(k)}
    eligible_rows = [c for c in rows if cls[c.id] not in ("gone", "disallowed", "retry_limit", "duplicate", "non_product")]
    eligible = len(eligible_rows)
    acquired = n("acquired")
    fetched_ok = acquired + n("parse_error") + n("ingest_invalid")
    never = [c for c in eligible_rows if cls[c.id] == "never_attempted"]
    waiting = [c for c in eligible_rows if cls[c.id] in ("never_attempted", "fetch_error")]
    with_counterpart = sum(1 for c in waiting if any(r.get("name") == "cross_merchant" and r.get("points", 0) >= CROSS_STRONG
                                                      for r in (c.priority_reasons or [])))
    runs = list(s.scalars(select(CollectionRun).where(CollectionRun.source_id == src.id, CollectionRun.finished_at.is_not(None))))
    pages, secs = sum(r.pages_fetched or 0 for r in runs), sum(r.duration_s or 0 for r in runs)
    ages = [(now - l.last_observed_at).total_seconds() / 3600 for l in s.scalars(
        select(Listing).where(Listing.source_id == src.id, Listing.active.is_(True), Listing.last_observed_at.is_not(None)))]
    h = source_health(s, src, now=now)
    return {
        "key": src.key, "name": src.name, "kind": src.kind,
        "discovered": len(rows), "rejected": rejected, "duplicates": n("duplicate"), "non_product": n("non_product"),
        "eligible": eligible, "acquired": acquired, "fetched_ok": fetched_ok,
        "failed": {k: n(k) for k in ("parse_error", "ingest_invalid", "fetch_error") if n(k)},
        "pending_retry": n("fetch_error"), "unknown_outcome": n("unknown_outcome"), "never_attempted": len(never),
        "deferred_budget": sum(1 for c in eligible_rows if c.selection_state == "deferred" and (c.selection_reason or "").startswith("budget")),
        "unfetched_with_counterpart": with_counterpart,
        "eligible_coverage": (acquired / eligible) if eligible else None,
        "extraction_success_rate": (acquired / fetched_ok) if fetched_ok else None,
        "last_fetch_at": max((c.last_fetched_at for c in rows if c.last_fetched_at), default=None),
        "last_attempt_at": max((r.started_at for r in runs), default=None),
        "avg_seconds_per_page": (secs / pages) if pages else None,  # wall time per page INCLUDING the politeness delay
        "freshness": {"listings": len(ages), "median_age_hours": median(ages) if ages else None,
                      "max_age_hours": max(ages) if ages else None, "stale": sum(1 for a in ages if a > src.stale_after_hours)},
        "health": h.state,
    }


def next_up(s: Session, src: Source, limit: int = 8) -> list[dict]:
    """The best never-acquired URLs by recorded priority: what the next collection would fetch first (and why)."""
    rows = [c for c in s.scalars(select(CrawlUrl).where(CrawlUrl.source_id == src.id)) if _classify(c) in ("never_attempted", "fetch_error")]
    rows.sort(key=lambda c: (-(c.priority_score or 0), c.url))
    return [{"url": c.url, "score": c.priority_score, "state": c.selection_state, "reason": c.selection_reason,
             "why": [f"{r['name']} {r['points']:g}: {r['why']}" for r in (c.priority_reasons or []) if r.get("points")]} for c in rows[:limit]]


def coverage_report(s: Session, now: datetime | None = None, with_next_up: bool = False) -> dict:
    """Live sources only: demo/sandbox data never counts toward real coverage."""
    now = now or utcnow()
    srcs = list(s.scalars(select(Source).where(Source.kind == "live").order_by(Source.key)))
    listings = list(s.scalars(select(Listing).join(Source, Source.id == Listing.source_id).where(Source.kind == "live", Listing.active.is_(True))))
    by_product: dict[int, set[int]] = {}
    for l in listings:
        if l.product_id is not None:
            by_product.setdefault(l.product_id, set()).add(l.source_id)
    return {"data_class": "live", "live_merchants": len({l.source_id for l in listings}), "live_listings": len(listings),
            "cross_merchant_products": sum(1 for v in by_product.values() if len(v) >= 2),
            "sources": [{**source_coverage(s, x, now), **({"next_up": next_up(s, x)} if with_next_up else {})} for x in srcs]}
