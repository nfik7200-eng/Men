from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from bip.api.deps import Context, WritableContext
from bip.api.schemas import WatchlistCreate, WatchlistEntryCreate
from bip.db.records import Watchlist, WatchlistEntry, WatchlistSummary

router = APIRouter(tags=["watchlists"])
Id = Annotated[int, Path(ge=1, le=2**53)]


@router.get("/watchlists", response_model=list[WatchlistSummary])
def list_watchlists(ctx: Context) -> list[WatchlistSummary]:
    return ctx.repo.list_watchlists()


@router.post("/watchlists", response_model=Watchlist, status_code=status.HTTP_201_CREATED)
def create_watchlist(ctx: WritableContext, body: WatchlistCreate) -> Watchlist:
    return ctx.repo.create_watchlist(body.name, body.description)


@router.get("/watchlists/{watchlist_id}", response_model=Watchlist)
def get_watchlist(ctx: Context, watchlist_id: Id) -> Watchlist:
    return ctx.repo.get_watchlist(watchlist_id)


@router.delete("/watchlists/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watchlist(ctx: WritableContext, watchlist_id: Id) -> Response:
    ctx.repo.delete_watchlist(watchlist_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/watchlists/{watchlist_id}/entries",
    response_model=WatchlistEntry,
    status_code=status.HTTP_201_CREATED,
)
def add_entry(ctx: WritableContext, watchlist_id: Id, body: WatchlistEntryCreate) -> WatchlistEntry:
    return ctx.repo.add_watchlist_entry(watchlist_id, body.chain, body.address, body.note)


@router.delete(
    "/watchlists/{watchlist_id}/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_entry(ctx: WritableContext, watchlist_id: Id, entry_id: Id) -> Response:
    ctx.repo.remove_watchlist_entry(watchlist_id, entry_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
