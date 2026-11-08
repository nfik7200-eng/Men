from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, joinedload

from watchtower.currency import THREE_DECIMAL, ZERO_DECIMAL, to_minor
from watchtower.models import Listing, Source

SORTS = {
    "price_asc": "Price: low to high", "price_desc": "Price: high to low",
    "rating": "Rating", "newest": "Recently added", "name": "Name",
}



def _price_bound(major: float, currency: str | None):
    """A user's price bound in minor units. With a currency chosen it uses that currency's exponent. Without one, each
    row is compared in its OWN currency (a yen bound must not be scaled like a dollar bound)."""
    if currency:
        return to_minor(major, currency)
    return case(
        (Listing.currency.in_(sorted(ZERO_DECIMAL)), to_minor(major, "JPY")),
        (Listing.currency.in_(sorted(THREE_DECIMAL)), to_minor(major, "KWD")),
        else_=to_minor(major, "USD"),
    )

def search_listings(s: Session, q: str | None = None, category: str | None = None, source: str | None = None,
                    currency: str | None = None, min_price: float | None = None, max_price: float | None = None,
                    in_stock: bool = False, sort: str = "price_asc", page: int = 1, per_page: int = 24,
                    kinds: tuple[str, ...] | None = None):
    stmt = select(Listing).options(joinedload(Listing.source)).join(Source, Source.id == Listing.source_id).where(Listing.active.is_(True))
    if kinds is not None:
        stmt = stmt.where(Source.kind.in_(kinds))
    for term in (q or "").lower().split()[:8]:
        esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")  # literal match, not wildcard
        like = f"%{esc}%"
        stmt = stmt.where(or_(func.lower(Listing.title).like(like, escape="\\"),
                              func.lower(Listing.description).like(like, escape="\\"),
                              func.lower(func.coalesce(Listing.brand, "")).like(like, escape="\\")))
    if category:
        stmt = stmt.where(Listing.category == category)
    if source:
        stmt = stmt.where(Source.key == source)
    if currency:
        stmt = stmt.where(Listing.currency == currency)
    if min_price is not None:
        stmt = stmt.where(Listing.price_minor >= _price_bound(min_price, currency))
    if max_price is not None:
        stmt = stmt.where(Listing.price_minor <= _price_bound(max_price, currency))
    if in_stock:
        stmt = stmt.where(Listing.availability == "in_stock")
    total = s.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    order = {
        "price_asc": Listing.price_minor.asc().nulls_last(), "price_desc": Listing.price_minor.desc().nulls_last(),
        "rating": Listing.rating.desc().nulls_last(), "newest": Listing.first_seen_at.desc(),
        "name": func.lower(Listing.title),
    }.get(sort, Listing.price_minor.asc().nulls_last())
    rows = s.scalars(stmt.order_by(order, Listing.id).limit(per_page).offset((max(page, 1) - 1) * per_page)).unique().all()
    return rows, total


def facets(s: Session) -> dict:
    cats = s.execute(select(Listing.category, func.count()).where(Listing.active.is_(True))
                     .group_by(Listing.category).order_by(func.count().desc())).all()
    srcs = s.execute(select(Source.key, Source.name, Source.kind)).all()
    curs = [c for (c,) in s.execute(select(Listing.currency).distinct()).all()]
    return {"categories": [(c, n) for c, n in cats if c], "sources": srcs, "currencies": curs}


def same_product(s: Session, listing: Listing) -> list[Listing]:
    """Other listings of the same canonical product (cross-store comparison)."""
    if not listing.product_id:
        return []
    return list(s.scalars(select(Listing).options(joinedload(Listing.source)).where(
        Listing.product_id == listing.product_id, Listing.id != listing.id, Listing.active.is_(True))
        .order_by(Listing.price_minor.asc().nulls_last())).unique())
