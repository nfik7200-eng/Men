from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Path

from bip.api.deps import AppContext, Context, WritableContext
from bip.api.schemas import ChainStatus, NetworkObservationOut
from bip.chains.ethereum.adapter import EthereumAdapter
from bip.chains.identity import EXPECTED_IDENTITY
from bip.chains.solana.adapter import SolanaAdapter
from bip.contracts.blockchain import get_chain
from bip.db.errors import InvalidRecordError, RecordNotFoundError
from bip.db.types import utcnow
from bip.providers.status import all_views

router = APIRouter(tags=["chains"])
ChainPath = Annotated[str, Path(min_length=1, max_length=32)]

CONNECTED_WITHIN = timedelta(minutes=30)
ADAPTER_CHAINS = frozenset({"ethereum", "solana"})


def _observation(ctx: AppContext, chain: str) -> NetworkObservationOut | None:
    row = ctx.ingestor.store.latest_network_observation(chain)
    if row is None:
        return None
    return NetworkObservationOut.model_validate(
        {k: row[k] for k in NetworkObservationOut.model_fields}
    )


def chain_statuses(ctx: AppContext) -> list[ChainStatus]:
    views = all_views(ctx.registry, ctx.provider_state.states(), ctx.router.environ)
    now = utcnow()
    out: list[ChainStatus] = []
    for chain_id in ctx.settings.enabled_chains:
        spec = get_chain(chain_id)
        stats = ctx.repo.chain_data_stats(chain_id)
        chain_views = [v for v in views if chain_id in v.spec.chains and v.spec.has_adapter]
        usable = [v for v in chain_views if v.usable]
        successes: list[datetime] = [
            v.state.last_success_at for v in chain_views if v.state.last_success_at is not None
        ]
        last_success = max(successes) if successes else None
        connected = any(
            v.lifecycle in {"healthy", "degraded"}
            and v.state.last_success_at is not None
            and now - v.state.last_success_at <= CONNECTED_WITHIN
            for v in usable
        )
        tx_count = int(stats["transactions"] or 0)
        if connected:
            note = "A provider answered a verified request within the last 30 minutes."
        elif usable:
            note = "Adapter and endpoint available; no successful request in the last 30 minutes."
        else:
            note = "No usable provider endpoint (none configured or all disabled)."
        out.append(
            ChainStatus(
                chain=chain_id,
                family=spec.family.value,
                network=spec.network,
                position_kind=spec.position_kind,
                configured=True,
                adapter_implemented=chain_id in ADAPTER_CHAINS,
                connected=connected,
                data_available=tx_count > 0,
                transactions_stored=tx_count,
                latest_stored_block=stats["latest_block"],
                latest_stored_slot=stats["latest_slot"],
                last_successful_retrieval=last_success,
                latest_observation=_observation(ctx, chain_id),
                usable_providers=tuple(v.spec.id for v in usable),
                status_note=note,
            )
        )
    return out


def _require_chain(ctx: AppContext, chain: str) -> None:
    try:
        get_chain(chain)
    except ValueError as exc:
        raise InvalidRecordError(str(exc)) from None
    if chain not in ctx.settings.enabled_chains or chain not in ADAPTER_CHAINS:
        raise InvalidRecordError(f"chain {chain!r} is not enabled or has no adapter")


@router.get("/chains", response_model=list[ChainStatus])
def list_chains(ctx: Context) -> list[ChainStatus]:
    return chain_statuses(ctx)


@router.get("/chains/{chain}/network", response_model=NetworkObservationOut)
def get_network(ctx: Context, chain: ChainPath) -> NetworkObservationOut:
    """The most recent *stored* verified network observation (no network request)."""
    _require_chain(ctx, chain)
    obs = _observation(ctx, chain)
    if obs is None:
        raise RecordNotFoundError(f"no network observation stored for {chain} yet")
    return obs


@router.post("/chains/{chain}/latest-block", response_model=NetworkObservationOut)
@router.get("/chains/{chain}/latest-block", response_model=NetworkObservationOut)
def latest_block(ctx: WritableContext, chain: ChainPath) -> NetworkObservationOut:
    """Fetch the current head from an eligible provider (identity-verified), store and return it."""
    _require_chain(ctx, chain)
    store = ctx.ingestor.store
    if chain == "ethereum":
        a = EthereumAdapter(ctx.router, raw_sink=store)
        pair = a.block("latest")
        if pair is None:
            raise RecordNotFoundError("provider returned no latest block")
        head, src = pair
        fin = a.finalized_block()
        store.record_network_observation(
            chain=chain,
            network="mainnet",
            provider_id=src.provider_id,
            identity=EXPECTED_IDENTITY[chain].label,
            head_block=head.number,
            head_hash=head.hash,
            head_time=head.timestamp,
            finalized_block=fin[0].number if fin else None,
        )
    else:
        s = SolanaAdapter(ctx.router, raw_sink=store)
        commitment = ctx.settings.ingest.solana_commitment
        slot = s.slot(commitment)
        summary = None
        for candidate in (slot, slot - 1, slot - 2):  # the head slot can be skipped
            pair = s.block_summary(candidate, commitment)
            if pair is not None:
                summary = pair[0]
                break
        provider = sorted(s.providers_used)[0]
        store.record_network_observation(
            chain=chain,
            network="mainnet-beta",
            provider_id=provider,
            identity=EXPECTED_IDENTITY[chain].label,
            head_slot=slot,
            head_hash=str(summary["blockhash"]) if summary else None,
            head_time=summary["block_time"]
            if summary and isinstance(summary["block_time"], datetime)
            else None,
            commitment=commitment,
        )
    obs = _observation(ctx, chain)
    assert obs is not None  # noqa: S101 - just stored
    return obs
