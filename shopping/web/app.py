"""FastAPI app: server-rendered pages + JSON API under /api/v1. Modular monolith: no business logic here."""
import re
import time
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from shopping import affiliate, assistant, plans
from shopping.search import SORTS, CurrencyRequired, facets, search_listings
from watchtower import config, deals, monitor
from watchtower import notifications as notes
from shopping.web import routes_admin, routes_alerts, routes_catalog, routes_compare, routes_watchlist, security
from shopping.web.params import Money, Text
from watchtower.collector import ensure_sources
from watchtower.db import Database
from watchtower.coverage import coverage_report
from watchtower.models import (AffiliateClick, CollectionRun, Listing, Observation, Product, Source,
                               utcnow)
from watchtower.money import fmt_money
from watchtower.quality import freshness, price_status
from watchtower.sources import ADAPTERS

HERE = Path(__file__).parent




def urlencode_params(items, drop: str | None = None) -> str:
    """Query string from (key, value) pairs, URL-encoded, minus one key (the pager rebuilds `page`)."""
    from urllib.parse import urlencode
    return urlencode([(k, v) for k, v in items if k != drop])


def match_label(status: str | None, confidence: float | None) -> str:
    """Plain-language match status for people (the raw status stays in the data)."""
    pct = f", {round(confidence * 100)}% confidence" if confidence else ""
    return {"self": "Only listing for this product", "auto": f"Automatic match{pct}", "confirmed": "Confirmed by a person",
            "review": "Awaiting review"}.get(status or "", status or "")


def plain_reason(text: str) -> str:
    """'identical name [\'4\', \'b\']' -> 'identical name (4, b)': matcher reasons were stored with Python list reprs."""
    return re.sub(r"\[([^\]]*)\]", lambda m: "(" + ", ".join(re.findall(r"'([^']*)'", m.group(1))) + ")", text or "")


COVERAGE_TTL_S = 30  # seconds the public coverage report is reused




