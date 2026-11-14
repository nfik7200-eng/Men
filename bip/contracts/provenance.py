"""Provenance metadata carried by every collected or derived record.

Mirrors ``docs/DATA_ARCHITECTURE.md`` §6.1. A provenance record states where data came from and
how far it has been verified. It is never proof of correctness by itself: a record starts as
``observed`` / ``unverified`` and is promoted only by later verification steps.
"""

from __future__ import annotations

import re
from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from bip.contracts.base import ContractModel, NonNegInt, PositiveInt, Slug
from bip.contracts.blockchain import (
    ChainId,
    ChainPosition,
    TxRef,
    require_position_for_chain,
    require_tx_for_chain,
)

CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SourceKind(StrEnum):
    RPC = "rpc"
    DATASET = "dataset"
    INDEXER_API = "indexer_api"
    WEB = "web"
    DERIVED = "derived"


class IngestionMethod(StrEnum):
    HISTORICAL = "historical"
    LIVE = "live"
    ON_DEMAND = "on_demand"
    VERIFICATION = "verification"
    DERIVED = "derived"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    SINGLE_SOURCE = "single_source"
    CROSS_VERIFIED = "cross_verified"
    """Displayed as "corroborated": matching observations from independent providers."""
    CONFLICT = "conflict"
    """Displayed as "conflicted": independent providers or stored evidence disagree."""
    INVALIDATED = "invalidated"
    """Evidence no longer valid (e.g. the block was reorganized away)."""


class Finality(StrEnum):
    OBSERVED = "observed"
    CONFIRMED = "confirmed"
    FINALIZED = "finalized"
    ORPHANED = "orphaned"


VERIFIED_STATUSES = frozenset({VerificationStatus.SINGLE_SOURCE, VerificationStatus.CROSS_VERIFIED})


class RawDataRef(ContractModel):
    """Pointer to the raw payload a record was derived from (e.g. a Bronze Parquet row)."""

    uri: Annotated[str, Field(min_length=1, max_length=1024, pattern=r"^\S+$")]
    row: NonNegInt | None = None
    content_hash: str | None = None
    """Lower-case SHA-256 hex of the raw payload, when computed."""

    @field_validator("content_hash")
    @classmethod
    def _sha256(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.fullmatch(value):
            raise ValueError("content_hash must be lower-case SHA-256 hex (64 characters)")
        return value


class Provenance(ContractModel):
    """Where a record came from, when, how, and how far it is verified."""

    chain: ChainId | None
    """``None`` only for chain-agnostic sources (e.g. a web document)."""
    source_kind: SourceKind
    source_provider: Slug
    source_endpoint_id: Slug | None = None
    source_method: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    request_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    collected_at: AwareDatetime
    event_time: AwareDatetime | None = None
    """Original on-chain or publication time. ``None`` when the source does not provide it."""
    ingestion_method: IngestionMethod
    finality: Finality = Finality.OBSERVED
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    schema_version: PositiveInt
    raw_ref: RawDataRef | None = None
    position: ChainPosition | None = None
    tx: TxRef | None = None

    @model_validator(mode="after")
    def _consistency(self) -> Self:
        if self.chain is None:
            if self.position is not None or self.tx is not None:
                raise ValueError("position and tx require a chain")
        else:
            if self.position is not None:
                require_position_for_chain(self.chain, self.position)
            if self.tx is not None:
                require_tx_for_chain(self.chain, self.tx)
        derived_kind = self.source_kind is SourceKind.DERIVED
        derived_method = self.ingestion_method is IngestionMethod.DERIVED
        if derived_kind != derived_method:
            raise ValueError("source_kind 'derived' and ingestion_method 'derived' go together")
        if (
            self.event_time is not None
            and self.event_time > self.collected_at + CLOCK_SKEW_TOLERANCE
        ):
            raise ValueError("event_time is later than collected_at (beyond clock-skew tolerance)")
        return self


def require_matching_chain(record_chain: str, provenance: Provenance) -> None:
    """Raise ``ValueError`` if a record and its provenance disagree on the chain."""
    if provenance.chain != record_chain:
        raise ValueError(
            f"provenance chain {provenance.chain!r} does not match record chain {record_chain!r}"
        )
