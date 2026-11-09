"""Watchlist routes: the HTML page and forms, and the per-browser JSON API. Moved out of the create_app route closure in
shopping/web/app.py; behaviour is unchanged. Watchlists belong to a browser (owner cookie), never to an account."""
import math
from typing import Annotated

from fastapi import Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from shopping import plans
from shopping.web import security
from watchtower import monitor
from watchtower.currency import to_minor
from watchtower.models import Listing, WatchItem


async def json_body(request: Request) -> dict:
    """A JSON object from the request body; anything else is a 422 with a plain reason."""
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(422, "request body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(422, "request body must be a JSON object")
    return body


def register_watchlist(app, *, get_s, owner_of, render, banner):
    """Registers the watchlist routes and returns require_browser_owner, which the alert routes also use."""

    def require_browser_owner(request: Request) -> str:
        """Watchlists belong to a browser. The owner cookie is set on page visits, not on /api calls, so an API client
        must have one already; without it a write would vanish, so refuse it clearly instead."""
        if not security.valid_owner(request.cookies.get(security.COOKIE_OWNER, "")):
            raise HTTPException(401, "Open a Watchtower page in this browser first: the watchlist is tied to the browser session.")
        return owner_of(request)

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

    return require_browser_owner
