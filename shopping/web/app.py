"""FastAPI app: server-rendered pages + JSON API under /api/v1. Modular monolith: no business logic here."""
import re
import time
import math
import threading
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from shopping import affiliate, assistant, fx as fxmod, importcost, plans
from shopping.search import SORTS, CurrencyRequired, facets, same_product, search_listings
from shopping.web.charts import price_chart
from watchtower import config, deals, monitor
from watchtower import notifications as notes
from watchtower.health import source_health
from shopping.web import routes_alerts, security
from watchtower.collector import ensure_sources, run_source, set_enabled
from watchtower.currency import to_minor
from watchtower.db import Database
from watchtower.coverage import coverage_report
from watchtower.matching import resolve_candidate
from watchtower.models import (AffiliateClick, CollectionRun, Event, Listing, MatchCandidate, Observation, Product, Source,
                               SourcePolicy, WatchItem, utcnow)
from watchtower.money import fmt_money
from watchtower.quality import freshness, price_status
from watchtower.sources import ADAPTERS

HERE = Path(__file__).parent
Money = Annotated[float | None, Query(ge=0, le=1e9, allow_inf_nan=False)]
Text = Annotated[str, Query(max_length=200)]
_running: set[str] = set()
_lock = threading.Lock()


NON_COMPARABLE_SPECS = {"merchant_category"}  # each shop's own category wording: differing is expected, not evidence


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


