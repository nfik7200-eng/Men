from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Path, Query

from bip.api.deps import Context
from bip.api.schemas import Out
from bip.contracts.provenance import Provenance
from bip.db.records import WalletProfile, WalletSummary
from bip.db.repository import canonical_address

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


class ObservationPage(Out):
    total: int
    limit: int
    offset: int
    items: tuple[dict[str, object], ...]


_HIDDEN = {"provenance_json", "id", "run_id"}


def _clean(row: dict[str, object]) -> dict[str, object]:
    out = {k: v for k, v in row.items() if k not in _HIDDEN}
    prov = row.get("provenance_json")
    if isinstance(prov, str):
        p = Provenance.model_validate_json(prov)
        out["source_provider"] = p.source_provider
        out["collected_at"] = p.collected_at
        out["verification_status"] = p.verification_status.value
        out["finality"] = p.finality.value
    program_ids = row.get("program_ids_json")
    if isinstance(program_ids, str):
        out["program_ids"] = json.loads(program_ids)
        out.pop("program_ids_json", None)
    return out


Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


@router.get("/wallets/{chain}/{address}/transactions", response_model=ObservationPage)
def wallet_transactions(
    ctx: Context, chain: str, address: str, limit: Limit = 50, offset: Offset = 0
) -> ObservationPage:
    ref = canonical_address(chain, address)
    rows, total = ctx.ingestor.store.wallet_transactions(ref.chain, ref.address, limit, offset)
    return ObservationPage(
        total=total, limit=limit, offset=offset, items=tuple(_clean(r) for r in rows)
    )


@router.get("/wallets/{chain}/{address}/transfers", response_model=ObservationPage)
def wallet_transfers(
    ctx: Context, chain: str, address: str, limit: Limit = 50, offset: Offset = 0
) -> ObservationPage:
    ref = canonical_address(chain, address)
    rows, total = ctx.ingestor.store.wallet_transfers(ref.chain, ref.address, limit, offset)
    return ObservationPage(
        total=total, limit=limit, offset=offset, items=tuple(_clean(r) for r in rows)
    )


@router.get("/wallets/{chain}/{address}/balance-changes", response_model=ObservationPage)
def wallet_balance_changes(
    ctx: Context, chain: str, address: str, limit: Limit = 50, offset: Offset = 0
) -> ObservationPage:
    ref = canonical_address(chain, address)
    rows, total = ctx.ingestor.store.wallet_balance_changes(ref.chain, ref.address, limit, offset)
    return ObservationPage(
        total=total, limit=limit, offset=offset, items=tuple(_clean(r) for r in rows)
    )
