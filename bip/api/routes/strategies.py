"""Multi-strategy intelligence API routes: copy trader, meme momentum, dev tracker, wallet classification."""

from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Path

from bip.api.deps import AppContext, Context
from bip.api.routes.chains import _require_chain
from bip.intel.common import J
from bip.strategies.classifier import classify_wallet_profile
from bip.strategies.copy_trader import evaluate_copy_trade_signals
from bip.strategies.dev_tracker import track_developer_activity
from bip.strategies.meme_trader import detect_meme_momentum

router = APIRouter(prefix="/strategies", tags=["strategies"])

ChainP = Annotated[str, Path(min_length=1, max_length=32)]
AddressP = Annotated[str, Path(min_length=1, max_length=100)]


def _wallet_ds(ctx: AppContext, chain: str, address: str):
    _require_chain(ctx, chain)
    return ctx.intel.dataset(chain, address)


@router.get("/classification/{chain}/{address}")
def classify_wallet(ctx: Context, chain: ChainP, address: AddressP) -> J:
    ds = _wallet_ds(ctx, chain, address)
    return classify_wallet_profile(address, ds)


@router.get("/copy-trader/{chain}/{address}")
def copy_trader_signals(ctx: Context, chain: ChainP, address: AddressP) -> J:
    ds = _wallet_ds(ctx, chain, address)
    return evaluate_copy_trade_signals(ds, address)


@router.get("/meme-momentum/{chain}/{address}")
def meme_momentum(ctx: Context, chain: ChainP, address: AddressP) -> J:
    ds = _wallet_ds(ctx, chain, address)
    return detect_meme_momentum(ds)


@router.get("/dev-tracker/{chain}/{address}")
def dev_tracker(ctx: Context, chain: ChainP, address: AddressP) -> J:
    ds = _wallet_ds(ctx, chain, address)
    return track_developer_activity(ds, address)
