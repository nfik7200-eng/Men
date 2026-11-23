"""Keyed REST integrations: status, tests, prices, Etherscan lookups, Dune results.

Reads of stored results are open to viewers. Anything that contacts a provider (tests, refreshes,
lookups) is a POST and therefore admin-only under ``bip.api.access`` (it spends the owner's quota).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query

from bip.api.deps import AppContext, Context, WritableContext
from bip.api.errors import error_response
from bip.sources.client import SourceError
from bip.sources.service import INTEGRATIONS, SourcesService

router = APIRouter(prefix="/sources", tags=["sources"])
ProviderP = Annotated[str, Path(pattern=r"^[a-z0-9\-]{2,40}$")]
AddressP = Annotated[str, Path(min_length=3, max_length=100)]
_STATUS = {
    "not_configured": 409,
    "no_integration": 404,
    "invalid_input": 422,
    "cap_reached": 429,
    "busy": 409,
}


def _svc(ctx: AppContext) -> SourcesService:
    assert ctx.sources is not None  # noqa: S101 - always set by create_app
    return ctx.sources


def _err(exc: SourceError) -> Any:
    return error_response(_STATUS.get(exc.code, 502), exc.code, str(exc))


@router.get("")
def status(ctx: Context) -> dict[str, Any]:
    return _svc(ctx).status()


@router.post("/{provider_id}/test", response_model=None)
def test(ctx: WritableContext, provider_id: ProviderP) -> Any:
    if provider_id not in INTEGRATIONS:
        return error_response(404, "no_integration", f"{provider_id} has no integration")
    return _svc(ctx).test(provider_id)


@router.get("/prices")
def prices(
    ctx: Context, chain: Annotated[str | None, Query(max_length=32)] = None
) -> dict[str, Any]:
    return _svc(ctx).prices(chain=chain)


@router.post("/prices/refresh", response_model=None)
def refresh_prices(ctx: WritableContext) -> Any:
    try:
        return _svc(ctx).refresh_prices(tuple(ctx.settings.enabled_chains))
    except SourceError as exc:
        return _err(exc)


@router.get("/records")
def records(
    ctx: Context,
    provider: Annotated[str | None, Query(max_length=40)] = None,
    kind: Annotated[str | None, Query(max_length=40)] = None,
    subject: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    return _svc(ctx).records(provider_id=provider, kind=kind, subject=subject, limit=limit)


@router.post("/etherscan/contract/{address}", response_model=None)
def etherscan_contract(ctx: WritableContext, address: AddressP) -> Any:
    try:
        return _svc(ctx).etherscan_contract(address)
    except SourceError as exc:
        return _err(exc)


@router.post("/etherscan/transactions/{address}", response_model=None)
def etherscan_transactions(
    ctx: WritableContext,
    address: AddressP,
    kind: Annotated[Literal["normal", "internal"], Query()] = "normal",
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> Any:
    try:
        return _svc(ctx).etherscan_transactions(address, kind=kind, limit=limit)
    except SourceError as exc:
        return _err(exc)


@router.post("/dune/refresh", response_model=None)
def dune_refresh(ctx: WritableContext) -> Any:
    if not ctx.settings.sources.dune_query_ids:
        return error_response(409, "no_queries", "add saved Dune query ids on the Setup page")
    return _svc(ctx).refresh_dune()
