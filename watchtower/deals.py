"""Explainable deal scoring (0-100). Every point is traceable to a component with an evidence level.

What counts as evidence (audit-hardened, see docs/AUDIT_REPORT.md):
  * Observations less than MIN_OBS_GAP_HOURS apart are ONE observation (bursts / duplicates are not history).
  * history_drop (<=40, verified): needs >=MIN_HISTORY_OBS collections spanning >=MIN_HISTORY_SPAN_DAYS days, AND
      - the current price must be LOWER than every price seen before the current price began (so a spike that
        merely returns to normal is not a "drop"), AND
      - the reference ("usual") price is the price that held longest before the change, and it must have held
        for >=MIN_REFERENCE_DAYS days.
  * markdown (<=20): a claimed original price is credited only if it matches that observed reference price
      (within 5%) which held >=7 days; otherwise it is flagged suspicious / unverified and earns nothing.
  * peer_value (<=40, estimated): vs median of comparable listings; never counts as verification.
  * Stale listings are labelled "Stale data" and excluded from rankings.
"""
from bisect import bisect_left
from statistics import median
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from . import config
from .models import Listing, Observation, Source, utcnow


def data_class(kind: str | None) -> str:
    """Peers/ranks are only compared within the same class: real retailer data never mixes with demo/sandbox data."""
    return "live" if kind == "live" else "test"


@dataclass
class Component:
    name: str
    points: float
    max_points: float
    evidence: str  # verified | estimated | unverified | none
    detail: str


@dataclass
class DealScore:
    listing_id: int
    score: float | None
    label: str
    confidence: str  # none | low | medium | high
    components: list[Component] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    n_obs: int = 0


