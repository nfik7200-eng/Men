"""Request bodies and API-specific response models."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bip.db.records import ActivityEvent, FindingSummary, InvestigationStatus

Name = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^\S(?:.*\S)?$")]
Title = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^\S(?:.*\S)?$")]
Description = Annotated[str, Field(max_length=2000)]
Note = Annotated[str, Field(max_length=500)]
ChainField = Annotated[str, Field(min_length=1, max_length=32)]
AddressField = Annotated[str, Field(min_length=1, max_length=100)]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WatchlistCreate(Body):
    name: Name
    description: Description | None = None


class WatchlistEntryCreate(Body):
    chain: ChainField
    address: AddressField
    note: Note | None = None


class InvestigationCreate(Body):
    title: Title
    description: Description | None = None
    subject_chain: ChainField | None = None
    subject_address: AddressField | None = None

    @model_validator(mode="after")
    def _subject_pair(self) -> Self:
        if (self.subject_chain is None) != (self.subject_address is None):
            raise ValueError("subject_chain and subject_address must be given together")
        return self


class InvestigationUpdate(Body):
    status: InvestigationStatus


class Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class Health(Out):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    database: Literal["ok", "unavailable"]
    storage_writes: Literal["allowed", "refused", "unknown"]
    timestamp: datetime


class NetworkObservationOut(Out):
    chain: str
    network: str
    provider_id: str
    observed_at: datetime
    identity: str
    head_block: int | None
    head_slot: int | None
    head_hash: str | None
    head_time: datetime | None
    finalized_block: int | None
    commitment: str | None


class ChainStatus(Out):
    chain: str
    family: str
    network: str
    position_kind: Literal["block", "slot"]
    configured: bool
    adapter_implemented: bool
    connected: bool
    data_available: bool
    transactions_stored: int
    latest_stored_block: int | None
    latest_stored_slot: int | None
    last_successful_retrieval: datetime | None
    latest_observation: NetworkObservationOut | None
    usable_providers: tuple[str, ...]
    status_note: str


class ProviderOut(Out):
    id: str
    name: str
    operator: str
    kind: str
    protocol: str
    chains: tuple[str, ...]
    network: str | None
    access: Literal["public", "credentials"]
    lifecycle: str
    enabled: bool
    adapter_available: bool
    configuration_status: str
    configuration_note: str
    endpoint_host: str | None
    """Shown only for built-in public endpoints. Credentialed URLs are never exposed."""
    methods: tuple[str, ...]
    archive: str
    max_log_block_range: int | None
    rate_limit: str
    client_rps: float | None
    limitations: tuple[str, ...]
    docs_url: str
    terms_url: str
    sources: tuple[str, ...]
    notes: str | None
    last_check_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    consecutive_failures: int
    last_latency_ms: int | None
    last_outcome: str | None


class HealthCheckOut(Out):
    provider_id: str
    checked_at: datetime
    ok: bool
    latency_ms: int | None
    outcome: str
    detail: str | None
    observed_network: str | None
    head_block: int | None
    head_slot: int | None


class ProviderDetail(ProviderOut):
    recent_checks: tuple[HealthCheckOut, ...]


class ProviderUpdate(Body):
    enabled: bool
    reason: Annotated[str, Field(max_length=200)] | None = None


class ProviderSummary(Out):
    total: int
    rpc_providers: int
    configured: int
    adapters_available: int
    healthy: int
    degraded: int
    unavailable: int
    untested: int
    disabled: int


class IngestionSummary(Out):
    runs_total: int
    runs_by_status: dict[str, int]
    balance_changes_stored: int
    blocks_stored: int


class DashboardSummary(Out):
    generated_at: datetime
    chains: tuple[ChainStatus, ...]
    wallets_stored: int
    watchlisted_addresses: int
    transactions_stored: int
    transfers_stored: int
    watchlists: int
    open_investigations: int
    findings_stored: int
    recent_findings: tuple[FindingSummary, ...]
    recent_activity: tuple[ActivityEvent, ...]
    providers: ProviderSummary
    ingestion: IngestionSummary


class StorageOut(Out):
    bip_used_bytes: int
    max_data_bytes: int
    writes_allowed: bool
    reasons: tuple[str, ...]


class PublicSettings(Out):
    version: str
    environment: str
    log_level: str
    enabled_chains: tuple[str, ...]
    live_requests_enabled: bool
    cross_verification_sample_rate: float
    max_request_bytes: int
    cors_origins: tuple[str, ...]
    database_engine: Literal["sqlite"]
    database_schema_version: int
    storage: StorageOut | None
    authentication: Literal["disabled", "required"]
    """The configured authentication mode (``BIP_AUTH__MODE``)."""
    ingest_default_limit: int
    ingest_max_limit: int
    ethereum_lookback_blocks: int
    solana_commitment: str
    rpc_timeout_s: float
    rpc_max_attempts: int


Severity = Literal["low", "medium", "high"]
RowId = Annotated[int, Field(ge=1, le=2**53)]


class DiscoveryRunRequest(Body):
    chain: ChainField | None = None
    as_of: datetime | None = None
    """Evaluate stored data as it stood at this past time (bounded by
    ``discovery.as_of_max_days``); signals are labelled with it and raise no alerts."""


class SignalDismiss(Body):
    note: Note | None = None


class SignalReview(Body):
    state: Literal[
        "pending_review",
        "investigating",
        "monitoring",
        "confirmed",
        "rejected",
        "duplicate",
        "insufficient_evidence",
        "archived",
        "accepted",  # the pre-v19 name of confirmed, kept as an input alias
    ]
    reason_code: Literal[
        "evidence_sufficient",
        "evidence_insufficient",
        "evidence_contradicted",
        "needs_more_data",
        "false_positive",
        "duplicate",
        "known_entity",
        "out_of_scope",
        "dismissed",
        "promoted_to_watchlist",
        "promoted_to_investigation",
        "reopened",
        "dispute_resolved",
        "other",
    ]
    note: Note | None = None
    duplicate_of: RowId | None = None
    """Required for, and only for, ``duplicate``: the candidate this one duplicates."""
    correction: Note | None = None
    """What the reviewer corrected (free text), kept on the decision."""
    wallet_label: (
        Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[a-z_]+$")] | None
    ) = None
    """A wallet-classifier label id the reviewer assigns with a confirmation."""


class ExpansionRun(Body):
    policy: Literal["shared_neighbourhood", "deployer_network"]


class SignalPromote(Body):
    target: Literal["watchlist", "investigation"]
    watchlist_id: RowId | None = None
    investigation_id: RowId | None = None
    note: Note | None = None

    @model_validator(mode="after")
    def _target_id(self) -> Self:
        if self.target == "watchlist" and self.watchlist_id is None:
            raise ValueError("watchlist_id is required when target is 'watchlist'")
        if self.target == "investigation" and self.investigation_id is None:
            raise ValueError("investigation_id is required when target is 'investigation'")
        return self


class AlertRuleCreate(Body):
    name: Name
    min_severity: Severity = "high"
    min_score: Annotated[float, Field(ge=0, le=100)] = 0.0
    families: Annotated[list[str], Field(max_length=20)] = []
    chains: Annotated[list[str], Field(max_length=10)] = []
    enabled: bool = True


class AlertRuleUpdate(Body):
    enabled: bool | None = None
    min_severity: Severity | None = None
    min_score: Annotated[float, Field(ge=0, le=100)] | None = None
    families: Annotated[list[str], Field(max_length=20)] | None = None
    chains: Annotated[list[str], Field(max_length=10)] | None = None


class LoginBody(Body):
    username: Annotated[str, Field(min_length=1, max_length=64)]
    password: Annotated[str, Field(min_length=1, max_length=256)]


class PasswordChange(Body):
    current_password: Annotated[str, Field(min_length=1, max_length=256)]
    new_password: Annotated[str, Field(min_length=1, max_length=256)]


class UserCreate(Body):
    username: Annotated[str, Field(min_length=1, max_length=64)]
    password: Annotated[str, Field(min_length=1, max_length=256)]
    role: Literal["viewer", "analyst", "admin"]


class UserUpdate(Body):
    role: Literal["viewer", "analyst", "admin"] | None = None
    enabled: bool | None = None


class PasswordSet(Body):
    new_password: Annotated[str, Field(min_length=1, max_length=256)]
