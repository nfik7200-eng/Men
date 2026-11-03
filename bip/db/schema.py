"""Operational database schema (current version: 2).

Table objects describe the *current* schema and are used for queries. The DDL that creates them
lives in versioned migrations (``bip/db/engine.py``): v1 is frozen SQL, v2+ are explicit steps.

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
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from bip.db.types import UTCDateTime

SCHEMA_VERSION = 3

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
    # --- v2 ---
    Column("network", String(32), nullable=True),
    Column("block_hash", String(100), nullable=True),
    Column("finality", String(16), nullable=True),
    Column("parse_status", String(16), nullable=False, server_default="complete"),
    Column("value_raw", String(80), nullable=True),
    Column("fee_raw", String(80), nullable=True),
    Column("gas_used", String(80), nullable=True),
    Column("instruction_count", Integer, nullable=True),
    Column("unknown_instruction_count", Integer, nullable=True),
    Column("program_ids_json", Text, nullable=True),
    Column("raw_ref", String(100), nullable=True),
    Column("run_id", Integer, nullable=True),
    # v3: EVM receipt.contractAddress for contract-creation transactions (NULL = none/unknown)
    Column("created_contract", String(64), nullable=True),
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
    # --- v2 ---
    Column("observation_kind", String(40), nullable=False, server_default="unspecified"),
    Column("parse_status", String(16), nullable=False, server_default="complete"),
    Column("block_time", UTCDateTime, nullable=True),
    Column("raw_ref", String(100), nullable=True),
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
    # --- v2 ---
    Column("last_failure_at", UTCDateTime, nullable=True),
    Column("consecutive_failures", Integer, nullable=False, server_default="0"),
    Column("last_latency_ms", Integer, nullable=True),
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


# ============================================================================ v2 tables
# Created by migration v2. Changing them later requires a new migration.

provider_health_checks = Table(
    "provider_health_checks",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("provider_id", String(64), nullable=False),
    Column("checked_at", UTCDateTime, nullable=False),
    Column("ok", Boolean, nullable=False),
    Column("latency_ms", Integer, nullable=True),
    Column("outcome", String(40), nullable=False),
    Column("detail", String(300), nullable=True),
    Column("observed_network", String(100), nullable=True),
    Column("head_block", Integer, nullable=True),
    Column("head_slot", Integer, nullable=True),
    Index(None, "provider_id", "checked_at"),
)
"""Explicit, bounded health checks (API-triggered or live smoke test)."""

provider_overrides = Table(
    "provider_overrides",
    metadata,
    Column("provider_id", String(64), primary_key=True),
    Column("enabled", Boolean, nullable=False),
    Column("reason", String(200), nullable=True),
    Column("updated_at", UTCDateTime, nullable=False),
)
"""Operator enable/disable decisions that override the registry default."""

network_observations = Table(
    "network_observations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("chain", String(32), nullable=False),
    Column("network", String(32), nullable=False),
    Column("provider_id", String(64), nullable=False),
    Column("observed_at", UTCDateTime, nullable=False),
    Column("identity", String(100), nullable=False),
    Column("head_block", Integer, nullable=True),
    Column("head_slot", Integer, nullable=True),
    Column("head_hash", String(100), nullable=True),
    Column("head_time", UTCDateTime, nullable=True),
    Column("finalized_block", Integer, nullable=True),
    Column("commitment", String(16), nullable=True),
    CheckConstraint("(head_block IS NULL) != (head_slot IS NULL)", name="one_head"),
    Index(None, "chain", "observed_at"),
)
"""Verified network identity and head position as reported by one provider at one time."""

blocks = Table(
    "blocks",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("chain", String(32), nullable=False),
    Column("block_number", Integer, nullable=True),
    Column("slot", Integer, nullable=True),
    Column("block_hash", String(100), nullable=False),
    Column("parent_hash", String(100), nullable=True),
    Column("block_time", UTCDateTime, nullable=True),
    Column("tx_count", Integer, nullable=True),
    Column("finality", String(16), nullable=False),
    Column("provenance_json", Text, nullable=False),
    Column("stored_at", UTCDateTime, nullable=False),
    UniqueConstraint("chain", "block_hash"),
    CheckConstraint("(block_number IS NULL) != (slot IS NULL)", name="one_position"),
    Index(None, "chain", "block_number"),
    Index(None, "chain", "slot"),
)

balance_changes = Table(
    "balance_changes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("chain", String(32), nullable=False),
    Column("tx_id", String(100), nullable=False),
    Column("slot", Integer, nullable=True),
    Column("block_number", Integer, nullable=True),
    Column("block_time", UTCDateTime, nullable=True),
    Column("account", String(64), nullable=False),
    Column("owner", String(64), nullable=True),
    Column("mint", String(64), nullable=True),
    Column("observation_kind", String(40), nullable=False),
    Column("pre_raw", String(80), nullable=True),
    Column("post_raw", String(80), nullable=True),
    Column("delta_raw", String(81), nullable=True),
    Column("decimals", Integer, nullable=True),
    Column("parse_status", String(16), nullable=False),
    Column("provenance_json", Text, nullable=False),
    Column("stored_at", UTCDateTime, nullable=False),
    UniqueConstraint("chain", "tx_id", "account", "mint", "observation_kind"),
    Index(None, "chain", "account"),
    Index(None, "chain", "owner"),
)
"""Observed balance changes (Solana pre/post balances). Not transfers: no sender/recipient
pairing is implied."""

raw_payloads = Table(
    "raw_payloads",
    metadata,
    Column("sha256", String(64), primary_key=True),
    Column("chain", String(32), nullable=False),
    Column("provider_id", String(64), nullable=False),
    Column("method", String(64), nullable=False),
    Column("fetched_at", UTCDateTime, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("body_zlib", LargeBinary, nullable=False),
)
"""Compressed raw JSON-RPC results that normalized records reference (provenance raw_ref)."""

ingestion_runs = Table(
    "ingestion_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("chain", String(32), nullable=False),
    Column("address", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("requested_limit", Integer, nullable=False),
    Column("started_at", UTCDateTime, nullable=False),
    Column("finished_at", UTCDateTime, nullable=True),
    Column("providers_json", Text, nullable=False, server_default="[]"),
    Column("rpc_calls", Integer, nullable=False, server_default="0"),
    Column("transactions_seen", Integer, nullable=False, server_default="0"),
    Column("transactions_stored", Integer, nullable=False, server_default="0"),
    Column("transfers_stored", Integer, nullable=False, server_default="0"),
    Column("balance_changes_stored", Integer, nullable=False, server_default="0"),
    Column("error_count", Integer, nullable=False, server_default="0"),
    Column("coverage_json", Text, nullable=True),
    Column("message", String(500), nullable=True),
    CheckConstraint("status IN ('running', 'succeeded', 'partial', 'failed')", name="status"),
    Index(None, "chain", "address"),
    Index(None, "started_at"),
)

ingestion_errors = Table(
    "ingestion_errors",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False),
    Column("occurred_at", UTCDateTime, nullable=False),
    Column("stage", String(40), nullable=False),
    Column("item_ref", String(120), nullable=True),
    Column("error_code", String(40), nullable=False),
    Column("detail", String(500), nullable=False),
)

wallet_cursors = Table(
    "wallet_cursors",
    metadata,
    Column("chain", String(32), primary_key=True),
    Column("address", String(64), primary_key=True),
    Column("newest_tx_id", String(100), nullable=True),
    Column("newest_position", Integer, nullable=True),
    Column("oldest_tx_id", String(100), nullable=True),
    Column("oldest_position", Integer, nullable=True),
    Column("scanned_from_block", Integer, nullable=True),
    Column("scanned_to_block", Integer, nullable=True),
    Column("last_run_id", Integer, nullable=True),
    Column("updated_at", UTCDateTime, nullable=False),
)
"""Per-wallet progress so repeated or interrupted runs resume instead of restarting."""

wallet_activity = Table(
    "wallet_activity",
    metadata,
    Column("chain", String(32), primary_key=True),
    Column("address", String(64), primary_key=True),
    Column("tx_id", String(100), primary_key=True),
    Column("discovered_by", String(40), nullable=False),
    Column("first_run_id", Integer, nullable=True),
    Column("first_seen_at", UTCDateTime, nullable=False),
    Index(None, "chain", "tx_id"),
)
"""Which transactions were discovered for which wallet, and how (signature list, Transfer log).
Links a wallet to transactions even when no transfer or balance change for it was decoded."""

V2_TABLES = (
    provider_health_checks,
    provider_overrides,
    network_observations,
    blocks,
    balance_changes,
    raw_payloads,
    ingestion_runs,
    ingestion_errors,
    wallet_cursors,
    wallet_activity,
)


# ============================================================================ v3 tables
# Stage 3: verification and intelligence. Source observations are never modified by these.

provider_observations = Table(
    "provider_observations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("chain", String(32), nullable=False),
    Column("tx_id", String(100), nullable=False),
    Column("provider_id", String(64), nullable=False),
    Column("infrastructure", String(40), nullable=True),
    Column("fetched_at", UTCDateTime, nullable=False),
    Column("found", Boolean, nullable=False),
    Column("fields_json", Text, nullable=True),
    Column("raw_ref", String(100), nullable=True),
    Column("verification_id", Integer, nullable=True),
    Index(None, "chain", "tx_id"),
)
"""What one provider reported for one transaction during a corroboration check."""

evidence_verifications = Table(
    "evidence_verifications",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("chain", String(32), nullable=False),
    Column("tx_id", String(100), nullable=False),
    Column("checked_at", UTCDateTime, nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("independent_providers", Integer, nullable=False),
    Column("providers_json", Text, nullable=False),
    Column("differences_json", Text, nullable=False),
    Column("note", String(300), nullable=True),
    CheckConstraint(
        "outcome IN ('unverified', 'cross_verified', 'conflict', 'invalidated')", name="outcome"
    ),
    Index(None, "chain", "tx_id", "checked_at"),
)
"""Outcome of comparing stored evidence with independent providers. The latest row per
(chain, tx_id) is the evidence's effective verification status."""

