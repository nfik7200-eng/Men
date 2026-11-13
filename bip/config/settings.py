"""Typed, validated settings loaded from ``BIP_*`` environment variables.

Nested groups use ``__`` (e.g. ``BIP_ROUTER__CROSS_VERIFICATION_SAMPLE_RATE=0.05``). Settings do
**not** read a ``.env`` file implicitly; for local development run commands through
``uv run --env-file .env …`` so the source of configuration is always explicit.

Secrets (``postgres_dsn``, ``redis_url``) are :class:`pydantic.SecretStr`: they print as
``**********``, are registered for log redaction on load, and are never included in
:class:`~bip.core.exceptions.ConfigurationError` messages. Provider API keys are *not* settings;
they are referenced by environment-variable name from the provider registry (Stage 2) and read
with :func:`bip.security.secrets.read_secret`.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ByteSize,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from bip.contracts.blockchain import get_chain
from bip.core.exceptions import ConfigurationError
from bip.security.secrets import register_secret


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class RouterFlags(BaseModel):
    """Feature flags for the future Provider Intelligence Router (Stage 3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    live_requests_enabled: bool = False
    """Master switch for real provider requests. No code path uses it yet; it must stay False
    until the capability prober (Stage 4) exists."""
    circuit_breakers_enabled: bool = True
    """Skip a provider in routine routing after ``rpc.circuit_failure_threshold`` consecutive
    failures, for ``rpc.circuit_open_s``. Probes and admin health checks still reach it."""
    cross_verification_sample_rate: Annotated[float, Field(ge=0.0, le=1.0)] = 0.01


_LOCAL_ORIGIN_PREFIXES = ("http://localhost:", "http://127.0.0.1:", "https://")


class ApiSettings(BaseModel):
    """HTTP API settings (``BIP_API__*``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = "127.0.0.1"
    """Bind address. Defaults to loopback; there is no authentication yet (see docs)."""
    port: Annotated[int, Field(ge=1, le=65535)] = 8000
    unix_socket: Path | None = None
    """Serve on this Unix socket instead of a TCP address (``host`` and ``port`` are then unused).

    Only processes allowed to open the socket file can reach the API, so a reverse proxy on the
    same host is the only client, and the client address can be trusted from its
    ``X-Forwarded-For``. A TCP listener on loopback cannot promise that on a shared host."""
    cors_origins: Annotated[tuple[str, ...], NoDecode] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    """Exact origins allowed by CORS. Comma-separated in the environment. ``*`` is rejected."""
    max_request_bytes: Annotated[int, Field(ge=1024, le=10 * 1024 * 1024)] = 64 * 1024

    @field_validator("unix_socket")
    @classmethod
    def _absolute_socket(cls, value: Path | None) -> Path | None:
        if value is not None and (not value.is_absolute() or "\0" in str(value)):
            raise ValueError("unix_socket must be an absolute path")
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("cors_origins")
    @classmethod
    def _exact_origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for origin in value:
            if "*" in origin:
                raise ValueError("wildcard CORS origins are not allowed")
            if not origin.startswith(_LOCAL_ORIGIN_PREFIXES) or origin.endswith("/"):
                raise ValueError(
                    "CORS origins must be exact, e.g. http://localhost:5173 or https://host"
                )
        return value


class DatabaseSettings(BaseModel):
    """Operational SQLite database settings (``BIP_DATABASE__*``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.\-]{1,64}\.sqlite3$")] = "bip.sqlite3"
    """File name inside ``<data_root>/operational/``. Never a path."""
    busy_timeout_ms: Annotated[int, Field(ge=0, le=60_000)] = 5_000


class RpcSettings(BaseModel):
    """Outbound JSON-RPC client limits (``BIP_RPC__*``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeout_s: Annotated[float, Field(gt=0, le=60)] = 10.0
    max_attempts: Annotated[int, Field(ge=1, le=5)] = 3
    backoff_base_s: Annotated[float, Field(ge=0, le=10)] = 0.5
    backoff_cap_s: Annotated[float, Field(ge=0, le=60)] = 8.0
    max_retry_after_s: Annotated[float, Field(ge=0, le=600)] = 60.0
    """Longest Retry-After BIP will honour inline; longer waits mark the provider rate-limited."""
    max_response_bytes: Annotated[int, Field(ge=1024, le=64 * 1024 * 1024)] = 8 * 1024 * 1024
    circuit_failure_threshold: Annotated[int, Field(ge=2, le=50)] = 5
    """Consecutive failed calls after which routine routing stops choosing a provider (its
    circuit opens). Used only when ``router.circuit_breakers_enabled`` is on."""
    circuit_open_s: Annotated[float, Field(ge=10, le=3600)] = 120.0
    """How long an open circuit stays open after the last failure. After that one call is let
    through (half-open): a success closes it, a failure opens it again."""
    daily_request_budgets: dict[str, Annotated[int, Field(ge=1)]] = {}
    """Optional per-provider cap on requests BIP sends per UTC day, keyed by provider id (for
    example ``{"helius-sol": 30000}``). Empty means no cap. Set it from the provider's own
    documented quota; BIP never assumes one. A provider at its cap is skipped in routing."""
    use_env_proxy: bool = False
    """Send requests through HTTPS_PROXY. Disables local IP pinning (the proxy resolves hosts)."""


class IngestSettings(BaseModel):
    """Bounded read-only wallet ingestion (``BIP_INGEST__*``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    default_limit: Annotated[int, Field(ge=1, le=50)] = 20
    max_limit: Annotated[int, Field(ge=1, le=50)] = 50
    ethereum_lookback_blocks: Annotated[int, Field(ge=1, le=50_000)] = 2_000
    """How many recent blocks the Ethereum log scan covers (standard RPC has no address index)."""
    ethereum_log_chunk_blocks: Annotated[int, Field(ge=1, le=10_000)] = 500
    solana_commitment: Literal["confirmed", "finalized"] = "finalized"
    solana_token_accounts_limit: Annotated[int, Field(ge=0, le=50)] = 5
    """Token accounts (SPL Token and Token-2022) scanned per wallet run; 0 disables discovery."""
    solana_placeholder_repair_limit: Annotated[int, Field(ge=0, le=100)] = 10
    """Previously unparsed Solana transactions re-fetched per wallet run."""
    solana_token_account_signature_limit: Annotated[int, Field(ge=1, le=50)] = 10
    """Newest signatures walked per token account and run (bounds the per-run call budget)."""
    ethereum_partial_repair_limit: Annotated[int, Field(ge=0, le=100)] = 10
    """Ethereum transactions stored without a receipt that are re-fetched per wallet run."""
    repair_max_attempts: Annotated[int, Field(ge=1, le=20)] = 5
    """Failed fetches after which a placeholder or partial row is no longer retried."""
    run_deadline_s: Annotated[float, Field(gt=0, le=90)] = 90.0
    max_rpc_calls_per_run: Annotated[int, Field(ge=1, le=150)] = 150

    @model_validator(mode="after")
    def _limits(self) -> Self:
        if self.default_limit > self.max_limit:
            raise ValueError("default_limit must not exceed max_limit")
        return self


class IntelSettings(BaseModel):
    """Wallet intelligence bounds and detector thresholds (``BIP_INTEL__*``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    graph_max_depth: Annotated[int, Field(ge=1, le=3)] = 2
    graph_max_nodes: Annotated[int, Field(ge=10, le=1000)] = 200
    graph_max_edges: Annotated[int, Field(ge=10, le=3000)] = 500
    graph_timeout_s: Annotated[float, Field(gt=0, le=30)] = 5.0
    max_records_per_wallet: Annotated[int, Field(ge=10, le=20_000)] = 5_000
    """Upper bound on stored records loaded into one analysis (larger sets are truncated and
    say so)."""
    flow_max_window_days: Annotated[int, Field(ge=1, le=3650)] = 365
    newly_observed_days: Annotated[int, Field(ge=1, le=90)] = 7
    spike_factor: Annotated[float, Field(ge=1.5, le=50)] = 3.0
    large_transfer_mad_z: Annotated[float, Field(ge=2, le=50)] = 6.0
    burst_count: Annotated[int, Field(ge=3, le=100)] = 5
    burst_window_minutes: Annotated[int, Field(ge=1, le=1440)] = 10
    concentration_share: Annotated[float, Field(gt=0.2, le=1.0)] = 0.5


class AuthSettings(BaseModel):
    """Authentication, sessions and request throttling (``BIP_AUTH__*``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["disabled", "required"] = "disabled"
    """``disabled`` keeps the single-user local prototype behaviour and is only allowed while the
    API is bound to loopback. ``required`` protects every route except the public allow-list."""
    session_ttl_hours: Annotated[int, Field(ge=1, le=24 * 30)] = 12
    idle_timeout_minutes: Annotated[int, Field(ge=1, le=24 * 60)] = 60
    cookie_secure: bool = True
    """Mark the session cookie Secure (HTTPS only). Turn off only for plain-HTTP local testing."""
    login_max_failures: Annotated[int, Field(ge=3, le=100)] = 5
    login_lockout_minutes: Annotated[int, Field(ge=1, le=24 * 60)] = 15
    rate_limit_per_minute: Annotated[int, Field(ge=0, le=100_000)] = 600
    """Requests per client address per minute across the API; 0 turns the limiter off."""
    login_rate_limit_per_minute: Annotated[int, Field(ge=1, le=1000)] = 10
    trust_proxy_headers: bool = False
    """Use ``X-Forwarded-For`` as the client address. Enable only behind a proxy you control."""
    allow_insecure_bind: bool = False
    """Allow a non-loopback bind with ``mode=disabled``. Never enable on an exposed host."""
    audit_retention_days: Annotated[int, Field(ge=7, le=3650)] = 365


class WorkersSettings(BaseModel):
    """Background scheduler (``BIP_WORKERS__*``). Off by default.

    Jobs that only read and write the local database (discovery over stored data, maintenance,
    backups) are always safe. ``watchlist_refresh`` contacts providers, so it has its own switch
    and stays off until the owner approves it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    poll_seconds: Annotated[int, Field(ge=1, le=3600)] = 30
    discovery_interval_minutes: Annotated[int, Field(ge=5, le=24 * 60)] = 60
    maintenance_interval_hours: Annotated[int, Field(ge=1, le=24 * 7)] = 24
    backup_enabled: bool = False
    backup_interval_hours: Annotated[int, Field(ge=1, le=24 * 30)] = 24
    backup_keep: Annotated[int, Field(ge=1, le=365)] = 7
    watchlist_refresh_enabled: bool = False
    """Contacts providers. Requires explicit approval; see docs/OPERATIONS.md."""
    watchlist_refresh_interval_minutes: Annotated[int, Field(ge=5, le=24 * 60)] = 120
    watchlist_refresh_wallets_per_tick: Annotated[int, Field(ge=1, le=20)] = 2
    collector_enabled: bool = False
    """Contacts providers (``chain_scan`` job). Requires explicit approval."""
    collector_interval_minutes: Annotated[int, Field(ge=5, le=24 * 60)] = 30
    solana_collector_enabled: bool = False
    """Contacts providers (``solana_scan`` job). Requires explicit approval."""
    solana_collector_interval_minutes: Annotated[int, Field(ge=1, le=24 * 60)] = 5
    ethereum_backfill_enabled: bool = False
    """Contacts providers (``ethereum_backfill`` job). Requires explicit approval."""
    solana_backfill_enabled: bool = False
    """Contacts providers (``solana_backfill`` job). Requires explicit approval."""
    backfill_interval_minutes: Annotated[int, Field(ge=5, le=24 * 60)] = 60
    gap_recovery_enabled: bool = False
    """Contacts providers (``gap_recovery`` job). Requires explicit approval."""
    gap_recovery_interval_minutes: Annotated[int, Field(ge=5, le=24 * 60)] = 120
    memory_enabled: bool = False
    """Refresh intelligence memory from stored data (``memory_refresh`` job; local only)."""
    memory_interval_minutes: Annotated[int, Field(ge=5, le=24 * 60)] = 60
    expansion_enabled: bool = False
    """Bounded relationship expansion (``discovery_expansion`` job; local only). Off by default."""
    expansion_interval_minutes: Annotated[int, Field(ge=5, le=24 * 60)] = 120
    max_backoff_hours: Annotated[int, Field(ge=1, le=72)] = 24
    closed_signal_retention_days: Annotated[int, Field(ge=1, le=3650)] = 90
    job_history_days: Annotated[int, Field(ge=1, le=3650)] = 90


class CollectorSettings(BaseModel):
    """Bounded Ethereum block collector (``BIP_COLLECTOR__*``).

    Reads full finalized blocks to find activity nobody asked about: contract creations and
    large native transfers. Contacts providers, so it only runs when an admin calls it or the
    owner enables the ``chain_scan`` job (``workers.collector_enabled``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocks_per_run: Annotated[int, Field(ge=1, le=200)] = 20
    """Finalized blocks read per run (one full-block call each)."""
    initial_lookback_blocks: Annotated[int, Field(ge=1, le=1000)] = 20
    """Where the very first run starts, counted back from the finalized head."""
    min_native_value_eth: Annotated[int, Field(ge=1, le=1_000_000)] = 10
    """A top-level native transfer is stored when it moves at least this much ETH."""
    max_receipts_per_run: Annotated[int, Field(ge=1, le=150)] = 40
    """Receipts fetched per run (creations and large transfers each need one)."""
    max_pools_per_run: Annotated[int, Field(ge=0, le=100)] = 10
    """New pool-creation events stored per run (each costs transaction and receipt calls)."""
    token_scan_enabled: bool = True
    """Scan ERC-20 ``Transfer`` logs of the planned range (topic filter only, no address)."""
    max_new_tokens_per_run: Annotated[int, Field(ge=0, le=100)] = 8
    """New tokens whose first transaction is stored as evidence (transaction and receipt calls)."""
    max_token_transfers_per_run: Annotated[int, Field(ge=0, le=5000)] = 500
    """Transfers of tokens with a known pool stored per run (feeds whale and holder detectors)."""
    max_reserve_pools: Annotated[int, Field(ge=0, le=200)] = 100
    """Known v2-style pools whose ``Sync`` events are read per run (one address-filtered call)."""
    max_reserve_updates_per_run: Annotated[int, Field(ge=0, le=2000)] = 500
    max_rpc_calls_per_run: Annotated[int, Field(ge=1, le=150)] = 150
    run_deadline_s: Annotated[float, Field(gt=0, le=90)] = 90.0
    extra_factories: Annotated[tuple[str, ...], NoDecode] = ()
    """Extra pool factories (``0x`` addresses, comma-separated in the environment) the operator
    has verified. Pools from a factory that is neither built in nor listed here are recorded but
    never feed token transfers or reserve watching."""
    hub_pool_threshold: Annotated[int, Field(ge=2, le=1000)] = 25
    """A token that appears in this many stored pools is a hub (a base asset), not a new token:
    its transfers are not stored per event."""
    max_tracked_tokens: Annotated[int, Field(ge=1000, le=5_000_000)] = 500_000
    """Rows in ``token_stats``; new tokens beyond this are counted as a gap, not tracked."""
    max_block_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    token_allowlist: tuple[str, ...] = ()
    """ERC-20 token contracts whose transfers are always stored (live: from the block's Transfer
    scan; backfill: with an address-filtered eth_getLogs). Lower-case 0x addresses chosen by the
    operator. Their transfers still count against ``max_token_transfers_per_run``."""
    """Failed reads of one block (a malformed or oversize response, a hard provider error) before
    it is given up as a recorded gap, so one bad block cannot stall the cursor forever."""

    @field_validator("extra_factories", mode="before")
    @classmethod
    def _split_factories(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(x.strip() for x in value.split(",") if x.strip())
        return value

    @field_validator("extra_factories")
    @classmethod
    def _factory_format(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for v in value:
            if not re.fullmatch(r"0x[0-9a-fA-F]{40}", v):
                raise ValueError("each extra factory must be a 0x-prefixed 20-byte address")
        return tuple(v.lower() for v in value)

    @property
    def feed_call_reserve(self) -> int:
        """Requests kept back for the event feeds: address-less logs may cost one request per
        block for each of the two feeds, plus one reserves call and two per evidence transaction
        (transaction and receipt)."""
        return (
            2 * self.blocks_per_run + 1 + 2 * (self.max_pools_per_run + self.max_new_tokens_per_run)
        )

    @model_validator(mode="after")
    def _budget_fits(self) -> Self:
        need = self.blocks_per_run + 1 + self.max_receipts_per_run + self.feed_call_reserve
        if need > self.max_rpc_calls_per_run:
            raise ValueError(
                f"blocks_per_run + max_receipts_per_run + the feed reserve ({need}) exceeds "
                f"max_rpc_calls_per_run ({self.max_rpc_calls_per_run}); lower a cap"
            )
        return self


class SolanaCollectorSettings(BaseModel):
    """Bounded Solana finalized-slot collector (``BIP_SOLANA_COLLECTOR__*``).

    Reads full finalized blocks, one ``getBlock`` request per slot. Public endpoints rate-limit
    and Solana blocks are large, so each invocation covers only a few seconds of chain; the
    collector says how far it lags and records any range it skips.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocks_per_run: Annotated[int, Field(ge=1, le=100)] = 10
    max_lag_slots: Annotated[int, Field(ge=10, le=1_000_000)] = 2_000
    """When the cursor is further behind than this, jump to the head and record the skipped range
    as a coverage gap instead of reading ever-staler slots."""
    min_sol_transfer: Annotated[int, Field(ge=1, le=10_000_000)] = 500
    """A System Program transfer of at least this many SOL is stored (with its transaction)."""
    max_stored_transactions_per_run: Annotated[int, Field(ge=0, le=500)] = 100
    spl_mint_allowlist: tuple[str, ...] = ()
    """SPL token mints whose transfers are stored (Token and Token-2022, TransferChecked and
    Transfer). Empty means SPL transfers are not stored: storing every token transfer on mainnet
    is not a goal. Addresses are base58 mints chosen by the operator."""
    max_spl_transfers_per_run: Annotated[int, Field(ge=0, le=2000)] = 200
    """SPL transfers stored per run; any beyond are recorded as an incomplete coverage range."""
    max_slot_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    """Failed reads of one slot before it is given up as a recorded gap (e.g. an oversize block)."""
    max_rpc_calls_per_run: Annotated[int, Field(ge=1, le=150)] = 150
    run_deadline_s: Annotated[float, Field(gt=0, le=90)] = 90.0


class BackfillSettings(BaseModel):
    """Bounded historical backfill and gap recovery (``BIP_BACKFILL__*``).

    Each run reads a small window of blocks or slots *backward* from the historical checkpoint,
    toward a boundary set once from the target period. The per-day rates are estimates (12 s
    Ethereum slots, about 0.4 s Solana slots) used only to place that boundary; the boundary is
    recorded on the checkpoint, so nothing silently shortens the period later.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ethereum_target_days: Annotated[int, Field(ge=1, le=3650)] = 180
    solana_target_days: Annotated[int, Field(ge=1, le=3650)] = 90
    ethereum_blocks_per_day_estimate: Annotated[int, Field(ge=1, le=100_000)] = 7_200
    solana_slots_per_day_estimate: Annotated[int, Field(ge=1, le=1_000_000)] = 216_000
    blocks_per_run: Annotated[int, Field(ge=1, le=100)] = 10
    slots_per_run: Annotated[int, Field(ge=1, le=100)] = 10
    max_rpc_calls_per_run: Annotated[int, Field(ge=1, le=150)] = 100
    run_deadline_s: Annotated[float, Field(gt=0, le=90)] = 60.0
    max_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    """Failed reads of one position before it is given up as a ``failed`` coverage range."""
    max_live_lag_blocks: Annotated[int, Field(ge=1, le=100_000)] = 100
    max_live_lag_slots: Annotated[int, Field(ge=1, le=1_000_000)] = 3_000
    """Live work has priority: backfill and recovery wait while live is further behind than this
    (as of its last run) or is running."""
    recovery_positions_per_run: Annotated[int, Field(ge=1, le=100)] = 10


class MemorySettings(BaseModel):
    """Intelligence memory and bounded expansion discovery (``BIP_MEMORY__*``). Both read only the
    local database; nothing here contacts a provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_rows_per_source: Annotated[int, Field(ge=10, le=20_000)] = 2_000
    """Source rows (transfers, deployments, pools, signals...) folded into memory per run."""
    max_entities_per_run: Annotated[int, Field(ge=10, le=20_000)] = 500
    """Entities whose aggregate facts (activity, deployer counts) are recomputed per run."""
    expansion_max_depth: Annotated[int, Field(ge=1, le=3)] = 2
    expansion_max_entities: Annotated[int, Field(ge=1, le=500)] = 100
    expansion_max_rows: Annotated[int, Field(ge=10, le=50_000)] = 5_000
    expansion_max_seeds: Annotated[int, Field(ge=1, le=100)] = 20
    expansion_cooldown_days: Annotated[int, Field(ge=1, le=365)] = 7
    """An entity examined by a policy is not examined again by it for this long."""


class DiscoverySettings(BaseModel):
    """Bounds and thresholds of the stored-data detectors (``BIP_DISCOVERY__*``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lookback_hours: Annotated[int, Field(ge=1, le=24 * 30)] = 48
    """Only events newer than this can open a signal."""
    baseline_days: Annotated[int, Field(ge=2, le=365)] = 30
    min_baseline: Annotated[int, Field(ge=5, le=10_000)] = 10
    """Fewest baseline observations a median/MAD detector accepts."""
    mad_threshold: Annotated[float, Field(ge=2, le=50)] = 3.5
    dormant_days: Annotated[int, Field(ge=7, le=3650)] = 90
    burst_min_count: Annotated[int, Field(ge=3, le=10_000)] = 10
    funding_min_recipients: Annotated[int, Field(ge=2, le=1000)] = 3
    funding_window_hours: Annotated[int, Field(ge=1, le=24 * 30)] = 24
    new_token_min_transfers: Annotated[int, Field(ge=1, le=100_000)] = 3
    """A newly seen token needs at least this many counted transfers to open a signal."""
    reserve_change_threshold: Annotated[float, Field(gt=0.01, le=1.0)] = 0.2
    """Relative change of a pool reserve between two stored updates that opens a signal."""
    holder_min_transfers: Annotated[int, Field(ge=5, le=100_000)] = 30
    holder_min_addresses: Annotated[int, Field(ge=3, le=10_000)] = 10
    holder_concentration_share: Annotated[float, Field(gt=0.3, le=1.0)] = 0.6
    """Share of positive net inflow held by the top three addresses that opens a signal."""
    cooldown_seconds: Annotated[int, Field(ge=0, le=7 * 86_400)] = 6 * 3600
    """New signals for the same detector and subject inside this window merge into the earlier
    one instead of opening a new candidate."""
    ttl_days: Annotated[int, Field(ge=1, le=365)] = 7
    max_signals_per_detector: Annotated[int, Field(ge=1, le=1000)] = 50
    max_rows_scanned: Annotated[int, Field(ge=100, le=1_000_000)] = 20_000
    as_of_max_days: Annotated[int, Field(ge=1, le=3650)] = 365
    """Furthest back a manual as-of (historical) discovery run may evaluate."""


class ProbeSettings(BaseModel):
    """Budgets for the provider capability prober (``BIP_PROBE__*``).

    The ``le``/``ge`` bounds are hard safety ceilings: operators can only make probing *more*
    conservative (fewer calls, shorter runs, longer spacing), never more aggressive.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_calls_per_provider: Annotated[int, Field(ge=1, le=8)] = 8
    max_calls_per_run: Annotated[int, Field(ge=1, le=48)] = 48
    max_run_seconds: Annotated[float, Field(gt=0, le=90)] = 90.0
    max_concurrent_providers: Annotated[int, Field(ge=1, le=2)] = 2
    request_timeout_s: Annotated[float, Field(gt=0, le=5)] = 5.0
    retries: Annotated[int, Field(ge=0, le=0)] = 0
    """Always 0: a failed probe request is recorded, never retried within a run."""
    max_response_bytes: Annotated[int, Field(ge=1024, le=1024 * 1024)] = 1024 * 1024
    min_interval_s: Annotated[float, Field(ge=1.0, le=60)] = 1.0
    """Minimum spacing between two requests to one provider (a floor: only larger is allowed)."""
    ethereum_history_lookback_blocks: Annotated[int, Field(ge=128, le=1_000_000)] = 100_000
    """Distance below the head for the single historical-block sample (rounded down to 1,000)."""
    solana_history_lookback_slots: Annotated[int, Field(ge=150, le=10_000_000)] = 1_000_000
    """Distance below the finalized slot for the single historical-block sample."""


class MLSettings(BaseModel):
    """Wallet classification (``BIP_ML__*``). Models are trained only on request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_labelled_wallets: Annotated[int, Field(ge=10, le=100_000)] = 30
    """Minimum labelled wallets (real labels) before any supervised model is trained."""
    min_positives_per_label: Annotated[int, Field(ge=3, le=10_000)] = 5
    """Labels with fewer positive examples are not modelled (reported as insufficient)."""
    min_tx_for_classification: Annotated[int, Field(ge=1, le=1000)] = 3
    """Below this many observed transactions behavioural roles are 'insufficient_evidence'."""
    high_precision_target: Annotated[float, Field(ge=0.8, le=1.0)] = 0.95
    moderate_precision_target: Annotated[float, Field(ge=0.5, le=0.99)] = 0.80
    min_threshold_support: Annotated[int, Field(ge=3, le=10_000)] = 10
    """Validation predictions needed at a threshold before it may be used."""
    allow_synthetic_training: bool = False
    """Allow training on the built-in synthetic dataset (pipeline demonstration only)."""
    max_artifact_bytes: Annotated[int, Field(ge=1024, le=512 * 1024 * 1024)] = 64 * 1024 * 1024
    max_label_import: Annotated[int, Field(ge=1, le=100_000)] = 5_000


class AISettings(BaseModel):
    """AI research (``BIP_AI__*``). No provider is used unless explicitly configured.

    Budget fields are hard ceilings: configuration can only lower them, requests cannot raise
    them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Literal["none", "ollama", "openai_compatible"] = "none"
    ollama_url: str = "http://127.0.0.1:11434"
    """Must be a loopback http(s) URL: BIP only talks to a local Ollama."""
    ollama_model: Annotated[str, Field(max_length=100, pattern=r"^[A-Za-z0-9._:\-/]+$")] | None = (
        None
    )
    """An already-installed model tag. BIP never pulls or modifies Ollama models."""
    api_base_url: str | None = None
    """OpenAI-compatible endpoint (https). Optional; never required."""
    api_model: Annotated[str, Field(max_length=100)] | None = None
    api_key_env: Annotated[str, Field(pattern=r"^BIP_AI_[A-Z0-9_]+$")] = "BIP_AI_API_KEY"
    """Name of the environment variable holding the key (the key itself is never a setting)."""
    request_timeout_s: Annotated[float, Field(gt=0, le=60)] = 45.0
    max_seconds: Annotated[float, Field(gt=0, le=120)] = 120.0
    max_tool_calls: Annotated[int, Field(ge=1, le=20)] = 20
    max_model_calls: Annotated[int, Field(ge=0, le=8)] = 8
    max_tool_output_bytes: Annotated[int, Field(ge=1024, le=100 * 1024)] = 100 * 1024
    max_report_bytes: Annotated[int, Field(ge=1024, le=30 * 1024)] = 30 * 1024
    max_concurrent: Annotated[int, Field(ge=1, le=2)] = 2
    max_model_output_bytes: Annotated[int, Field(ge=1024, le=64 * 1024)] = 16 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BIP_",
        env_nested_delimiter="__",
        env_file=None,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )

    env: Environment = Environment.DEVELOPMENT
    log_level: LogLevel = "INFO"
    log_format: Literal["json", "text"] = "json"

    data_root: Path = Path("var/data")
    """Root of all BIP data. Relative paths resolve against the working directory."""
    temp_dir: Path | None = None
    """Scratch space; defaults to ``<data_root>/tmp``."""
    max_data_bytes: ByteSize = ByteSize(5 * 1024**3)
    """Hard cap on bytes BIP may store under ``data_root`` (ADR-011 prototype cap: 5 GiB)."""
    min_free_bytes: ByteSize = ByteSize(5 * 1024**3)
    """Writes pause when free space on the data filesystem would drop below this."""
    min_free_ratio: Annotated[float, Field(ge=0.0, le=0.5)] = 0.05

    enabled_chains: Annotated[tuple[str, ...], NoDecode] = ("ethereum", "solana")
    """Comma-separated in the environment: ``BIP_ENABLED_CHAINS=ethereum,solana``."""

    router: RouterFlags = RouterFlags()
    api: ApiSettings = ApiSettings()
    database: DatabaseSettings = DatabaseSettings()
    rpc: RpcSettings = RpcSettings()
    ingest: IngestSettings = IngestSettings()
    intel: IntelSettings = IntelSettings()
    discovery: DiscoverySettings = DiscoverySettings()
    collector: CollectorSettings = CollectorSettings()
    backfill: BackfillSettings = BackfillSettings()
    memory: MemorySettings = MemorySettings()
    solana_collector: SolanaCollectorSettings = SolanaCollectorSettings()
    auth: AuthSettings = AuthSettings()
    workers: WorkersSettings = WorkersSettings()
    probe: ProbeSettings = ProbeSettings()
    ml: MLSettings = MLSettings()
    ai: AISettings = AISettings()

    postgres_dsn: SecretStr | None = None
    """Optional. Not needed for unit tests; no database code exists yet."""
    redis_url: SecretStr | None = None

    @field_validator("enabled_chains", mode="before")
    @classmethod
    def _split_chains(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("enabled_chains")
    @classmethod
    def _known_chains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("at least one chain must be enabled")
        if len(set(value)) != len(value):
            raise ValueError("enabled_chains contains duplicates")
        for chain in value:
            get_chain(chain)
        return value

    @field_validator("postgres_dsn")
    @classmethod
    def _postgres_scheme(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().startswith(
            ("postgresql://", "postgres://", "postgresql+psycopg://")
        ):
            raise ValueError("must start with postgresql:// (value not shown)")
        return value

    @field_validator("redis_url")
    @classmethod
    def _redis_scheme(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().startswith(("redis://", "rediss://")):
            raise ValueError("must start with redis:// or rediss:// (value not shown)")
        return value

    @model_validator(mode="after")
    def _environment_rules(self) -> Self:
        if self.env is Environment.PRODUCTION:
            if not self.data_root.is_absolute():
                raise ValueError("data_root must be an absolute path in production")
            if self.log_format != "json":
                raise ValueError("log_format must be 'json' in production")
        if (
            self.temp_dir is not None
            and not self.temp_dir.is_absolute()
            and self.env is Environment.PRODUCTION
        ):
            raise ValueError("temp_dir must be an absolute path in production")
        return self

    @property
    def resolved_data_root(self) -> Path:
        return self.data_root.expanduser().resolve()

    @property
    def resolved_temp_dir(self) -> Path:
        base = self.temp_dir if self.temp_dir is not None else self.data_root / "tmp"
        return base.expanduser().resolve()


def _format_errors(exc: ValidationError) -> str:
    lines: list[str] = []
    for err in exc.errors(include_input=False, include_url=False, include_context=False):
        loc = ".".join(str(part) for part in err["loc"]) or "settings"
        lines.append(f"  - BIP_{loc.upper().replace('.', '__')}: {err['msg']}")
    return "invalid configuration:\n" + "\n".join(lines)


def load_settings(**overrides: Any) -> Settings:
    """Load settings from the environment (plus explicit overrides) or raise a readable error.

    Error messages list variable names and reasons only, never the offending values.
    """
    try:
        settings = Settings(**overrides)
    except ValidationError as exc:
        raise ConfigurationError(_format_errors(exc)) from None
    register_secret(settings.postgres_dsn)
    register_secret(settings.redis_url)
    return settings
