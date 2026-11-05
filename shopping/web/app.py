"""FastAPI app: server-rendered pages + JSON API under /api/v1. Modular monolith: no business logic here."""
import threading
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from shopping import affiliate, assistant, importcost, plans
from shopping.search import SORTS, facets, same_product, search_listings
from shopping.web.charts import price_chart
from watchtower import config, deals, monitor
from watchtower.collector import ensure_sources, run_source
from watchtower.db import Database
from watchtower.matching import resolve_candidate
from watchtower.models import (AffiliateClick, CollectionRun, Event, Listing, MatchCandidate, Observation, Source,
                               WatchItem, utcnow)
from watchtower.money import fmt_money
from watchtower.quality import freshness, price_status
from watchtower.sources import ADAPTERS

HERE = Path(__file__).parent
OWNER = "local"  # single-tenant until accounts exist (P1)
_running: set[str] = set()
_lock = threading.Lock()


def create_app(db: Database | None = None) -> FastAPI:
    db = db or Database()
    db.init()
    ensure_sources(db)
    app = FastAPI(title="Watchtower Shopping Intelligence", version="0.1.0")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")
    tpl.env.filters["money"] = fmt_money
    tpl.env.filters["dt"] = lambda d: d.strftime("%Y-%m-%d %H:%M UTC") if d else "never"
    tpl.env.globals.update(SORTS=SORTS, fmt_money=fmt_money)

    def get_s():
        with db.session() as s:
            yield s

    def admin(request: Request):
        tok = request.headers.get("x-admin-token") or request.query_params.get("token") or request.cookies.get("wt_admin")
        if config.ADMIN_TOKEN:
            if tok != config.ADMIN_TOKEN:
                raise HTTPException(403, "admin token required")
        elif (request.client.host if request.client else "") not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(403, "admin is loopback-only unless WATCHTOWER_ADMIN_TOKEN is set")

    def render(request, name, **ctx):
        return tpl.TemplateResponse(request, name, {"now": utcnow(), "plan": plans.plan_for(OWNER), **ctx})

    def banner(s):
        kinds = {k for (k,) in s.execute(select(Source.kind).where(Source.enabled.is_(True))).all()}
        return "sandbox" if kinds == {"sandbox"} else None

    def with_status(s, rows):
        ctx = deals.PeerContext(s)
        return [{"l": l, "deal": deals.score_listing(s, l, ctx), "pstatus": price_status(l, l.source),
                 "fresh": freshness(l, l.source)} for l in rows]

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
    def search(request: Request, q: str = "", category: str = "", source: str = "", currency: str = "",
               min_price: float | None = None, max_price: float | None = None, in_stock: bool = False,
               sort: str = "price_asc", page: int = Query(1, ge=1), s=Depends(get_s)):
        rows, total = search_listings(s, q, category or None, source or None, currency or None, min_price, max_price,
                                      in_stock, sort, page)
        return render(request, "search.html", items=with_status(s, rows), total=total, page=page, per_page=24,
                      f={"q": q, "category": category, "source": source, "currency": currency, "min_price": min_price,
                         "max_price": max_price, "in_stock": in_stock, "sort": sort},
                      facets=facets(s), banner=banner(s))

    @app.get("/listing/{listing_id}")
    def listing_page(request: Request, listing_id: int, dest: str = "", ship: float | None = None, s=Depends(get_s)):
        l = s.get(Listing, listing_id)
        if not l:
            raise HTTPException(404, "listing not found")
        obs = s.scalars(select(Observation).where(Observation.listing_id == l.id).order_by(Observation.observed_at)).all()
        pts = [(o.observed_at, o.price_minor) for o in obs if o.price_minor is not None and o.currency == l.currency]
        events = s.scalars(select(Event).where(Event.listing_id == l.id).order_by(Event.detected_at.desc())).all()
        ds = deals.score_listing(s, l)
        est = None
        if dest or ship is not None:
            est = importcost.estimate(l.price_minor, l.currency, dest or None, round(ship * 100) if ship is not None else None)
        link = affiliate.build_link(l)
        return render(request, "listing.html", l=l, obs=obs, chart=price_chart(pts, l.currency), npts=len(pts), events=events,
                      deal=ds, others=same_product(s, l), est=est, dest=dest, ship=ship, link=link,
                      pstatus=price_status(l, l.source), fresh=freshness(l, l.source), banner=banner(s),
                      watching=bool(s.scalar(select(WatchItem.id).where(WatchItem.owner == OWNER, WatchItem.listing_id == l.id))))

    @app.get("/deals")
    def deals_page(request: Request, category: str = "", s=Depends(get_s)):
        return render(request, "deals.html", top=deals.top_deals(s, 30, category or None), category=category,
                      facets=facets(s), banner=banner(s))

    @app.get("/research")
    def research(request: Request, q: str = "", ship: float | None = None, s=Depends(get_s)):
        res = assistant.answer(s, q, shipping_minor=round(ship * 100) if ship is not None else None) if q.strip() else None
        return render(request, "research.html", q=q, res=res, ship=ship, banner=banner(s))

    @app.get("/go/{listing_id}")
    def go(listing_id: int, s=Depends(get_s)):
        l = s.get(Listing, listing_id)
        if not l:
            raise HTTPException(404)
        return RedirectResponse(affiliate.record_click(s, l).url, status_code=302)

    @app.get("/watchlist")
    def watchlist(request: Request, msg: str = "", s=Depends(get_s)):
        return render(request, "watchlist.html", items=monitor.evaluate(s, OWNER), msg=msg, banner=banner(s))

    @app.post("/watch/{listing_id}")
    def watch(listing_id: int, target: str = Form(""), s=Depends(get_s)):
        ok, why = plans.can_add_watch(s, OWNER)
        exists = s.scalar(select(WatchItem.id).where(WatchItem.owner == OWNER, WatchItem.listing_id == listing_id))
        if not ok and not exists:
            return RedirectResponse(f"/watchlist?msg={why}", status_code=303)
        try:
            tp = round(float(target) * 100) if target.strip() else None
            if tp is not None and tp <= 0:
                raise ValueError
            monitor.add_watch(s, listing_id, OWNER, tp)
        except ValueError:
            return RedirectResponse("/watchlist?msg=Invalid target price or listing", status_code=303)
        return RedirectResponse("/watchlist", status_code=303)

    @app.post("/watch/{listing_id}/remove")
    def unwatch(listing_id: int, s=Depends(get_s)):
        w = s.scalar(select(WatchItem).where(WatchItem.owner == OWNER, WatchItem.listing_id == listing_id))
        if w:
            s.delete(w)
        return RedirectResponse("/watchlist", status_code=303)

    # ------------------------------------------------------------------ admin
    @app.get("/admin", dependencies=[Depends(admin)])
    def admin_page(request: Request, s=Depends(get_s)):
        return render(request, "admin.html", sources=s.scalars(select(Source).order_by(Source.name)).all(),
                      runs=s.scalars(select(CollectionRun).order_by(CollectionRun.id.desc()).limit(10)).all(),
                      queue=s.scalars(select(MatchCandidate).where(MatchCandidate.status == "pending")).all(),
                      clicks=s.scalar(select(func.count()).select_from(AffiliateClick)), running=sorted(_running),
                      token=request.query_params.get("token", ""), banner=banner(s))

    def _collect(key: str):
        try:
            run_source(db, key)
        finally:
            with _lock:
                _running.discard(key)

    @app.post("/admin/collect/{key}", dependencies=[Depends(admin)])
    def admin_collect(key: str, bg: BackgroundTasks, token: str = Form("")):
        if key not in ADAPTERS:
            raise HTTPException(404, "unknown source")
        with _lock:
            if key in _running:
                return RedirectResponse(f"/admin?token={token}", status_code=303)
            _running.add(key)
        bg.add_task(_collect, key)
        return RedirectResponse(f"/admin?token={token}", status_code=303)

    @app.post("/admin/match/{cand_id}/{decision}", dependencies=[Depends(admin)])
    def admin_match(cand_id: int, decision: str, token: str = Form(""), s=Depends(get_s)):
        if decision not in ("accept", "reject"):
            raise HTTPException(400)
        resolve_candidate(s, cand_id, decision == "accept")
        return RedirectResponse(f"/admin?token={token}", status_code=303)

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
    def api_listings(q: str = "", category: str = "", source: str = "", currency: str = "", min_price: float | None = None,
                     max_price: float | None = None, in_stock: bool = False, sort: str = "price_asc",
                     page: int = Query(1, ge=1), per_page: int = Query(24, ge=1, le=100), s=Depends(get_s)):
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
    def api_deals(category: str = "", limit: int = Query(20, ge=1, le=100), s=Depends(get_s)):
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
    def api_research(q: str = Query(..., min_length=3), ship: float | None = None, s=Depends(get_s)):
        r = assistant.answer(s, q, shipping_minor=round(ship * 100) if ship is not None else None)
        return JSONResponse({"text": r["text"], "interpretation": r["interpretation"], "caveats": r["caveats"],
                             "gaps": r["gaps"], "retrieved_listing_ids": r["retrieved_listing_ids"],
                             "llm_summary": r["llm_summary"],
                             "picks": [{"listing": listing_json(p.listing, p.deal), "meets": p.meets, "misses": p.misses,
                                        "unknown": p.unknown, "import_estimate": p.import_estimate} for p in r["picks"]]})

    return app
