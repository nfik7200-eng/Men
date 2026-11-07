"""Watch evaluation, shared by consumer alerts now and competitor monitoring later."""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Event, Listing, WatchItem


@dataclass
class WatchStatus:
    watch: WatchItem
    triggered: bool = False
    reasons: list[str] = field(default_factory=list)


def add_watch(s: Session, listing_id: int, owner: str = "local", target_price_minor: int | None = None,
              notify_restock: bool = True) -> WatchItem:
    l = s.get(Listing, listing_id)
    if l is None:
        raise ValueError("unknown listing")
    w = s.scalar(select(WatchItem).where(WatchItem.owner == owner, WatchItem.listing_id == listing_id))
    if w is None:
        w = WatchItem(owner=owner, listing_id=listing_id, baseline_price_minor=l.price_minor,
                      baseline_availability=l.availability, baseline_currency=l.currency)
        s.add(w)
    w.target_price_minor, w.notify_restock = target_price_minor, notify_restock
    s.flush()
    return w


def evaluate(s: Session, owner: str = "local") -> list[WatchStatus]:
    out = []
    for w in s.scalars(select(WatchItem).where(WatchItem.owner == owner).order_by(WatchItem.created_at.desc())):
        l, st = w.listing, WatchStatus(w)
        if w.baseline_currency and l.currency != w.baseline_currency:
            st.reasons.append(f"Currency changed {w.baseline_currency} → {l.currency}: target and price comparisons are "
                              "paused until you set a new target (no conversion is applied)")
            if not l.active:
                st.reasons.append("Listing no longer found at the source")
            out.append(st)
            continue
        if w.target_price_minor and l.price_minor is not None and l.price_minor <= w.target_price_minor:
            st.triggered = True
            st.reasons.append(f"Target reached: {l.price_minor/100:.2f} ≤ {w.target_price_minor/100:.2f} {l.currency}")
        if w.notify_restock and w.baseline_availability == "out_of_stock" and l.availability == "in_stock":
            st.triggered = True
            st.reasons.append("Back in stock")
        if w.baseline_price_minor and l.price_minor and l.price_minor != w.baseline_price_minor:
            pct = (l.price_minor - w.baseline_price_minor) / w.baseline_price_minor * 100
            st.reasons.append(f"Price {'down' if pct < 0 else 'up'} {abs(pct):.1f}% since you started watching")
        if not l.active:
            st.reasons.append("Listing no longer found at the source")
        out.append(st)
    return out


def recent_events(s: Session, kinds: tuple[str, ...] | None = None, limit: int = 20) -> list[Event]:
    q = select(Event).order_by(Event.detected_at.desc(), Event.id.desc()).limit(limit)
    if kinds:
        q = q.where(Event.kind.in_(kinds))
    return list(s.scalars(q))
