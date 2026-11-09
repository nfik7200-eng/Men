"""FastAPI app: server-rendered pages + JSON API under /api/v1. Modular monolith: no business logic here."""
import re
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from shopping import affiliate, plans
from shopping.search import SORTS, facets
from watchtower import config, deals, monitor
from watchtower import notifications as notes
from shopping.web import routes_admin, routes_alerts, routes_catalog, routes_compare, routes_public, routes_watchlist, security
from watchtower.collector import ensure_sources
from watchtower.db import Database
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

    # coverage report and the public JSON API live in routes_public.py (research and the redirect are in routes_catalog.py)
    routes_public.register_public(app, get_s=get_s, render=render, banner=banner, kinds_of=kinds_of)

    # watchlist routes live in routes_watchlist.py (HTML page, forms and the per-browser JSON API)
    require_browser_owner = routes_watchlist.register_watchlist(app, get_s=get_s, owner_of=owner_of, render=render, banner=banner)

    # admin pages, form actions and the token-protected review/status JSON live in routes_admin.py
    routes_admin.register_admin(app, db=db, get_s=get_s, render=render, banner=banner, admin=admin, adapters=ADAPTERS)

    routes_compare.register_compare(app, get_s=get_s, render=render, banner=banner)
    # in-app alert routes live in routes_alerts.py (the table is created only by notify-migrate)
    routes_alerts.register_alerts(app, get_s=get_s, owner_of=owner_of, require_browser_owner=require_browser_owner,
                                  render=render, banner=banner)

    return app