def create_app(db: Database | None = None) -> FastAPI:
    db = db or Database()
    db.init()
    ensure_sources(db)
    app = FastAPI(title="Watchtower Shopping Intelligence", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    security.install(app)
    tpl = Jinja2Templates(directory=HERE / "templates")
    tpl.env.filters["money"] = fmt_money
    tpl.env.globals["qs_without"] = lambda request, key: urlencode_params(request.query_params.multi_items(), drop=key)
    tpl.env.filters["match_label"] = match_label
    tpl.env.filters["plain_reason"] = plain_reason
    tpl.env.filters["dt"] = lambda d: d.strftime("%Y-%m-%d %H:%M UTC") if d else "never"

    def alerts_unread_for(request: Request) -> int:
        """Unread alerts for the browser making this request, for the navigation badge. 0 without a valid owner cookie or
        before the alerts table exists: a page must never fail or slow down because of the badge."""
        if not security.valid_owner(request.cookies.get(security.COOKIE_OWNER, "")):
            return 0
        try:
            with db.session() as s:
                return notes.unread_count(s, owner_of(request)) if notes.available(s) else 0
        except Exception:  # the badge is decoration: a database hiccup must not break the page
            return 0

    tpl.env.globals["alerts_unread"] = alerts_unread_for
    tpl.env.globals.update(SORTS=SORTS, fmt_money=fmt_money, MIN_HISTORY_OBS=config.MIN_HISTORY_OBS,
                           MIN_HISTORY_SPAN_DAYS=config.MIN_HISTORY_SPAN_DAYS)

    def active_disclosures():
        with db.session() as s_:
            nets: set[str] = {n for (n,) in s_.execute(select(AffiliateClick.network).where(AffiliateClick.network.is_not(None)).distinct()).all() if n}
            if s_.scalar(select(Listing.id).where(Listing.affiliate_url.is_not(None)).limit(1)):
                nets.add("provider")
        nets |= {r.get("network") or "x" for r in affiliate.load_config().values()}
        return affiliate.disclosures(nets)
    tpl.env.globals["disclosures"] = active_disclosures

    def get_s():
        with db.session() as s:
            yield s

    admin = security.require_admin

    def owner_of(request: Request) -> str:
        return request.state.owner

    def render(request, name, **ctx):
        return tpl.TemplateResponse(request, name, {"now": utcnow(), "plan": plans.plan_for(owner_of(request)), **ctx})

    def has_live(s) -> bool:
        return bool(s.scalar(select(Listing.id).join(Source, Source.id == Listing.source_id)
                             .where(Source.kind == "live", Listing.active.is_(True)).limit(1)))

    def banner(s):
        """'demo' = no real-retailer listings exist yet, so everything shown is test data."""
        return None if has_live(s) else "demo"

    def kinds_of(data: str):
        """The source kinds a data class includes; None = every source."""
        return {"real": ("live",), "demo": ("demo", "sandbox")}.get(data)

    def kinds_for(s, data: str):
        """'real' (live sources only) is the default once any real listings exist; demo/sandbox are opt-in views."""
        d = data or ("real" if has_live(s) else "all")
        return kinds_of(d), d

    def with_status(s, rows):
        ctx = deals.PeerContext(s)
        return [{"l": l, "deal": deals.score_listing(s, l, ctx), "pstatus": price_status(l, l.source),
                 "fresh": freshness(l, l.source)} for l in rows]

    def wants_json(request: Request) -> bool:
        return request.url.path.startswith("/api/")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
            return RedirectResponse(exc.headers["Location"], status_code=303)
        if wants_json(request):
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        return tpl.TemplateResponse(request, "error.html", {"code": exc.status_code, "detail": exc.detail, "banner": None,
                                                           "plan": plans.PLANS["free"]}, status_code=exc.status_code)

    @app.exception_handler(OverflowError)
    async def overflow_error(request: Request, exc: OverflowError):  # an id beyond SQLite's integer range cannot exist: 404, not 500
        return await http_error(request, StarletteHTTPException(404, "not found"))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        msg = "; ".join(f"{'.'.join(str(x) for x in e['loc'][1:])}: {e['msg']}" for e in exc.errors())[:300]
        if wants_json(request):
            return JSONResponse({"error": "invalid parameters", "detail": msg}, status_code=422)
        return tpl.TemplateResponse(request, "error.html", {"code": 422, "detail": f"Some inputs were not valid ({msg}).",
                                                           "banner": None, "plan": plans.PLANS["free"]}, status_code=422)

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception):
        if wants_json(request):
            return JSONResponse({"error": "internal error"}, status_code=500)
        return tpl.TemplateResponse(request, "error.html", {"code": 500, "detail": "Something went wrong on our side.",
                                                           "banner": None, "plan": plans.PLANS["free"]}, status_code=500)

    # ------------------------------------------------------------------ pages
    @app.get("/health", response_class=PlainTextResponse)
    def health(s=Depends(get_s)):
        s.execute(select(1))
        return "ok"

    @app.get("/")
    def index(request: Request, s=Depends(get_s)):
        live = has_live(s)
        kinds = ("live",) if live else None
        count = lambda q: s.scalar(q) or 0
        L = select(func.count()).select_from(Listing).join(Source, Source.id == Listing.source_id)
        stats = {"listings": count(L), "real_listings": count(L.where(Source.kind == "live")),
                 "observations": count(select(func.count()).select_from(Observation)),
                 "runs": count(select(func.count()).select_from(CollectionRun)),
                 "merchants": count(select(func.count(func.distinct(Listing.source_id))).join(Source, Source.id == Listing.source_id).where(Source.kind == "live")),
                 "multi_obs": count(select(func.count()).select_from(
                     select(Observation.listing_id).join(Listing, Listing.id == Observation.listing_id)
                     .join(Source, Source.id == Listing.source_id).where(*((Source.kind == "live",) if live else ()))
                     .group_by(Observation.listing_id).having(func.count() > 1).subquery()))}
        multi = s.execute(select(Listing.product_id, func.count(func.distinct(Listing.source_id)).label("m"))
                          .join(Source, Source.id == Listing.source_id)
                          .where(Listing.active.is_(True), Listing.product_id.is_not(None), *( (Source.kind == "live",) if live else () ))
                          .group_by(Listing.product_id).having(func.count(func.distinct(Listing.source_id)) >= 2)).all()
        stats["compared_products"] = len(multi)
        products = [s.get(Product, pid) for pid, _ in multi[:8]]
        top = deals.top_deals(s, 6, kinds=kinds)
        changes = monitor.recent_events(s, ("price_drop", "price_rise", "restock", "out_of_stock", "removed"), 8)
        sources = s.scalars(select(Source).order_by(Source.kind.desc(), Source.name)).all()
        return render(request, "index.html", stats=stats, top=top, changes=changes, sources=sources, products=products,
                      facets=facets(s), banner=banner(s), live=live,
                      stale={x.key: (utcnow() - x.last_success_at).total_seconds() / 3600 > x.stale_after_hours
                             if x.last_success_at else True for x in sources})

    # search, listing, product and deal-radar pages live in routes_catalog.py
    routes_catalog.register_catalog(app, get_s=get_s, render=render, banner=banner, owner_of=owner_of,
                                    kinds_for=kinds_for, with_status=with_status)


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

    @app.get("/research")
    def research(request: Request, q: Annotated[str, Query(max_length=500)] = "", ship: Money = None, s=Depends(get_s)):
        res = assistant.answer(s, q, shipping_major=ship) if q.strip() else None
        return render(request, "research.html", q=q, res=res, ship=ship, banner=banner(s), islands=True)

    @app.get("/go/{listing_id}")
    def go(listing_id: int, s=Depends(get_s)):
        l = s.get(Listing, listing_id)
        if not l:
            raise HTTPException(404)
        target = affiliate.record_click(s, l).url
        if urlparse(target).scheme not in ("http", "https"):
            raise HTTPException(404, "no safe outbound link for this listing")
        return RedirectResponse(target, status_code=302)

    # watchlist routes live in routes_watchlist.py (HTML page, forms and the per-browser JSON API)
    require_browser_owner = routes_watchlist.register_watchlist(app, get_s=get_s, owner_of=owner_of, render=render, banner=banner)

    # admin pages, form actions and the token-protected review/status JSON live in routes_admin.py
    routes_admin.register_admin(app, db=db, get_s=get_s, render=render, banner=banner, admin=admin, adapters=ADAPTERS)

    # ------------------------------------------------------------------ JSON API (v1)
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

    routes_compare.register_compare(app, get_s=get_s, render=render, banner=banner)
    # in-app alert routes live in routes_alerts.py (the table is created only by notify-migrate)
    routes_alerts.register_alerts(app, get_s=get_s, owner_of=owner_of, require_browser_owner=require_browser_owner,
                                  render=render, banner=banner)

    return app
