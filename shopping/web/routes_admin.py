"""Admin routes: sign-in and sign-out, the server-rendered admin page and its form actions, the token-protected match-review
JSON the review island uses, and the read-only admin status JSON. Moved out of the create_app closure in
shopping/web/app.py; behaviour is unchanged. Every route except sign-in is guarded by the `admin` dependency
(security.require_admin), which is fail-closed when no sufficiently long token is configured."""
import logging
import os
import threading
from typing import Annotated

from fastapi import BackgroundTasks, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from shopping.web import security
from shopping.web.routes_watchlist import json_body
from watchtower import config
from watchtower.collector import run_source, set_enabled
from watchtower.health import source_health
from watchtower.matching import resolve_candidate
from watchtower.models import AffiliateClick, CollectionRun, Listing, MatchCandidate, Source, SourcePolicy

log = logging.getLogger("watchtower.admin")

# Cookie paths for the signed admin session: the admin pages and the two admin JSON prefixes the review island uses.
# Each path is narrow, so the cookie is never sent to the public pages.
ADMIN_COOKIE_PATHS = ("/admin", "/api/v1/match-candidates", "/api/v1/admin")
NON_COMPARABLE_SPECS = {"merchant_category"}  # each shop's own category wording: differing is expected, not evidence
_running: set[str] = set()  # source keys with a collection in flight (one per process)
_lock = threading.Lock()


def queue_rows(s):
    return s.scalars(select(MatchCandidate).where(MatchCandidate.status == "pending")).all()


def budget_rows(adapters):
    from watchtower import config as wcfg
    return [{"key": k, "name": a.name, "default": a.default_max_pages, "effective": wcfg.page_budget(k, a.default_max_pages),
             "live": a.kind == "live"} for k, a in adapters.items() if a.kind == "live"]


def scheduler_status():
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


def candidate_json(s, c: MatchCandidate) -> dict:
    view = side_by_side(s, c)

    def side(l: Listing) -> dict:
        return {"id": l.id, "title": l.title, "source": l.source.key, "currency": l.currency, "price_minor": l.price_minor,
                "availability": l.availability, "specs": l.specs or {}}
    return {"id": c.id, "score": c.score, "reasons": c.reasons or [], "listing": side(c.listing),
            "members": [side(m) for m in view["members"]], "spec_keys": view["spec_keys"], "differing": view["differing"]}


def register_admin(app, *, db, get_s, render, banner, admin, adapters):
    """Registers the admin routes. `admin` is the require_admin dependency; `adapters` is the source adapter registry."""
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

    # ------------------------------------------------------------------ sign-in
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

    # ------------------------------------------------------------------ admin page and form actions
    @app.get("/admin", dependencies=[Depends(admin)])
    def admin_page(request: Request, msg: Annotated[str, Query(max_length=200)] = "", s=Depends(get_s)):
        return render(request, "admin.html", sources=s.scalars(select(Source).order_by(Source.name)).all(),
                      runs=s.scalars(select(CollectionRun).order_by(CollectionRun.id.desc()).limit(10)).all(),
                      queue=queue_rows(s),
                      clicks=s.scalar(select(func.count()).select_from(AffiliateClick)), running=sorted(_running),
                      msg=msg, islands=True, collect_errors=dict(collect_errors), banner=banner(s),
                      ready={k: a.configured() for k, a in adapters.items()},
                      health={x.key: source_health(s, x) for x in s.scalars(select(Source))},
                      policies={p.source.key: p for p in s.scalars(select(SourcePolicy).options(joinedload(SourcePolicy.source)))},
                      budgets=budget_rows(adapters), scheduler=scheduler_status(),
                      sidebyside={c.id: side_by_side(s, c) for c in queue_rows(s)},
                      decided=s.scalars(select(MatchCandidate).where(MatchCandidate.decided_at.is_not(None))
                                        .order_by(MatchCandidate.decided_at.desc()).limit(10)).all())

    @app.post("/admin/collect/{key}", dependencies=[Depends(admin)])
    def admin_collect(key: str, bg: BackgroundTasks, s=Depends(get_s)):
        if key not in adapters:
            raise HTTPException(404, "unknown source")
        src = s.scalar(select(Source).where(Source.key == key))
        if src is not None and not src.enabled:
            return RedirectResponse("/admin?msg=Source is disabled", status_code=303)
        ready, missing = adapters[key].configured()
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
        if key not in adapters or state not in ("enable", "disable"):
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

    # ------------------------------------------------------------------ JSON: match review and admin status (token)
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

    @app.get("/api/v1/admin/settings", dependencies=[Depends(admin)])
    def api_admin_settings(s=Depends(get_s)):
        """Operator settings the admin page shows, as JSON. Read-only except the per-source enabled flag (see below).
        The scheduler is reported, never changed here: it stays disabled unless the owner enables it on the server."""
        budgets = {b["key"]: b for b in budget_rows(adapters)}
        return {
            "sources": [{"key": x.key, "name": x.name, "kind": x.kind, "enabled": x.enabled,
                         "configured": adapters[x.key].configured()[0] if x.key in adapters else False,
                         "page_budget": budgets.get(x.key, {}).get("effective")}
                        for x in s.scalars(select(Source).order_by(Source.name))],
            "scheduler": scheduler_status(),
        }

    @app.post("/api/v1/admin/sources/{key}/enabled", dependencies=[Depends(admin)])
    async def api_admin_source_enabled(key: str, request: Request, s=Depends(get_s)):
        """Enable or disable one source. Body: {"enabled": true|false} (a real boolean; strings are refused).
        Admin token required and Origin-checked like every unsafe method. Changes are logged without any secret."""
        if key not in adapters:
            raise HTTPException(404, "unknown source")
        body = await json_body(request)
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(422, "enabled must be true or false")
        set_enabled(db, key, enabled)
        log.info("admin: source %s set to %s via API", key, "enabled" if enabled else "disabled")
        return {"key": key, "enabled": enabled}