# Paths the admin session cookie is scoped to: the admin pages, and the admin JSON that the review island calls.
ADMIN_COOKIE_PATHS = ("/admin", "/api/v1/match-candidates", "/api/v1/admin")


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

    @app.get("/watchlist")
    def watchlist(request: Request, msg: Annotated[str, Query(max_length=200)] = "", s=Depends(get_s)):
        plan = plans.plan_for(owner_of(request))
        texts = {"limit": f"The {plan.key} plan allows {plan.watch_limit} watched items. Upgrade (not yet available) to watch more.",
                 "invalid": "Invalid target price or listing"}
        return render(request, "watchlist.html", items=monitor.evaluate(s, owner_of(request)), msg=texts.get(msg, ""), banner=banner(s), islands=True)

    def parse_target(text: str, currency: str) -> int | None:
        if not text.strip():
            return None
        v = float(text)
        if not math.isfinite(v) or not (0 < v <= 1e9):
            raise ValueError("target out of range")
        return to_minor(v, currency)  # the listing's own exponent: yen has no minor unit

    @app.post("/watch/{listing_id}")
    def watch(request: Request, listing_id: int, target: str = Form("", max_length=20), s=Depends(get_s)):
        owner = owner_of(request)
        ok, why = plans.can_add_watch(s, owner)
        exists = s.scalar(select(WatchItem.id).where(WatchItem.owner == owner, WatchItem.listing_id == listing_id))
        if not ok and not exists:
            return RedirectResponse("/watchlist?msg=limit", status_code=303)
        listing = s.get(Listing, listing_id)
        if listing is None:
            return RedirectResponse("/watchlist?msg=invalid", status_code=303)
        try:
            monitor.add_watch(s, listing_id, owner, parse_target(target, listing.currency))
            s.commit()  # commit inside the handler: a failed write must not be reported as success
        except ValueError:
            return RedirectResponse("/watchlist?msg=invalid", status_code=303)
        return RedirectResponse("/watchlist", status_code=303)

    @app.post("/watch/{listing_id}/remove")
    def unwatch(request: Request, listing_id: int, s=Depends(get_s)):
        w = s.scalar(select(WatchItem).where(WatchItem.owner == owner_of(request), WatchItem.listing_id == listing_id))
        if w:
            s.delete(w)
            s.commit()  # commit inside the handler: a failed write must not be reported as success
        return RedirectResponse("/watchlist", status_code=303)

    # ------------------------------------------------------------------ admin
    @app.get("/admin/login")
    def login_page(request: Request, err: str = ""):
        return render(request, "login.html", enabled=bool(config.ADMIN_TOKEN), err=err, banner=None)

    @app.post("/admin/login")
    def login(request: Request, token: str = Form("", max_length=200)):
        if not security.token_ok(token):
            return RedirectResponse("/admin/login?err=1", status_code=303)
        r = RedirectResponse("/admin", status_code=303)
        if not config.ADMIN_TOKEN:  # token_ok above already required one; this makes the type and the rule explicit
            raise HTTPException(403, "Admin is disabled.")
        value = security.admin_cookie_value(config.ADMIN_TOKEN)
        # The same signed session is sent to the admin pages and to the two admin JSON prefixes the review island uses.
        # Each path is narrow: the cookie is still never sent to the public pages.
        for path in ADMIN_COOKIE_PATHS:
            r.set_cookie(security.COOKIE_ADMIN, value, max_age=8 * 3600, httponly=True, samesite="strict", path=path,
                         secure=request.url.scheme == "https" or config.COOKIE_SECURE)
        return r

    @app.post("/admin/logout")
    def logout():
        r = RedirectResponse("/admin/login", status_code=303)
        for path in ADMIN_COOKIE_PATHS:
            r.delete_cookie(security.COOKIE_ADMIN, path=path)
        return r

    def queue_rows(s):
        return s.scalars(select(MatchCandidate).where(MatchCandidate.status == "pending")).all()

    def budget_rows():
        from watchtower import config as wcfg
        return [{"key": k, "name": a.name, "default": a.default_max_pages, "effective": wcfg.page_budget(k, a.default_max_pages),
                 "live": a.kind == "live"} for k, a in ADAPTERS.items() if a.kind == "live"]

    def scheduler_status():
        import os
        from watchtower import config as wcfg, schedule
        try:
            cfg = schedule.load(wcfg.ROOT / "data" / "schedule.json")
        except Exception as ex:  # a broken file must not break the admin page
            return {"error": str(ex)[:160], "enabled": False, "env": False, "sources": {}, "ceiling": wcfg.MAX_PAGE_BUDGET}
        return {"enabled": cfg["enabled"], "env": os.getenv("WATCHTOWER_SCHEDULER_ENABLED") == "1", "sources": cfg["sources"],
                "ceiling": wcfg.MAX_PAGE_BUDGET}

    def side_by_side(s, c: MatchCandidate):
        """Both sides of a review question with the spec keys that differ, so the reviewer compares facts, not titles."""
        members = [m for m in s.scalars(select(Listing).options(joinedload(Listing.source)).where(
            Listing.product_id == c.product_id, Listing.active.is_(True))) if m.source_id != c.listing.source_id]
        keys = sorted({k for x in [c.listing] + members for k in (x.specs or {})} - NON_COMPARABLE_SPECS)
        differing = [k for k in keys if len({repr((x.specs or {}).get(k)) for x in [c.listing] + members}) > 1]
        return {"members": members, "spec_keys": keys, "differing": differing}

    @app.get("/admin", dependencies=[Depends(admin)])
    def admin_page(request: Request, msg: Annotated[str, Query(max_length=200)] = "", s=Depends(get_s)):
        return render(request, "admin.html", sources=s.scalars(select(Source).order_by(Source.name)).all(),
                      runs=s.scalars(select(CollectionRun).order_by(CollectionRun.id.desc()).limit(10)).all(),
                      queue=queue_rows(s),
                      clicks=s.scalar(select(func.count()).select_from(AffiliateClick)), running=sorted(_running),
                      msg=msg, islands=True, collect_errors=dict(collect_errors), banner=banner(s), ready={k: a.configured() for k, a in ADAPTERS.items()},
                      health={x.key: source_health(s, x) for x in s.scalars(select(Source))},
                      policies={p.source.key: p for p in s.scalars(select(SourcePolicy).options(joinedload(SourcePolicy.source)))},
                      budgets=budget_rows(), scheduler=scheduler_status(), sidebyside={c.id: side_by_side(s, c) for c in queue_rows(s)},
                      decided=s.scalars(select(MatchCandidate).where(MatchCandidate.decided_at.is_not(None))
                                        .order_by(MatchCandidate.decided_at.desc()).limit(10)).all())

    collect_errors: dict[str, str] = {}

    def _collect(key: str):
        try:
            run_source(db, key)
            collect_errors.pop(key, None)
        except Exception as e:  # a refusal (policy gate, busy, not configured) creates no run row: keep the reason for the admin page
            collect_errors[key] = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            with _lock:
                _running.discard(key)

    @app.post("/admin/collect/{key}", dependencies=[Depends(admin)])
    def admin_collect(key: str, bg: BackgroundTasks, s=Depends(get_s)):
        if key not in ADAPTERS:
            raise HTTPException(404, "unknown source")
        src = s.scalar(select(Source).where(Source.key == key))
        if src is not None and not src.enabled:
            return RedirectResponse("/admin?msg=Source is disabled", status_code=303)
        ready, missing = ADAPTERS[key].configured()
        if not ready:
            return RedirectResponse("/admin?msg=Not configured: missing " + ", ".join(missing), status_code=303)
        with _lock:
            if key in _running:
                return RedirectResponse("/admin?msg=Already running", status_code=303)
            _running.add(key)
        bg.add_task(_collect, key)
        return RedirectResponse("/admin?msg=Collection started", status_code=303)

    @app.post("/admin/source/{key}/{state}", dependencies=[Depends(admin)])
    def admin_toggle(key: str, state: str):
        if key not in ADAPTERS or state not in ("enable", "disable"):
            raise HTTPException(404)
        set_enabled(db, key, state == "enable")
        return RedirectResponse(f"/admin?msg=Source {state}d", status_code=303)

    @app.post("/admin/match/{cand_id}/{decision}", dependencies=[Depends(admin)])
    def admin_match(cand_id: int, decision: str, s=Depends(get_s)):
        if decision not in ("accept", "reject"):
            raise HTTPException(400)
        resolve_candidate(s, cand_id, decision == "accept", by="admin")
        s.commit()  # commit inside the handler: a failed write must not be reported as success
        return RedirectResponse("/admin", status_code=303)

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

    # ------------------------------------------------------------------ JSON API: watchlist (per browser), match review and admin status (token)
    def watch_json(st: "monitor.WatchStatus") -> dict:
        w, l = st.watch, st.watch.listing
        return {"listing_id": w.listing_id, "title": l.title, "url": l.url, "source": l.source.key, "currency": l.currency,
                "price_minor": l.price_minor, "target_price_minor": w.target_price_minor,
                "baseline_price_minor": w.baseline_price_minor, "notify_restock": w.notify_restock,
                "triggered": st.triggered, "reasons": st.reasons}

    def target_minor(target, currency: str) -> int | None:
        """A JSON target in major units -> minor units of THIS listing's currency (never a global x100)."""
        if target is None:
            return None
        if isinstance(target, bool) or not isinstance(target, (int, float)):
            raise ValueError("target_price must be a number or null")
        v = float(target)
        if not math.isfinite(v) or not (0 < v <= 1e9):
            raise ValueError("target_price out of range")
        return to_minor(v, currency)

    async def json_body(request: Request) -> dict:
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(422, "request body must be JSON")
        if not isinstance(body, dict):
            raise HTTPException(422, "request body must be a JSON object")
        return body

    def require_browser_owner(request: Request) -> str:
        """Watchlists belong to a browser. The owner cookie is set on page visits, not on /api calls, so an API client
        must have one already; without it a write would vanish, so refuse it clearly instead."""
        if not security.valid_owner(request.cookies.get(security.COOKIE_OWNER, "")):
            raise HTTPException(401, "Open a Watchtower page in this browser first: the watchlist is tied to the browser session.")
        return owner_of(request)

    @app.get("/api/v1/watchlist")
    def api_watchlist(request: Request, s=Depends(get_s)):
        owner = require_browser_owner(request)
        return {"limit": plans.plan_for(owner).watch_limit,
                "items": [watch_json(st) for st in monitor.evaluate(s, owner)]}

    @app.post("/api/v1/watchlist", status_code=201)
    async def api_watch_add(request: Request, s=Depends(get_s)):
        owner = require_browser_owner(request)
        body = await json_body(request)
        listing_id = body.get("listing_id")
        if isinstance(listing_id, bool) or not isinstance(listing_id, int):
            raise HTTPException(422, "listing_id must be an integer")
        listing = s.get(Listing, listing_id)
        if listing is None:
            raise HTTPException(404, "listing not found")
        exists = s.scalar(select(WatchItem.id).where(WatchItem.owner == owner, WatchItem.listing_id == listing_id))
        ok, why = plans.can_add_watch(s, owner)
        if not ok and not exists:
            raise HTTPException(409, why)
        try:
            target = target_minor(body.get("target_price"), listing.currency)
        except ValueError as e:
            raise HTTPException(422, str(e))
        w = monitor.add_watch(s, listing_id, owner, target)
        s.commit()  # commit inside the handler: a failed write must not be reported as success
        s.flush()
        return {"listing_id": w.listing_id, "target_price_minor": w.target_price_minor, "currency": listing.currency}

    @app.delete("/api/v1/watchlist/{listing_id}")
    def api_watch_remove(listing_id: int, request: Request, s=Depends(get_s)):
        w = s.scalar(select(WatchItem).where(WatchItem.owner == require_browser_owner(request), WatchItem.listing_id == listing_id))
        if w is None:
            raise HTTPException(404, "not on your watchlist")
        s.delete(w)
        s.commit()  # commit inside the handler: a failed write must not be reported as success
        return {"removed": listing_id}

    def candidate_json(s, c: MatchCandidate) -> dict:
        view = side_by_side(s, c)
        def side(l: Listing) -> dict:
            return {"id": l.id, "title": l.title, "source": l.source.key, "currency": l.currency, "price_minor": l.price_minor,
                    "availability": l.availability, "specs": l.specs or {}}
        return {"id": c.id, "score": c.score, "reasons": c.reasons or [], "listing": side(c.listing),
                "members": [side(m) for m in view["members"]], "spec_keys": view["spec_keys"], "differing": view["differing"]}

    @app.get("/api/v1/match-candidates", dependencies=[Depends(admin)])
    def api_match_candidates(s=Depends(get_s)):
        """Pending human-review questions. Admin token required: merge decisions are never self-service."""
        pending = s.scalars(select(MatchCandidate).where(MatchCandidate.status == "pending").order_by(MatchCandidate.id)).all()
        return {"items": [candidate_json(s, c) for c in pending]}

    @app.post("/api/v1/match-candidates/{cand_id}/decision", dependencies=[Depends(admin)])
    async def api_match_decide(cand_id: int, request: Request, s=Depends(get_s)):
        body = await json_body(request)
        decision = body.get("decision")
        if decision not in ("accept", "reject"):
            raise HTTPException(422, "decision must be 'accept' or 'reject'")
        cand = s.get(MatchCandidate, cand_id)
        if cand is None:
            raise HTTPException(404, "candidate not found")
        if cand.status != "pending":  # resolve_candidate silently does nothing for a decided candidate: say so, do not claim success
            raise HTTPException(409, f"candidate already decided ({cand.status})")
        resolve_candidate(s, cand_id, decision == "accept", by="admin", note=body.get("note") if isinstance(body.get("note"), str) else None)
        s.commit()  # commit inside the handler: a failed write must not be reported as success
        return {"id": cand_id, "decision": decision}

    @app.get("/api/v1/admin/status", dependencies=[Depends(admin)])
    def api_admin_status(s=Depends(get_s)):
        """Read-only operator view: sources and their health, recent collection runs, review backlog. Token required."""
        return {
            "sources": [{"key": x.key, "name": x.name, "kind": x.kind, "enabled": x.enabled,
                         "last_success_at": x.last_success_at.isoformat() if x.last_success_at else None,
                         "last_status": x.last_status} for x in s.scalars(select(Source).order_by(Source.name))],
            "runs": [{"id": r.id, "status": r.status, "source": r.source.key if r.source else None,
                      "started_at": r.started_at.isoformat() if r.started_at else None} for r in
                     s.scalars(select(CollectionRun).order_by(CollectionRun.id.desc()).limit(10))],
            "pending_review": s.scalar(select(func.count()).select_from(MatchCandidate).where(MatchCandidate.status == "pending")) or 0,
        }

    # ------------------------------------------------------------------ comparison: chosen listings side by side
    MATCH_LABELS = {"self": "Own product", "auto": "Automatic match", "confirmed": "Confirmed match", "review": "Awaiting review"}

    @app.get("/compare")
    def compare_page(request: Request, ids: Annotated[str, Query(max_length=200)] = "", s=Depends(get_s)):
        """Up to eight listings side by side. Prices stay in each listing's own currency: when the listings use more than
        one currency they are shown, never ranked against each other."""
        wanted: list[int] = []
        for part in ids.split(","):
            part = part.strip()
            if part.isdigit() and int(part) not in wanted and len(wanted) < 8:
                wanted.append(int(part))
        rows = [l for l in (s.get(Listing, i) for i in wanted) if l is not None]
        currencies = sorted({l.currency for l in rows})
        spec_keys = sorted({k for l in rows for k in (l.specs or {})})
        return render(request, "compare.html", rows=rows, currencies=currencies, spec_keys=spec_keys,
                      labels=MATCH_LABELS, banner=banner(s))

    # in-app alert routes live in routes_alerts.py (the table is created only by notify-migrate)
    routes_alerts.register_alerts(app, get_s=get_s, owner_of=owner_of, require_browser_owner=require_browser_owner,
                                  render=render, banner=banner)

    return app