analysis_runs = Table(
    "analysis_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("chain", String(32), nullable=False),
    Column("address", String(64), nullable=False),
    Column("analyzer_version", String(32), nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("params_json", Text, nullable=False),
    Column("started_at", UTCDateTime, nullable=False),
    Column("finished_at", UTCDateTime, nullable=True),
    Column("status", String(16), nullable=False),
    Column("result_json", Text, nullable=True),
    Index(None, "chain", "address", "started_at"),
    Index(None, "chain", "address", "input_hash", "analyzer_version"),
)
"""Reproducible analysis snapshots: same inputs + same analyzer version => same result."""

anomaly_observations = Table(
    "anomaly_observations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False),
    Column("chain", String(32), nullable=False),
    Column("address", String(64), nullable=False),
    Column("detector_id", String(64), nullable=False),
    Column("detector_version", String(16), nullable=False),
    Column("status", String(24), nullable=False),
    Column("severity", String(16), nullable=True),
    Column("confidence", String(8), nullable=True),
    Column("window_start", UTCDateTime, nullable=True),
    Column("window_end", UTCDateTime, nullable=True),
    Column("payload_json", Text, nullable=False),
    Column("finding_id", String(128), nullable=True),
    Column("created_at", UTCDateTime, nullable=False),
    Index(None, "chain", "address"),
    Index(None, "status"),
)

opportunity_candidates = Table(
    "opportunity_candidates",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("candidate_key", String(64), nullable=False, unique=True),
    Column("kind", String(48), nullable=False),
    Column("chain", String(32), nullable=False),
    Column("subject", String(100), nullable=False),
    Column("first_detected_at", UTCDateTime, nullable=False),
    Column("last_detected_at", UTCDateTime, nullable=False),
    Column("window_start", UTCDateTime, nullable=True),
    Column("window_end", UTCDateTime, nullable=True),
    Column("score", String(16), nullable=False),
    Column("verification", String(24), nullable=False),
    Column("payload_json", Text, nullable=False),
    Index(None, "chain", "subject"),
)

V3_TABLES = (
    provider_observations,
    evidence_verifications,
    analysis_runs,
    anomaly_observations,
    opportunity_candidates,
)
