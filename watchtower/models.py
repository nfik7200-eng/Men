"""Watchtower data model.

Money is stored as integer minor units + ISO currency. A missing price is NULL, never 0.
Observations are append-only; Listing price columns are a denormalised cache of the latest one.
"""
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint, Index)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC everywhere


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(String(300))
    country: Mapped[str | None] = mapped_column(String(2))
    currency: Mapped[str] = mapped_column(String(3))
    kind: Mapped[str] = mapped_column(String(16), default="demo")  # demo | sandbox | live (live must be explicit)
    terms_note: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    stale_after_hours: Mapped[int] = mapped_column(Integer, default=72)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_status: Mapped[str | None] = mapped_column(String(16))
    last_error: Mapped[str | None] = mapped_column(Text)


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|ok|partial|failed|blocked
    complete: Mapped[bool] = mapped_column(Boolean, default=False)  # whole catalogue crawled?
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_invalid: Mapped[int] = mapped_column(Integer, default=0)
    price_changes: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    duration_s: Mapped[float | None] = mapped_column(Float)
    bytes_fetched: Mapped[int | None] = mapped_column(Integer)
    pages_unchanged: Mapped[int | None] = mapped_column(Integer)  # answered 304 / identical offer
    pages_failed: Mapped[int | None] = mapped_column(Integer)
    extraction: Mapped[dict | None] = mapped_column(JSON)  # counts by method: jsonld|microdata|css|not_modified|failed
    source: Mapped[Source] = relationship()


class Product(Base):
    """A canonical product that one or more listings (across stores) refer to."""
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    brand: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    listings: Mapped[list["Listing"]] = relationship(back_populates="product")


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source_id", "external_id"), Index("ix_listing_cat", "category"))
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str] = mapped_column(String(200))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    url: Mapped[str] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(String(500))
    brand: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str | None] = mapped_column(String(1000))
    specs: Mapped[dict] = mapped_column(JSON, default=dict)  # parsed from source text
    identifiers: Mapped[dict] = mapped_column(JSON, default=dict)  # gtin/upc/mpn when source states them
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    desc_hash: Mapped[str | None] = mapped_column(String(40))
    sku: Mapped[str | None] = mapped_column(String(64), index=True)  # MERCHANT-internal id; never a matching identifier
    mpn: Mapped[str | None] = mapped_column(String(64), index=True)  # manufacturer part number, only if distinct from sku
    gtin: Mapped[str | None] = mapped_column(String(14), index=True)  # validated, zero-padded to 14
    condition: Mapped[str | None] = mapped_column(String(20))  # new | used | refurbished (source-stated) or NULL
    ships_to: Mapped[list | None] = mapped_column(JSON)  # ISO country codes the seller ships to; NULL = unknown
    location_country: Mapped[str | None] = mapped_column(String(2))  # item location, ISO code; NULL = unknown
    shipping_minor: Mapped[int | None] = mapped_column(Integer)  # cheapest quoted shipping to the quote country; NULL = unknown
    affiliate_url: Mapped[str | None] = mapped_column(String(1500))  # provider-issued tracked URL, if any
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # cache of the latest observation
    price_minor: Mapped[int | None] = mapped_column(Integer)
    original_price_minor: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    availability: Mapped[str] = mapped_column(String(16), default="unknown")
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # matching
    match_status: Mapped[str] = mapped_column(String(16), default="self")  # self|auto|review|confirmed
    match_confidence: Mapped[float | None] = mapped_column(Float)
    match_reasons: Mapped[list] = mapped_column(JSON, default=list)

    source: Mapped[Source] = relationship()
    product: Mapped[Product | None] = relationship(back_populates="listings")
    observations: Mapped[list["Observation"]] = relationship(
        back_populates="listing", order_by="Observation.observed_at")


class Observation(Base):
    __tablename__ = "observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("collection_runs.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    price_minor: Mapped[int | None] = mapped_column(Integer)  # NULL = unavailable (never 0)
    original_price_minor: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    availability: Mapped[str] = mapped_column(String(16), default="unknown")
    shipping_minor: Mapped[int | None] = mapped_column(Integer)  # tracked separately; NULL = unknown
    extraction_method: Mapped[str | None] = mapped_column(String(16))  # jsonld | microdata | css | not_modified
    content_hash: Mapped[str | None] = mapped_column(String(40))  # hash of the extracted offer tuple (not raw HTML)
    http_status: Mapped[int | None] = mapped_column(Integer)
    listing: Mapped[Listing] = relationship(back_populates="observations")


