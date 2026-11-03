from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from bip.api.deps import AppContext, Context
from bip.api.schemas import ChainStatus
from bip.contracts.blockchain import get_chain
from bip.providers.catalog import CATALOG

router = APIRouter(tags=["chains"])


def chain_statuses(ctx: AppContext) -> list[ChainStatus]:
    checks = ctx.repo.provider_checks()
    out: list[ChainStatus] = []
    for chain_id in ctx.settings.enabled_chains:
        spec = get_chain(chain_id)
        stats = ctx.repo.chain_data_stats(chain_id)
        successes: list[datetime] = []
        for provider in CATALOG:
            success = checks.get(provider.id, (None, None, None))[1]
            if chain_id in provider.chains and success is not None:
                successes.append(success)
        last_success = max(successes) if successes else None
        tx_count = int(stats["transactions"] or 0)
        out.append(
            ChainStatus(
                chain=chain_id,
                family=spec.family.value,
                network=spec.network,
                position_kind=spec.position_kind,
                configured=True,
                adapter_implemented=False,
                connected=False,
                data_available=tx_count > 0,
                transactions_stored=tx_count,
                latest_stored_block=stats["latest_block"],
                latest_stored_slot=stats["latest_slot"],
                last_successful_retrieval=last_success,
                status_note="Configured only. No chain adapter exists yet, so BIP has never "
                "connected to this chain.",
            )
        )
    return out


@router.get("/chains", response_model=list[ChainStatus])
def list_chains(ctx: Context) -> list[ChainStatus]:
    return chain_statuses(ctx)
