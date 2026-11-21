from __future__ import annotations

from fastapi import APIRouter

from bip import __version__
from bip.api.deps import Context
from bip.api.schemas import PublicSettings, StorageOut
from bip.core.exceptions import StorageLimitError
from bip.db.schema import SCHEMA_VERSION

router = APIRouter(tags=["system"])


@router.get("/settings", response_model=PublicSettings)
def get_public_settings(ctx: Context) -> PublicSettings:
    """Non-sensitive configuration only: no secrets, credentials or filesystem paths."""
    s = ctx.settings
    try:
        check = ctx.disk_guard.check()
        storage: StorageOut | None = StorageOut(
            bip_used_bytes=check.status.bip_used_bytes,
            max_data_bytes=check.status.max_data_bytes,
            writes_allowed=check.allowed,
            reasons=check.reasons,
        )
    except StorageLimitError:
        storage = None
    return PublicSettings(
        version=__version__,
        environment=s.env.value,
        log_level=s.log_level,
        enabled_chains=s.enabled_chains,
        live_requests_enabled=s.router.live_requests_enabled,
        cross_verification_sample_rate=s.router.cross_verification_sample_rate,
        max_request_bytes=s.api.max_request_bytes,
        cors_origins=s.api.cors_origins,
        database_engine="sqlite",
        database_schema_version=SCHEMA_VERSION,
        storage=storage,
        authentication=s.auth.mode,
        ingest_default_limit=s.ingest.default_limit,
        ingest_max_limit=s.ingest.max_limit,
        ethereum_lookback_blocks=s.ingest.ethereum_lookback_blocks,
        solana_commitment=s.ingest.solana_commitment,
        rpc_timeout_s=s.rpc.timeout_s,
        rpc_max_attempts=s.rpc.max_attempts,
    )
