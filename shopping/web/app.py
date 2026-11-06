"""FastAPI app: server-rendered pages + JSON API under /api/v1. Modular monolith: no business logic here."""
import math
import threading
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from shopping import affiliate, assistant, fx as fxmod, importcost, plans
from shopping.search import SORTS, facets, same_product, search_listings
from shopping.web.charts import price_chart
from watchtower import config, deals, monitor
from shopping.web import security
from watchtower.collector import SourceBusy, SourceDisabled, SourceNotConfigured, ensure_sources, run_source, set_enabled
from watchtower.db import Database
from watchtower.matching import resolve_candidate
from watchtower.models import (AffiliateClick, CollectionRun, Event, Listing, MatchCandidate, Observation, Source,
                               WatchItem, utcnow)
from watchtower.money import fmt_money
from watchtower.quality import freshness, price_status
from watchtower.sources import ADAPTERS

HERE = Path(__file__).parent
Money = Annotated[float | None, Query(ge=0, le=1e9, allow_inf_nan=False)]
Text = Annotated[str, Query(max_length=200)]
_running: set[str] = set()
_lock = threading.Lock()


def create_app(db: Database | None = None) -> FastAPI:
    db = db or Database()
    db.init()
    ensure_sources(db)
    app = FastAPI(title="Watchtower Shopping Intelligence", version="0.1.0")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    security.install(app)
    tpl = Jinja2Templates(directory=HERE / "templates")
    tpl.env.filters["money"] = fmt_money
    tpl.env.filters["dt"] = lambda d: d.strftime("%Y-%m-%d %H:%M UTC") if d else "never"
    tpl.env.globals.update(SORTS=SORTS, fmt_money=fmt_money)

    def active_disclosures():
        with db.session() as s_:
            nets = set(n for (n,) in s_.execute(select(AffiliateClick.network).where(AffiliateClick.network.is_not(None)).distinct()).all())
            if s_.scalar(select(Listing.id).where(Listing.affiliate_url.is_not(None)).limit(1)):
                nets.add("provider")
        nets |= {r.get("network", "x") for r in affiliate.load_config().values()}
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

    def banner(s):
        kinds = {k for (k,) in s.execute(select(Source.kind).where(Source.enabled.is_(True))).all()}
        return "sandbox" if kinds == {"sandbox"} else None

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
        stats = {"listings": s.scalar(select(func.count()).select_from(Listing)),
                 "observations": s.scalar(select(func.count()).select_from(Observation)),
                 "runs": s.scalar(select(func.count()).select_from(CollectionRun)),
                 "multi_obs": s.scalar(select(func.count()).select_from(
                     select(Observation.listing_id).group_by(Observation.listing_id).having(func.count() > 1).subquery()))}
        top = deals.top_deals(s, 6)
        changes = monitor.recent_events(s, ("price_drop", "price_rise", "restock", "out_of_stock", "removed"), 8)
        sources = s.scalars(select(Source).order_by(Source.name)).all()
        return render(request, "index.html", stats=stats, top=top, changes=changes, sources=sources,
                      facets=facets(s), banner=banner(s), stale={x.key: (utcnow() - x.last_success_at).total_seconds() / 3600
                                                                   > x.stale_after_hours if x.last_success_at else True for x in sources})

    @app.get("/search")
    def search(request: Request, q: Text = "", category: Text = "", source: Text = "", currency: Text = "",
               min_price: Money = None, max_price: Money = None, in_stock: bool = False,
               sort: Text = "price_asc", page: int = Query(1, ge=1, le=1000), s=Depends(get_s)):
        rows, total = search_listings(s, q, category or None, source or None, currency or None, min_price, max_price,
                                      in_stock, sort, page)
        return render(request, "search.html", items=with_status(s, rows), total=total, page=page, per_page=24,
                      f={"q": q, "category": category, "source": source, "currency": currency, "min_price": min_price,
                         "max_price": max_price, "in_stock": in_stock, "sort": sort},
                      facets=facets(s), banner=banner(s))

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
        est = None
        if dest or ship is not None or ins is not None or hand is not None or to:
            ship_minor, ship_ev = (round(ship * 100), "user-provided") if ship is not None else (l.shipping_minor, "quoted")
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
        return render(request, "listing.html", l=l, obs=obs, chart=price_chart(pts, l.currency), npts=len(pts), events=events,
                      deal=ds, others=same_product(s, l), est=est, dest=dest, ship=ship, ins=ins, hand=hand, to=to, link=link,
                      pstatus=price_status(l, l.source), fresh=freshness(l, l.source), banner=banner(s),
                      watching=bool(s.scalar(select(WatchItem.id).where(WatchItem.owner == owner_of(request), WatchItem.listing_id == l.id))))

    @app.get("/deals")
    def deals_page(request: Request, category: Text = "", s=Depends(get_s)):
        return render(request, "deals.html", top=deals.top_deals(s, 30, category or None), category=category,
                      facets=facets(s), banner=banner(s))

    @app.get("/research")
    def research(request: Request, q: Annotated[str, Query(max_length=500)] = "", ship: Money = None, s=Depends(get_s)):
        res = assistant.answer(s, q, shipping_minor=round(ship * 100) if ship is not None else None) if q.strip() else None
        return render(request, "research.html", q=q, res=res, ship=ship, banner=banner(s))

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
        return render(request, "watchlist.html", items=monitor.evaluate(s, owner_of(request)), msg=msg, banner=banner(s))

    def parse_target(text: str) -> int | None:
        if not text.strip():
            return None
        v = float(text)
        if not math.isfinite(v) or not (0 < v <= 1e9):
            raise ValueError("target out of range")
        return round(v * 100)

    @app.post("/watch/{listing_id}")
    def watch(request: Request, listing_id: int, target: str = Form("", max_length=20), s=Depends(get_s)):
        owner = owner_of(request)
        ok, why = plans.can_add_watch(s, owner)
        exists = s.scalar(select(WatchItem.id).where(WatchItem.owner == owner, WatchItem.listing_id == listing_id))
        if not ok and not exists:
            return RedirectResponse(f"/watchlist?msg={why}", status_code=303)
        try:
            monitor.add_watch(s, listing_id, owner, parse_target(target))
        except ValueError:
            return RedirectResponse("/watchlist?msg=Invalid target price or listing", status_code=303)
        return RedirectResponse("/watchlist", status_code=303)

    @app.post("/watch/{listing_id}/remove")
    def unwatch(request: Request, listing_id: int, s=Depends(get_s)):
        w = s.scalar(select(WatchItem).where(WatchItem.owner == owner_of(request), WatchItem.listing_id == listing_id))
        if w:
            s.delete(w)
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
        r.set_cookie(security.COOKIE_ADMIN, security.admin_cookie_value(config.ADMIN_TOKEN), max_age=8 * 3600, httponly=True,
                     samesite="strict", path="/admin", secure=request.url.scheme == "https" or config.COOKIE_SECURE)
        return r

    @app.post("/admin/logout")
    def logout():
        r = RedirectResponse("/admin/login", status_code=303)
        r.delete_cookie(security.COOKIE_ADMIN, path="/admin")
        return r

    @app.get("/admin", dependencies=[Depends(admin)])
    def admin_page(request: Request, msg: Annotated[str, Query(max_length=200)] = "", s=Depends(get_s)):
        return render(request, "admin.html", sources=s.scalars(select(Source).order_by(Source.name)).all(),
                      runs=s.scalars(select(CollectionRun).order_by(CollectionRun.id.desc()).limit(10)).all(),
                      queue=s.scalars(select(MatchCandidate).where(MatchCandidate.status == "pending")).all(),
                      clicks=s.scalar(select(func.count()).select_from(AffiliateClick)), running=sorted(_running),
                      msg=msg, banner=banner(s), ready={k: a.configured() for k, a in ADAPTERS.items()})

    def _collect(key: str):
        try:
            run_source(db, key)
        except (SourceBusy, SourceDisabled, SourceNotConfigured):
            pass
        except Exception:  # already recorded on the run row by the collector
            pass
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
        resolve_candidate(s, cand_id, decision == "accept")
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
                     page: int = Query(1, ge=1, le=1000), per_page: int = Query(24, ge=1, le=100), s=Depends(get_s)):
        rows, total = search_listings(s, q, category or None, source or None, currency or None, min_price, max_price,
                                      in_stock, sort, page, per_page)
        return {"total": total, "page": page, "items": [listing_json(l) for l in rows]}

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
        r = assistant.answer(s, q, shipping_minor=round(ship * 100) if ship is not None else None)
        return JSONResponse({"text": r["text"], "interpretation": r["interpretation"], "caveats": r["caveats"],
                             "gaps": r["gaps"], "retrieved_listing_ids": r["retrieved_listing_ids"],
                             "llm_summary": r["llm_summary"],
                             "picks": [{"listing": listing_json(p.listing, p.deal), "meets": p.meets, "misses": p.misses,
                                        "unknown": p.unknown, "import_estimate": p.import_estimate} for p in r["picks"]]})

    return app
