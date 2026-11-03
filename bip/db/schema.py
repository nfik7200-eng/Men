"""Schema version 1 for the operational database.

Conventions:
- Chain identifiers (tx hash or Solana signature, address, mint) are stored as canonical text,
  normalized by the contracts (EVM lower-case hex, Solana base58 unchanged).
- EVM ``block_number`` and Solana ``slot`` are separate nullable columns, and exactly one is set.
- Raw on-chain amounts are decimal **strings** (uint256 does not fit SQLite INTEGER).
- Provenance is stored as validated JSON of :class:`bip.contracts.provenance.Provenance`.
- Natural-key UNIQUE constraints make repeated inserts of the same record no-ops.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from bip.db.types import UTCDateTime

SCHEMA_VERSION = 1

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

app_metadata = Table(
    "app_metadata",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
)

schema_migrations = Table(
    "schema_migrations",
    metadata,
    Column("version", Integer, primary_key=True),
    Column("applied_at", UTCDateTime, nullable=False),
    Column("description", String(200), nullable=False),
)

wallets = Table(
    "wallets",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("chain", String(32), nullable=False),
    Column("address", String(64), nullable=False),
    Column("first_observed_at", UTCDateTime, nullable=True),
    Column("last_observed_at", UTCDateTime, nullable=True),
    Column("first_provenance_json", Text, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    UniqueConstraint("chain", "address"),
)

transactions = Table(
    "transactions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("chain", String(32), nullable=False),
    Column("tx_id", String(100), nullable=False),
    Column("block_number", Integer, nullable=True),
    Column("slot", Integer, nullable=True),
    Column("block_time", UTCDateTime, nullable=True),
    Column("from_address", String(64), nullable=True),
    Column("to_address", String(64), nullable=True),
    Column("status", String(16), nullable=True),
    Column("provenance_json", Text, nullable=False),
    Column("stored_at", UTCDateTime, nullable=False),
    UniqueConstraint("chain", "tx_id"),
    CheckConstraint("(block_number IS NULL) != (slot IS NULL)", name="one_position"),
    Index(None, "chain", "from_address"),
    Index(None, "chain", "to_address"),
)

transfers = Table(
    "transfers",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("chain", String(32), nullable=False),
    Column("tx_id", String(100), nullable=False),
    Column("event_key", String(32), nullable=False),
    Column("block_number", Integer, nullable=True),
    Column("slot", Integer, nullable=True),
    Column("standard", String(32), nullable=False),
    Column("token", String(64), nullable=True),
    Column("from_address", String(64), nullable=True),
    Column("to_address", String(64), nullable=True),
    Column("amount_raw", String(80), nullable=False),
    Column("decimals", Integer, nullable=True),
    Column("token_id", String(80), nullable=True),
    Column("classification", String(32), nullable=False),
    Column("provenance_json", Text, nullable=False),
    Column("stored_at", UTCDateTime, nullable=False),
    UniqueConstraint("chain", "tx_id", "event_key"),
    CheckConstraint("(block_number IS NULL) != (slot IS NULL)", name="one_position"),
    Index(None, "chain", "from_address"),
    Index(None, "chain", "to_address"),
    Index(None, "chain", "token"),
)

watchlists = Table(
    "watchlists",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(100), nullable=False, unique=True),
    Column("description", Text, nullable=True),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
)

watchlist_entries = Table(
    "watchlist_entries",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "watchlist_id",
        Integer,
        ForeignKey("watchlists.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("chain", String(32), nullable=False),
    Column("address", String(64), nullable=False),
    Column("note", Text, nullable=True),
    Column("added_at", UTCDateTime, nullable=False),
    UniqueConstraint("watchlist_id", "chain", "address"),
    Index(None, "chain", "address"),
)

investigations = Table(
    "investigations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("title", String(200), nullable=False),
    Column("description", Text, nullable=True),
    Column("status", String(16), nullable=False),
    Column("subject_chain", String(32), nullable=True),
    Column("subject_address", String(64), nullable=True),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
    CheckConstraint("status IN ('open', 'in_progress', 'closed')", name="status"),
    CheckConstraint("(subject_chain IS NULL) = (subject_address IS NULL)", name="subject_pair"),
    Index(None, "status"),
)

findings = Table(
    "findings",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "investigation_id",
        Integer,
        ForeignKey("investigations.id", ondelete="CASCADE"),
        nullable=True,
    ),
    Column("finding_id", String(128), nullable=False, unique=True),
    Column("finding_type", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("detected_at", UTCDateTime, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Index(None, "detected_at"),
)

evidence_refs = Table(
    "evidence_refs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("finding_pk", Integer, ForeignKey("findings.id", ondelete="CASCADE"), nullable=False),
    Column("evidence_id", String(128), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("locator", Text, nullable=False),
    Column("verified", Boolean, nullable=False),
    Column("provenance_json", Text, nullable=True),
    UniqueConstraint("finding_pk", "evidence_id"),
)

provider_checks = Table(
    "provider_checks",
    metadata,
    Column("provider_id", String(64), primary_key=True),
    Column("last_check_at", UTCDateTime, nullable=True),
    Column("last_success_at", UTCDateTime, nullable=True),
    Column("last_outcome", String(32), nullable=True),
)
"""Results of real provider checks. Empty until the capability prober (Stage 4) exists."""

activity_log = Table(
    "activity_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("event_type", String(64), nullable=False),
    Column("summary", String(300), nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Index(None, "created_at"),
)
"""Append-only record of user actions (watchlist and investigation changes)."""
