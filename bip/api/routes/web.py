"""Web feeds: configured feed status, stored items, and an admin refresh (contacts sites)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from bip.api.deps import AppContext, Context, WritableContext
from bip.api.errors import error_response
from bip.web.service import FeedBusyError, FeedService

router = APIRouter(prefix="/web", tags=["web"])


def _service(ctx: AppContext) -> FeedService:
    assert ctx.web is not None  # noqa: S101 - always set by create_app
    return ctx.web


@router.get("/feeds")
def feeds(ctx: Context) -> dict[str, Any]:
    return _service(ctx).feeds()


@router.get("/items")
def items(
    ctx: Context,
    feed: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> dict[str, Any]:
    return _service(ctx).items(feed=feed, limit=limit, offset=offset)


@router.post("/feeds/refresh", response_model=None)
def refresh(ctx: WritableContext) -> Any:
    """Admin only: fetches every configured feed now (CONTACTS the feed sites)."""
    if not ctx.settings.web.feeds:
        return error_response(
            409, "no_feeds", "no feeds are configured; add them on the Setup page"
        )
    try:
        return _service(ctx).refresh()
    except FeedBusyError as exc:
        return error_response(409, "refresh_running", str(exc))
