"""Public read-only data: the coverage report (page and JSON), the JSON API for listings, history, deals, events,
sources and the research assistant. Moved out of the create_app closure in shopping/web/app.py; behaviour is unchanged.
The browse and deal filters come from the caller (kinds_of), so the data-class rules live in one place."""
import time

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from shopping import assistant
from shopping.search import CurrencyRequired, search_listings
from shopping.web.params import Money, Text
from watchtower import deals, monitor
from watchtower.coverage import coverage_report
from watchtower.models import Listing, Observation, Source
from watchtower.quality import freshness, price_status

COVERAGE_TTL_S = 30  # seconds the public coverage report is reused


def listing_json(l: Listing, deal: deals.DealScore | None = None):
    d = {"id": l.id, "title": l.title, "brand": l.brand, "category": l.category, "url": l.url,
         "source": l.source.key, "source_kind": l.source.kind, "currency": l.currency,
         "price_minor": l.price_minor, "original_price_minor": l.original_price_minor,
         "availability": l.availability, "specs": l.specs, "active": l.active, "product_id": l.product_id,
         "match_status": l.match_status, "match_confidence": l.match_confidence,
         "last_observed_at": l.last_observed_at.isoformat() if l.last_observed_at else None,
         "price_status": price_status(l, l.source), "freshness": freshness(l, l.source)}
    if deal:
        d["deal"] = {"score": deal.score, "label": deal.label, "confidence": deal.confidence, "warnings": deal.warnings,
                     "components": [c.__dict__ for c in deal.components]}
    return d


def register_public(app, *, get_s, render, banner, kinds_of):
    """Registers the coverage report and the public JSON API."""
    _cov_cache: dict = {"at": 0.0, "rep": None}

    def cached_coverage(s):
        """The report scans every crawl_urls row; the route is public, so recompute at most every 30 s."""
        if _cov_cache["rep"] is None or time.monotonic() - _cov_cache["at"] > COVERAGE_TTL_S:
            _cov_cache["rep"], _cov_cache["at"] = coverage_report(s, with_next_up=True), time.monotonic()
        return _cov_cache["rep"]

    @app.get("/coverage")
    def coverage_page(request: Request, s=Depends(get_s)):
        return render(request, "coverage.html", rep=cached_coverage(s), banner=banner(s), islands=True)

    @app.get("/api/v1/coverage")
    def api_coverage(s=Depends(get_s)):
        return JSONResponse(jsonable_encoder(cached_coverage(s)))

    @app.get("/api/v1/listings")
    def api_listings(q: Text = "", category: Text = "", source: Text = "", currency: Text = "", min_price: Money = None,
                     max_price: Money = None, in_stock: bool = False, sort: Text = "price_asc",
                     page: int = Query(1, ge=1, le=1000), per_page: int = Query(24, ge=1, le=100),
                     data: Text = "", s=Depends(get_s)):
        """`data` = real | demo | all. Omitted keeps the historical behaviour (every source); the browse island always sends it."""
        kinds = kinds_of(data) if data else None
        info: dict = {}
        try:
            rows, total = search_listings(s, q, category or None, source or None, currency or None, min_price, max_price,
                                          in_stock, sort, page, per_page, kinds=kinds, info=info)
        except CurrencyRequired as e:
            raise HTTPException(422, str(e))
        return {"total": total, "page": page, "items": [listing_json(l) for l in rows],
                "currencies": info.get("currencies", []), "sort_fallback": info.get("sort_fallback")}

    @app.get("/api/v1/listings/{listing_id}")
    def api_listing(listing_id: int, s=Depends(get_s)):
        l = s.get(Listing, listing_id)
        if not l:
            raise HTTPException(404, "listing not found")
        return listing_json(l, deals.score_listing(s, l))

    @app.get("/api/v1/listings/{listing_id}/history")
    def api_history(listing_id: int, s=Depends(get_s)):
        if not s.get(Listing, listing_id):
            raise HTTPException(404, "listing not found")
        obs = s.scalars(select(Observation).where(Observation.listing_id == listing_id).order_by(Observation.observed_at)).all()
        return {"listing_id": listing_id, "observations": [
            {"observed_at": o.observed_at.isoformat(), "price_minor": o.price_minor, "original_price_minor": o.original_price_minor,
             "currency": o.currency, "availability": o.availability, "shipping_minor": o.shipping_minor} for o in obs]}

    @app.get("/api/v1/deals")
    def api_deals(category: Text = "", limit: int = Query(20, ge=1, le=100), s=Depends(get_s)):
        return {"items": [listing_json(l, d) for l, d in deals.top_deals(s, limit, category or None)]}

    @app.get("/api/v1/events")
    def api_events(limit: int = Query(50, ge=1, le=200), s=Depends(get_s)):
        return {"items": [{"id": e.id, "listing_id": e.listing_id, "kind": e.kind, "old": e.old_value, "new": e.new_value,
                           "pct": e.pct, "detected_at": e.detected_at.isoformat()} for e in monitor.recent_events(s, None, limit)]}

    @app.get("/api/v1/sources")
    def api_sources(s=Depends(get_s)):
        return {"items": [{"key": x.key, "name": x.name, "kind": x.kind, "currency": x.currency,
                           "last_success_at": x.last_success_at.isoformat() if x.last_success_at else None,
                           "last_status": x.last_status, "terms_note": x.terms_note} for x in s.scalars(select(Source))]}

    @app.get("/api/v1/research")
    def api_research(q: str = Query(..., min_length=3, max_length=500), ship: Money = None, s=Depends(get_s)):
        r = assistant.answer(s, q, shipping_major=ship)
        return JSONResponse({"text": r["text"], "interpretation": r["interpretation"], "caveats": r["caveats"],
                             "gaps": r["gaps"], "retrieved_listing_ids": r["retrieved_listing_ids"],
                             "llm_summary": r["llm_summary"],
                             "picks": [{"listing": listing_json(p.listing, p.deal), "meets": p.meets, "misses": p.misses,
                                        "unknown": p.unknown, "import_estimate": p.import_estimate} for p in r["picks"]]})
