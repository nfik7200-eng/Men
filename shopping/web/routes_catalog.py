"""Catalog pages: search, the listing page, the product comparison page and the deal radar page. Moved out of the
create_app closure in shopping/web/app.py; behaviour is unchanged. The helpers that depend on app-wide state
(kinds_for, with_status) are passed in by the caller."""
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from shopping import affiliate, importcost
from shopping import fx as fxmod
from shopping.search import CurrencyRequired, facets, same_product, search_listings
from shopping.web.charts import price_chart
from shopping.web.params import Money, Text
from watchtower import deals
from watchtower.currency import to_minor
from watchtower.models import Event, Listing, Observation, Product, SourcePolicy, WatchItem
from watchtower.quality import freshness, price_status


def register_catalog(app, *, get_s, render, banner, owner_of, kinds_for, with_status):
    """Registers the search, listing, product and deal-radar pages."""
    @app.get("/search")
    def search(request: Request, q: Text = "", category: Text = "", source: Text = "", currency: Text = "",
               min_price: Money = None, max_price: Money = None, in_stock: bool = False,
               sort: Text = "price_asc", data: Text = "", page: int = Query(1, ge=1, le=1000), s=Depends(get_s)):
        kinds, data = kinds_for(s, data)
        info: dict = {}
        notice = ""
        try:
            rows, total = search_listings(s, q, category or None, source or None, currency or None, min_price, max_price,
                                          in_stock, sort, page, kinds=kinds, info=info)
        except CurrencyRequired as e:  # say so on the page; never apply a price limit in a currency the person did not choose
            min_price = max_price = None
            rows, total = search_listings(s, q, category or None, source or None, currency or None, None, None,
                                          in_stock, sort, page, kinds=kinds, info=info)
            notice = f"{e} The price limit was not applied."
        cur_list = info.get("currencies", [])
        if len(cur_list) > 1 and not currency:
            notice = (notice + " " if notice else "") + (f"These results include {len(cur_list)} currencies ({', '.join(cur_list)}). "
                      "Prices are not compared across currencies: choose one to sort or filter by price.")
        return render(request, "search.html", items=with_status(s, rows), total=total, page=page, per_page=24, notice=notice,
                      f={"q": q, "category": category, "source": source, "currency": currency, "min_price": min_price,
                         "max_price": max_price, "in_stock": in_stock, "sort": sort, "data": data},
                      facets=facets(s), banner=banner(s), islands=True)

    @app.get("/listing/{listing_id}")
    def listing_page(request: Request, listing_id: int, dest: Annotated[str, Query(max_length=2)] = "", ship: Money = None,
                     ins: Money = None, hand: Money = None, to: Annotated[str, Query(max_length=3)] = "", s=Depends(get_s)):
        l = s.get(Listing, listing_id)
        if not l:
            raise HTTPException(404, "listing not found")
        obs = s.scalars(select(Observation).where(Observation.listing_id == l.id).order_by(Observation.observed_at)).all()
        pts = [(o.observed_at, o.price_minor) for o in obs if o.price_minor is not None and o.currency == l.currency]
        events = s.scalars(select(Event).where(Event.listing_id == l.id).order_by(Event.detected_at.desc())).all()
        ds = deals.score_listing(s, l)
        stats = deals.price_stats(s, l)
        est = None
        if dest or ship is not None or ins is not None or hand is not None or to:
            ship_minor, ship_ev = (to_minor(ship, l.currency), "user-provided") if ship is not None else (l.shipping_minor, "quoted")
            quote = fxmod.get_rate(s, l.currency, to) if len(to) == 3 else None
            est = importcost.estimate(l.price_minor, l.currency, dest or None, ship_minor,
                                      insurance_minor=round(ins * 100) if ins is not None else None,
                                      handling_minor=round(hand * 100) if hand is not None else None,
                                      shipping_evidence=ship_ev, fx=quote)
            if est.get("available") and len(to) == 3 and quote is None:
                est["fx_missing"] = f"No sourced {l.currency}→{to.upper()} exchange rate is recorded, so nothing was converted."
            if est.get("available") and ship is None and l.shipping_minor is not None:
                est["notes"].append("Shipping is the source's own quote for its default destination; it may not apply to yours.")
        link = affiliate.build_link(l)
        policy = s.scalar(select(SourcePolicy).where(SourcePolicy.source_id == l.source_id))
        return render(request, "listing.html", l=l, obs=obs, chart=price_chart(pts, l.currency), npts=len(pts), events=events, policy=policy, islands=True,
                      deal=ds, stats=stats, others=same_product(s, l), est=est, dest=dest, ship=ship, ins=ins, hand=hand, to=to, link=link,
                      pstatus=price_status(l, l.source), fresh=freshness(l, l.source), banner=banner(s),
                      watching=bool(s.scalar(select(WatchItem.id).where(WatchItem.owner == owner_of(request), WatchItem.listing_id == l.id))))

    @app.get("/product/{product_id}")
    def product_page(request: Request, product_id: int, s=Depends(get_s)):
        prod = s.get(Product, product_id)
        if not prod:
            raise HTTPException(404, "product not found")
        rows = list(s.scalars(select(Listing).options(joinedload(Listing.source)).where(
            Listing.product_id == product_id, Listing.active.is_(True)).order_by(Listing.price_minor.asc().nulls_last())).unique())
        items = [{"l": l, "stats": deals.price_stats(s, l), "fresh": freshness(l, l.source), "pstatus": price_status(l, l.source)} for l in rows]
        cheapest = {}  # per currency: no cross-currency ranking without a sourced FX rate
        for it in items:
            l = it["l"]
            if l.price_minor is not None and l.availability in ("in_stock", "unknown") and it["pstatus"] != "stale":
                cheapest.setdefault(l.currency, l.id)
        multi_currency = len({it["l"].currency for it in items}) > 1
        return render(request, "product.html", prod=prod, items=items, cheapest=cheapest, multi_currency=multi_currency,
                      kinds={it["l"].source.kind for it in items}, banner=banner(s))

    @app.get("/deals")
    def deals_page(request: Request, category: Text = "", data: Text = "", s=Depends(get_s)):
        kinds, data = kinds_for(s, data)
        return render(request, "deals.html", top=deals.top_deals(s, 30, category or None, kinds=kinds), category=category,
                      facets=facets(s), banner=banner(s), data=data, islands=True)
