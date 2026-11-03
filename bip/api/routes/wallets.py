from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from bip.api.deps import Context
from bip.api.schemas import Out
from bip.db.records import WalletProfile, WalletSummary

router = APIRouter(tags=["wallets"])


class WalletList(Out):
    total: int
    limit: int
    offset: int
    items: tuple[WalletSummary, ...]


@router.get("/wallets", response_model=WalletList)
def list_wallets(
    ctx: Context,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> WalletList:
    items, total = ctx.repo.list_wallets(limit, offset)
    return WalletList(total=total, limit=limit, offset=offset, items=tuple(items))


@router.get("/wallets/{chain}/{address}", response_model=WalletProfile)
def get_wallet(
    ctx: Context,
    chain: Annotated[str, Path(min_length=1, max_length=32)],
    address: Annotated[str, Path(min_length=1, max_length=100)],
) -> WalletProfile:
    return ctx.repo.get_wallet(chain, address)