class Event(Base):
    """Change feed. Shared by consumer alerts and (later) competitor-monitoring reports."""
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("collection_runs.id"))
    kind: Mapped[str] = mapped_column(String(24))  # new_listing|price_drop|price_rise|restock|out_of_stock|removed|reappeared|description_change|price_unavailable
    old_value: Mapped[str | None] = mapped_column(String(100))
    new_value: Mapped[str | None] = mapped_column(String(100))
    pct: Mapped[float | None] = mapped_column(Float)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    listing: Mapped[Listing] = relationship()


class MatchCandidate(Base):
    """Uncertain cross-listing match awaiting human review. Never auto-merged."""
    __tablename__ = "match_candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    score: Mapped[float] = mapped_column(Float)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|accepted|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    listing: Mapped[Listing] = relationship()
    product: Mapped[Product] = relationship()


class WatchItem(Base):
    __tablename__ = "watch_items"
    __table_args__ = (UniqueConstraint("owner", "listing_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String(100), default="local")
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    target_price_minor: Mapped[int | None] = mapped_column(Integer)
    notify_restock: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    baseline_price_minor: Mapped[int | None] = mapped_column(Integer)
    baseline_availability: Mapped[str | None] = mapped_column(String(16))
    baseline_currency: Mapped[str | None] = mapped_column(String(3))
    listing: Mapped[Listing] = relationship()


class AffiliateClick(Base):
    __tablename__ = "affiliate_clicks"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    network: Mapped[str | None] = mapped_column(String(50))  # None = plain outbound link, no affiliate
    clicked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FxRate(Base):
    """A sourced exchange rate. We never invent or live-fetch rates: every rate carries who said so and when."""
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("base", "quote", "as_of", "source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    base: Mapped[str] = mapped_column(String(3))
    quote: Mapped[str] = mapped_column(String(3))
    rate: Mapped[str] = mapped_column(String(32))  # Decimal as string: 1 base = rate quote
    as_of: Mapped[datetime] = mapped_column(DateTime)  # the date the rate is valid for
    source: Mapped[str] = mapped_column(String(200))  # e.g. "ECB euro foreign exchange reference rates"
    source_url: Mapped[str | None] = mapped_column(String(500))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SourcePolicy(Base):
    """Compliance record per source. Collection of non-demo sources requires status='approved'.
    'approved' means "no prohibition found at review time", NOT "legally permitted" (see docs/SOURCE_DISCOVERY.md)."""
    __tablename__ = "source_policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), unique=True)
    status: Mapped[str] = mapped_column(String(12), default="unverified")  # approved | unverified | rejected
    robots_sha: Mapped[str | None] = mapped_column(String(64))  # sha256 of robots.txt at review time
    required_paths: Mapped[list | None] = mapped_column(JSON)  # URL path prefixes we need; re-checked against robots every run
    tos_url: Mapped[str | None] = mapped_column(String(500))
    tos_sha: Mapped[str | None] = mapped_column(String(64))
    tos_finding: Mapped[str | None] = mapped_column(Text)  # what the terms say about automated access/reuse
    crawl_delay_s: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    review_due_at: Mapped[datetime | None] = mapped_column(DateTime)
    source: Mapped[Source] = relationship()


class CrawlUrl(Base):
    """Per-URL incremental-crawl state for product-page sources."""
    __tablename__ = "crawl_urls"
    __table_args__ = (UniqueConstraint("source_id", "url"), Index("ix_crawl_due", "source_id", "next_check_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    url: Mapped[str] = mapped_column(String(1000))
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"))
    etag: Mapped[str | None] = mapped_column(String(200))
    last_modified: Mapped[str | None] = mapped_column(String(100))
    lastmod_seen: Mapped[str | None] = mapped_column(String(40))  # sitemap <lastmod>
    content_hash: Mapped[str | None] = mapped_column(String(40))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_status: Mapped[int | None] = mapped_column(Integer)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime)
    gone: Mapped[bool] = mapped_column(Boolean, default=False)  # 404/410 on its own URL
    # what happened the last time we tried this URL (acquisition intelligence): ok | not_modified | no_product | parse_error |
    # ingest_invalid | alias | disallowed | fetch_error | gone | blocked. Discovery is not acquisition: only `ok`/`not_modified` count.
    last_outcome: Mapped[str | None] = mapped_column(String(24))
    last_outcome_detail: Mapped[str | None] = mapped_column(String(200))