class PeerContext:
    """Sorted price lists by (category, currency[, RAM]) built once; per-listing peer median is O(log N)
    (the listing's own price is excluded without rebuilding anything)."""

    def __init__(self, s: Session):
        rows = s.execute(select(Listing.id, Listing.category, Listing.currency, Listing.price_minor, Listing.specs, Source.kind)
                         .join(Source, Source.id == Listing.source_id)
                         .where(Listing.active.is_(True), Listing.price_minor.is_not(None))).all()
        self.group: dict[tuple, list[int]] = {}
        self.ram_group: dict[tuple, list[int]] = {}
        for _, cat, cur, price, specs, kind in rows:
            dc = data_class(kind)
            self.group.setdefault((dc, cat, cur), []).append(price)
            ram = (specs or {}).get("ram_gb")
            if ram is not None:
                self.ram_group.setdefault((dc, cat, cur, ram), []).append(price)
        for d in (self.group, self.ram_group):
            for v in d.values():
                v.sort()

    @staticmethod
    def _median_without(vals: list[int], own: int | None) -> tuple[int, float | None]:
        """(count, median) of the sorted list with one occurrence of `own` removed."""
        n, pos = len(vals), None
        if own is not None:
            i = bisect_left(vals, own)
            if i < n and vals[i] == own:
                pos, n = i, n - 1
        if n == 0:
            return 0, None
        at = lambda k: vals[k] if pos is None or k < pos else vals[k + 1]
        return n, (at(n // 2) if n % 2 else (at(n // 2 - 1) + at(n // 2)) / 2)

    def peers(self, l: Listing) -> tuple[int, float | None, str]:
        own = l.price_minor if l.active else None
        ram = (l.specs or {}).get("ram_gb")
        dc = data_class(l.source.kind if l.source is not None else None)
        if ram is not None:
            n, med = self._median_without(self.ram_group.get((dc, l.category, l.currency, ram), []), own)
            if n >= config.MIN_PEERS:
                return n, med, f"{n} {l.category or 'similar'} listings with {ram}GB RAM"
        n, med = self._median_without(self.group.get((dc, l.category, l.currency), []), own)
        return n, med, f"{n} {l.category or 'similar'} listings"


class HistoryContext:
    """Bulk-loaded observation series (one query) for many listings."""

    def __init__(self, s: Session, listing_ids_stmt):
        self.by_listing: dict[int, list[Observation]] = {}
        q = select(Observation).where(Observation.listing_id.in_(listing_ids_stmt), Observation.price_minor.is_not(None)) \
            .order_by(Observation.listing_id, Observation.observed_at)
        for o in s.scalars(q):
            self.by_listing.setdefault(o.listing_id, []).append(o)


def collapse(hist: list[Observation], gap_hours: float | None = None) -> list[Observation]:
    """Observations closer than the gap are one collection: keep the latest of each burst."""
    gap = timedelta(hours=config.MIN_OBS_GAP_HOURS if gap_hours is None else gap_hours)
    out: list[Observation] = []
    for o in hist:
        if out and o.observed_at - out[-1].observed_at < gap:
            out[-1] = o
        else:
            out.append(o)
    return out


def priced_history(s: Session, listing: Listing) -> list[Observation]:
    return [o for o in s.scalars(select(Observation).where(Observation.listing_id == listing.id, Observation.price_minor.is_not(None))
                                 .order_by(Observation.observed_at)).all() if o.currency == listing.currency]


def _reference(hist: list[Observation]):
    """Split history at the start of the CURRENT price. Returns (prior_obs, ref_price, ref_days, prior_min)."""
    i = len(hist) - 1
    while i > 0 and hist[i - 1].price_minor == hist[-1].price_minor:
        i -= 1
    prior = hist[:i]
    if not prior:
        span = (hist[-1].observed_at - hist[0].observed_at).total_seconds() / 86400
        return [], hist[-1].price_minor, span, None
    dur: dict[int, float] = {}
    for k, o in enumerate(prior):
        end = prior[k + 1].observed_at if k + 1 < len(prior) else hist[i].observed_at
        dur[o.price_minor] = dur.get(o.price_minor, 0.0) + (end - o.observed_at).total_seconds() / 86400
    ref_price = max(dur, key=dur.get)
    return prior, ref_price, dur[ref_price], min(o.price_minor for o in prior)


def is_stale(listing: Listing, now: datetime) -> bool:
    if listing.last_observed_at is None:
        return True
    hours = listing.source.stale_after_hours if listing.source is not None else 72
    return now - listing.last_observed_at > timedelta(hours=hours)


def score_listing(s: Session, listing: Listing, ctx: PeerContext | None = None, now: datetime | None = None,
                  hist_ctx: HistoryContext | None = None) -> DealScore:
    now = now or utcnow()
    ds = DealScore(listing.id, None, "No price", "none")
    if listing.price_minor is None:
        ds.warnings.append("Price unavailable at last collection — not treated as zero.")
        return ds
    ctx = ctx or PeerContext(s)
    raw_hist = hist_ctx.by_listing.get(listing.id, []) if hist_ctx else priced_history(s, listing)
    hist = collapse([o for o in raw_hist if o.currency == listing.currency])
    ds.n_obs = len(hist)
    price = listing.price_minor
    comps = ds.components

    span_days = (hist[-1].observed_at - hist[0].observed_at).total_seconds() / 86400 if len(hist) > 1 else 0
    has_history = len(hist) >= config.MIN_HISTORY_OBS and span_days >= config.MIN_HISTORY_SPAN_DAYS
    prior, ref_price, ref_days, prior_min = _reference(hist) if has_history else ([], None, 0, None)

    # 1. own history
    if not has_history:
        comps.append(Component("history_drop", 0, 40, "none",
                               f"only {len(hist)} distinct collection(s) over {span_days:.1f} day(s); need "
                               f"{config.MIN_HISTORY_OBS}+ over {config.MIN_HISTORY_SPAN_DAYS}+ days"))
        ds.warnings.append("Not enough price history to confirm any discount is genuine.")
    elif not prior:
        comps.append(Component("history_drop", 0, 40, "none", f"price unchanged over {span_days:.0f} days — no drop"))
    elif not price < prior_min:
        comps.append(Component("history_drop", 0, 40, "none",
                               f"not lower than the lowest earlier price ({prior_min/100:.2f}); returning to a usual price is not a drop"))
    elif ref_days < config.MIN_REFERENCE_DAYS:
        comps.append(Component("history_drop", 0, 40, "none",
                               f"earlier reference price only held {ref_days:.1f} days (need {config.MIN_REFERENCE_DAYS}+)"))
    else:
        pts = max(0.0, min(1.0, (1 - price / ref_price) / 0.25)) * 40
        comps.append(Component("history_drop", round(pts, 1), 40, "verified",
                               f"now {price/100:.2f}, below every earlier price (lowest {prior_min/100:.2f}); usual price "
                               f"{ref_price/100:.2f} held {ref_days:.0f} days"))

    # 2. peers
    n_peers, med, basis = ctx.peers(listing)
    if n_peers >= config.MIN_PEERS:
        pts = max(0.0, min(1.0, (1 - price / med) / 0.4)) * 40
        rel = (price / med - 1) * 100
        comps.append(Component("peer_value", round(pts, 1), 40, "estimated",
                               f"{abs(rel):.0f}% {'below' if rel < 0 else 'above'} the median ({med/100:.2f}) of {basis}"))
    else:
        comps.append(Component("peer_value", 0, 40, "none", f"only {n_peers} comparable listing(s); need {config.MIN_PEERS}+"))

    # 3. markdown claim
    orig = listing.original_price_minor
    if orig and orig > price:
        claimed = (orig - price) / orig
        if has_history and ref_price and abs(orig - ref_price) <= 0.05 * orig and ref_days >= 7:
            comps.append(Component("markdown", round(min(1, claimed / 0.4) * 20, 1), 20, "verified",
                                   f"claimed {claimed*100:.0f}% off; the usual price {ref_price/100:.2f} was observed for {ref_days:.0f} days"))
        elif has_history:
            comps.append(Component("markdown", 0, 20, "none",
                                   f"claims {claimed*100:.0f}% off ({orig/100:.2f}) but the price that actually held was {ref_price/100:.2f}"))
            ds.warnings.append("Suspicious original price: not the price this item usually sold at in our history.")
        else:
            comps.append(Component("markdown", round(min(5, claimed * 20), 1), 20, "unverified",
                                   f"claims {claimed*100:.0f}% off; cannot verify without history (credit capped)"))
    else:
        comps.append(Component("markdown", 0, 20, "none", "no original/list price stated"))

    ds.score = round(sum(c.points for c in comps), 1)
    hist_pts, peer_pts = comps[0].points, comps[1].points
    ds.confidence = "high" if has_history else ("medium" if n_peers >= config.MIN_PEERS else "low")
    if is_stale(listing, now):
        ds.label, ds.confidence = "Stale data", "low"
        ds.warnings.append(f"Last observed {listing.last_observed_at:%Y-%m-%d}: older than the source's freshness window.")
    elif listing.availability == "out_of_stock":
        ds.label = "Out of stock"
    elif has_history and hist_pts >= 10 and ds.score >= 40:
        ds.label = "Verified price drop"
    elif peer_pts >= 15:
        ds.label = "Below typical price (estimate)"
    else:
        ds.label = "Typical price" if n_peers >= config.MIN_PEERS else "Insufficient data"
    return ds


def top_deals(s: Session, limit: int = 12, category: str | None = None, now: datetime | None = None,
              kinds: tuple[str, ...] | None = ("live",)) -> list[tuple[Listing, DealScore]]:
    """Commercial deal ranking: REAL (live) sources only by default. Pass kinds=None to include demo/sandbox data."""
    now = now or utcnow()
    q = select(Listing).options(joinedload(Listing.source)).join(Source, Source.id == Listing.source_id).where(
        Listing.active.is_(True), Listing.price_minor.is_not(None), Listing.availability.notin_(("out_of_stock", "backorder", "preorder")))
    if kinds is not None:
        q = q.where(Source.kind.in_(kinds))
    if category:
        q = q.where(Listing.category == category)
    cands = [l for l in s.scalars(q).unique() if not is_stale(l, now)]
    ctx = PeerContext(s)
    hist_ctx = HistoryContext(s, select(Listing.id).where(Listing.id.in_([l.id for l in cands]))) if len(cands) <= 800 \
        else HistoryContext(s, select(Listing.id).where(Listing.active.is_(True)))
    scored = [(l, score_listing(s, l, ctx, now, hist_ctx)) for l in cands]
    scored = [x for x in scored if x[1].score is not None and x[1].score > 0]
    scored.sort(key=lambda x: x[1].score, reverse=True)
    return scored[:limit]


@dataclass
class PriceStats:
    n_collections: int
    currency: str | None
    first_at: datetime | None = None
    last_at: datetime | None = None
    span_days: float = 0.0
    minimum: int | None = None
    maximum: int | None = None
    mean: float | None = None
    median: float | None = None
    volatility_pct: float | None = None  # coefficient of variation (stdev / mean), percent; needs >=3 collections
    current_vs_min_pct: float | None = None
    current_vs_median_pct: float | None = None
    changes: int = 0  # number of price changes between consecutive collections
    note: str = ""


def price_stats(s: Session, listing: Listing, hist_ctx: HistoryContext | None = None) -> PriceStats:
    """Historical min/max/mean/median/volatility over distinct collections (same currency only). Stats from fewer than
    3 collections are shown but labelled, never presented as evidence."""
    raw = hist_ctx.by_listing.get(listing.id, []) if hist_ctx else priced_history(s, listing)
    hist = collapse([o for o in raw if o.currency == listing.currency])
    st = PriceStats(len(hist), listing.currency)
    if not hist:
        st.note = "no priced observations yet"
        return st
    prices = [o.price_minor for o in hist]
    st.first_at, st.last_at = hist[0].observed_at, hist[-1].observed_at
    st.span_days = (st.last_at - st.first_at).total_seconds() / 86400
    st.minimum, st.maximum, st.mean, st.median = min(prices), max(prices), sum(prices) / len(prices), median(prices)
    st.changes = sum(1 for a, b in zip(prices, prices[1:]) if a != b)
    cur = listing.price_minor if listing.price_minor is not None else prices[-1]
    st.current_vs_min_pct = round((cur / st.minimum - 1) * 100, 1)
    st.current_vs_median_pct = round((cur / st.median - 1) * 100, 1)
    if len(prices) >= 3 and st.mean:
        mu = st.mean
        st.volatility_pct = round((sum((p - mu) ** 2 for p in prices) / (len(prices) - 1)) ** 0.5 / mu * 100, 2)
    if len(prices) < config.MIN_HISTORY_OBS or st.span_days < config.MIN_HISTORY_SPAN_DAYS:
        st.note = (f"only {len(prices)} collection(s) over {st.span_days:.1f} day(s): statistics are indicative, "
                   f"not evidence (need {config.MIN_HISTORY_OBS}+ over {config.MIN_HISTORY_SPAN_DAYS}+ days)")
    return st
