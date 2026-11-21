"""Read models returned by repositories (and reused as API response bodies)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

InvestigationStatus = Literal["open", "in_progress", "closed"]


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class WatchlistEntry(Record):
    id: int
    chain: str
    address: str
    note: str | None
    added_at: datetime


class WatchlistSummary(Record):
    id: int
    name: str
    description: str | None
    entry_count: int
    created_at: datetime
    updated_at: datetime


class Watchlist(WatchlistSummary):
    entries: tuple[WatchlistEntry, ...]


class FindingSummary(Record):
    finding_id: str
    finding_type: str
    status: str
    detected_at: datetime
    investigation_id: int | None
    claim_count: int
    evidence_count: int


class InvestigationSummary(Record):
    id: int
    title: str
    description: str | None
    status: InvestigationStatus
    subject_chain: str | None
    subject_address: str | None
    finding_count: int
    created_at: datetime
    updated_at: datetime


class Investigation(InvestigationSummary):
    findings: tuple[FindingSummary, ...]


class TransactionRecord(Record):
    chain: str
    tx_id: str
    block_number: int | None
    slot: int | None
    block_time: datetime | None
    from_address: str | None
    to_address: str | None
    status: str | None
    source_provider: str
    verification_status: str
    finality: str


class TransferRecord(Record):
    chain: str
    tx_id: str
    event_key: str
    block_number: int | None
    slot: int | None
    standard: str
    token: str | None
    from_address: str | None
    to_address: str | None
    amount_raw: str
    decimals: int | None
    classification: str
    source_provider: str
    verification_status: str


class WalletSummary(Record):
    chain: str
    address: str
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    transaction_count: int
    transfer_count: int


class WalletProfile(WalletSummary):
    first_seen_source: str
    first_seen_ingestion: str
    first_seen_verification: str
    recent_transactions: tuple[TransactionRecord, ...]
    recent_transfers: tuple[TransferRecord, ...]


class ActivityEvent(Record):
    event_type: str
    summary: str
    created_at: datetime
