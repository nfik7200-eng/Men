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
    status_note: str


class ProviderOut(Out):
    id: str
    name: str
    kind: str
    chains: tuple[str, ...]
    implemented_capabilities: tuple[str, ...]
    adapter_implemented: bool
    configuration_status: Literal["not_configured", "configured", "not_required"]
    connection_status: Literal["unavailable"]
    status_reason: str
    research_grade: str
    docs_url: str
    last_check_at: datetime | None
    last_success_at: datetime | None


class ProviderSummary(Out):
    total: int
    configured: int
    adapters_implemented: int
    connected: int


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
    authentication: Literal["not_implemented"]
