"""Validate -> upsert -> observe -> detect changes. The only place observations are written."""
import hashlib
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .matching import attach_or_queue
from .models import CollectionRun, Event, Listing, Observation, Product, Source
from .sources.base import RawListing


def validate(raw: RawListing) -> tuple[RawListing | None, list[str]]:
    """Returns (cleaned listing or None if unusable, problems). A non-positive price becomes None
    (unavailable) — it must never be stored as a zero price."""
    problems = []
    if not raw.external_id or not raw.title or not raw.title.strip():
        return None, ["missing id/title"]
    if not raw.url:
        return None, ["missing url"]
    if not (raw.currency and len(raw.currency) == 3):
        return None, ["bad currency"]
    if raw.price_minor is not None and raw.price_minor <= 0:
        problems.append(f"non-positive price {raw.price_minor} treated as unavailable")
        raw.price_minor = None
    if raw.original_price_minor is not None and (raw.original_price_minor <= 0 or
                                                 (raw.price_minor and raw.original_price_minor < raw.price_minor)):
        problems.append("implausible original price dropped")
        raw.original_price_minor = None
    if raw.availability not in ("in_stock", "out_of_stock", "unknown"):
        raw.availability = "unknown"
    return raw, problems


def _hash(text: str) -> str:
    return hashlib.sha1(" ".join(text.lower().split()).encode()).hexdigest()


def _event(s, listing, run, kind, old=None, new=None, pct=None, now=None):
    e = Event(listing_id=listing.id, run_id=run.id if run else None, kind=kind,
              old_value=None if old is None else str(old), new_value=None if new is None else str(new),
              pct=pct, detected_at=now)
    s.add(e)
    return e


def ingest_listing(s: Session, source: Source, run: CollectionRun | None, raw: RawListing,
                   now: datetime) -> tuple[Listing, bool, list[str]]:
    raw, problems = validate(raw)
    if raw is None:
        return None, False, problems  # type: ignore[return-value]
    listing = s.scalar(select(Listing).where(Listing.source_id == source.id, Listing.external_id == raw.external_id))
    dh = _hash(raw.description)
    is_new = listing is None
    if is_new:
        product = Product(title=raw.title, brand=raw.brand, category=raw.category, created_at=now)
        s.add(product)
        s.flush()
        listing = Listing(
            source_id=source.id, external_id=raw.external_id, product_id=product.id, url=raw.url, title=raw.title,
            brand=raw.brand, category=raw.category, description=raw.description, image_url=raw.image_url,
            specs=raw.specs, identifiers=raw.identifiers, rating=raw.rating, review_count=raw.review_count,
            desc_hash=dh, first_seen_at=now, last_seen_at=now, currency=raw.currency)
        s.add(listing)
        s.flush()
        _event(s, listing, run, "new_listing", new=raw.title, now=now)
    else:
        if not listing.active:
            listing.active = True
            _event(s, listing, run, "reappeared", now=now)
        old_p, new_p = listing.price_minor, raw.price_minor
        if listing.currency == raw.currency:
            if old_p is not None and new_p is not None and old_p != new_p:
                pct = (new_p - old_p) / old_p * 100
                _event(s, listing, run, "price_drop" if new_p < old_p else "price_rise", old_p, new_p, round(pct, 2), now)
            elif old_p is not None and new_p is None:
                _event(s, listing, run, "price_unavailable", old_p, None, now=now)
        old_a, new_a = listing.availability, raw.availability
        if old_a == "out_of_stock" and new_a == "in_stock":
            _event(s, listing, run, "restock", old_a, new_a, now=now)
        elif old_a == "in_stock" and new_a == "out_of_stock":
            _event(s, listing, run, "out_of_stock", old_a, new_a, now=now)
        if listing.desc_hash != dh and (listing.description or raw.description):
            _event(s, listing, run, "description_change", now=now)
        listing.title, listing.url, listing.description, listing.desc_hash = raw.title, raw.url, raw.description, dh
        listing.brand, listing.category = raw.brand or listing.brand, raw.category or listing.category
        listing.image_url, listing.specs = raw.image_url or listing.image_url, raw.specs or listing.specs
        listing.rating, listing.review_count = raw.rating, raw.review_count
        listing.currency = raw.currency
        listing.last_seen_at = now

    s.add(Observation(listing_id=listing.id, run_id=run.id if run else None, observed_at=now,
                      price_minor=raw.price_minor, original_price_minor=raw.original_price_minor,
                      currency=raw.currency, availability=raw.availability, shipping_minor=raw.shipping_minor))
    listing.price_minor, listing.original_price_minor = raw.price_minor, raw.original_price_minor
    listing.availability, listing.last_observed_at = raw.availability, now
    if is_new:
        attach_or_queue(s, listing)
    return listing, is_new, problems


def mark_removed(s: Session, source: Source, run: CollectionRun, now: datetime) -> int:
    """Only call after a COMPLETE crawl; a capped/failed crawl proves nothing about absence."""
    gone = s.scalars(select(Listing).where(Listing.source_id == source.id, Listing.active.is_(True),
                                           Listing.last_seen_at < run.started_at)).all()
    for l in gone:
        l.active = False
        _event(s, l, run, "removed", now=now)
    return len(gone)
