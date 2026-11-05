"""Explainable deal scoring (0-100). Every point is traceable to a component with an evidence level.

Components
  history_drop (<=40, verified)  latest price vs median of earlier observed prices; needs >=MIN_HISTORY_OBS
                                 observations spanning >=MIN_HISTORY_SPAN_DAYS days, else 0 + warning
  peer_value   (<=40, estimated) price vs median of comparable listings (same category/currency/RAM)
  markdown     (<=20)            claimed original price; credited fully ONLY if history shows that price
                                 really existed; capped at 5 and flagged "unverified" without history
A "Verified deal" label requires history evidence. Without it the best label is an *estimate*.
"""
from dataclasses import dataclass, field
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import Listing, Observation


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
    """Caches active priced listings by (category, currency) so ranking many listings is one query."""

    def __init__(self, s: Session):
        rows = s.execute(select(Listing.id, Listing.category, Listing.currency, Listing.price_minor, Listing.specs)
                         .where(Listing.active.is_(True), Listing.price_minor.is_not(None))).all()
        self.by_group: dict[tuple, list[tuple[int, int, int | None]]] = {}
        for i, cat, cur, price, specs in rows:
            self.by_group.setdefault((cat, cur), []).append((i, price, (specs or {}).get("ram_gb")))

    def peers(self, l: Listing) -> tuple[list[int], str]:
        group = [(i, p, r) for i, p, r in self.by_group.get((l.category, l.currency), []) if i != l.id]
        ram = (l.specs or {}).get("ram_gb")
        if ram is not None:
            same_ram = [p for _, p, r in group if r == ram]
            if len(same_ram) >= config.MIN_PEERS:
                return same_ram, f"{len(same_ram)} {l.category or 'similar'} listings with {ram}GB RAM"
        prices = [p for _, p, _ in group]
        return prices, f"{len(prices)} {l.category or 'similar'} listings"


def priced_history(s: Session, listing: Listing) -> list[Observation]:
    return [o for o in s.scalars(select(Observation).where(Observation.listing_id == listing.id)
                                 .order_by(Observation.observed_at)).all()
            if o.price_minor is not None and o.currency == listing.currency]


def score_listing(s: Session, listing: Listing, ctx: PeerContext | None = None) -> DealScore:
    ds = DealScore(listing.id, None, "No price", "none")
    if listing.price_minor is None:
        ds.warnings.append("Price unavailable at last collection — not treated as zero.")
        return ds
    ctx = ctx or PeerContext(s)
    hist = priced_history(s, listing)
    ds.n_obs = len(hist)
    price = listing.price_minor
    comps = ds.components

    # 1. own history
    span_days = (hist[-1].observed_at - hist[0].observed_at).total_seconds() / 86400 if len(hist) > 1 else 0
    has_history = len(hist) >= config.MIN_HISTORY_OBS and span_days >= config.MIN_HISTORY_SPAN_DAYS
    if has_history:
        base = median(o.price_minor for o in hist[:-1])
        pts = max(0.0, min(1.0, (1 - price / base) / 0.25)) * 40
        comps.append(Component("history_drop", round(pts, 1), 40, "verified",
                               f"latest {price/100:.2f} vs median {base/100:.2f} of {len(hist)-1} earlier observations "
                               f"over {span_days:.0f} days"))
    else:
        comps.append(Component("history_drop", 0, 40, "none",
                               f"only {len(hist)} observation(s) over {span_days:.0f} day(s); need "
                               f"{config.MIN_HISTORY_OBS}+ over {config.MIN_HISTORY_SPAN_DAYS}+ days"))
        ds.warnings.append("Not enough price history to confirm any discount is genuine.")

    # 2. peers
    peer_prices, basis = ctx.peers(listing)
    if len(peer_prices) >= config.MIN_PEERS:
        med = median(peer_prices)
        pts = max(0.0, min(1.0, (1 - price / med) / 0.4)) * 40
        rel = (price / med - 1) * 100
        comps.append(Component("peer_value", round(pts, 1), 40, "estimated",
                               f"{abs(rel):.0f}% {'below' if rel < 0 else 'above'} the median ({med/100:.2f}) of {basis}"))
    else:
        comps.append(Component("peer_value", 0, 40, "none", f"only {len(peer_prices)} comparable listing(s); need {config.MIN_PEERS}+"))

    # 3. markdown claim
    orig = listing.original_price_minor
    if orig and orig > price:
        claimed = (orig - price) / orig
        seen_high = max((o.price_minor for o in hist), default=0)
        if has_history and seen_high >= 0.95 * orig:
            comps.append(Component("markdown", round(min(1, claimed / 0.4) * 20, 1), 20, "verified",
                                   f"claimed {claimed*100:.0f}% off; the original price was observed in our history"))
        elif has_history:
            comps.append(Component("markdown", 0, 20, "none",
                                   f"claims {claimed*100:.0f}% off but highest price we ever observed is {seen_high/100:.2f}"))
            ds.warnings.append("Suspicious original price: never observed in our history.")
        else:
            comps.append(Component("markdown", round(min(5, claimed * 20), 1), 20, "unverified",
                                   f"claims {claimed*100:.0f}% off; cannot verify without history (credit capped)"))
    else:
        comps.append(Component("markdown", 0, 20, "none", "no original/list price stated"))

    ds.score = round(sum(c.points for c in comps), 1)
    hist_pts = comps[0].points
    peer_pts = comps[1].points
    ds.confidence = "high" if has_history else ("medium" if len(peer_prices) >= config.MIN_PEERS else "low")
    if listing.availability == "out_of_stock":
        ds.label = "Out of stock"
    elif has_history and hist_pts >= 10 and ds.score >= 40:
        ds.label = "Verified price drop"
    elif peer_pts >= 15:
        ds.label = "Below typical price (estimate)"
    else:
        ds.label = "Typical price" if len(peer_prices) >= config.MIN_PEERS else "Insufficient data"
    return ds


def top_deals(s: Session, limit: int = 12, category: str | None = None) -> list[tuple[Listing, DealScore]]:
    q = select(Listing).where(Listing.active.is_(True), Listing.price_minor.is_not(None),
                              Listing.availability != "out_of_stock")
    if category:
        q = q.where(Listing.category == category)
    ctx = PeerContext(s)
    scored = [(l, score_listing(s, l, ctx)) for l in s.scalars(q).all()]
    scored = [x for x in scored if x[1].score is not None and x[1].score > 0]
    scored.sort(key=lambda x: x[1].score, reverse=True)
    return scored[:limit]
