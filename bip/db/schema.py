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
    Float,
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

SCHEMA_VERSION = 11

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


# ============================================================================ v4 tables
# Stage 4: provider capability probing and reliability intelligence. These are diagnostics about
# providers; they never modify chain observations or evidence verification records.

CAPABILITY_STATUSES = ("supported", "unsupported", "inconclusive", "not_tested")
CONNECTIVITY_STATES = (
    "reachable",
    "network_blocked",
    "authentication_required",
    "unauthorized",
    "rate_limited",
    "timeout",
    "tls_error",
    "provider_error",
    "invalid_response",
    "wrong_network",
    "not_tested",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


provider_probe_runs = Table(
    "provider_probe_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_key", String(36), nullable=False, unique=True),
    Column("started_at", UTCDateTime, nullable=False),
    Column("finished_at", UTCDateTime, nullable=True),
    Column("mode", String(16), nullable=False),
    Column("data_origin", String(32), nullable=False),
    Column("providers_json", Text, nullable=False),
    Column("capabilities_json", Text, nullable=False),
    Column("budget_json", Text, nullable=False),
    Column("requests_used", Integer, nullable=False, server_default="0"),
    Column("requests_skipped", Integer, nullable=False, server_default="0"),
    Column("duration_ms", Integer, nullable=True),
    Column("status", String(16), nullable=False),
    Column("summary_json", Text, nullable=True),
    Column("failure_summary", String(500), nullable=True),
    CheckConstraint(_in("status", ("running", "completed", "partial", "failed")), name="status"),
    Index(None, "started_at"),
)
"""One bounded, manually started probe run."""

provider_capability_observations = Table(
    "provider_capability_observations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "run_id",
        Integer,
        ForeignKey("provider_probe_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("provider_id", String(64), nullable=False),
    Column("chain", String(32), nullable=False),
    Column("network", String(32), nullable=True),
    Column("infrastructure", String(40), nullable=True),
    Column("capability", String(48), nullable=False),
    Column("method", String(64), nullable=True),
    Column("status", String(16), nullable=False),
    Column("connectivity", String(32), nullable=False),
    Column("failure_kind", String(40), nullable=True),
    Column("environment_restricted", Boolean, nullable=False, server_default="0"),
    Column("explanation", String(500), nullable=False),
    Column("http_status", Integer, nullable=True),
    Column("rpc_error_code", Integer, nullable=True),
    Column("latency_ms", Integer, nullable=True),
    Column("response_bytes", Integer, nullable=True),
    Column("validation", String(16), nullable=False),
    Column("reference_json", Text, nullable=True),
    Column("observed_at", UTCDateTime, nullable=False),
    CheckConstraint(_in("status", CAPABILITY_STATUSES), name="status"),
    CheckConstraint(_in("connectivity", CONNECTIVITY_STATES), name="connectivity"),
    CheckConstraint(_in("validation", ("passed", "failed", "not_applicable")), name="validation"),
    UniqueConstraint("run_id", "provider_id", "capability"),
    Index(None, "provider_id", "capability", "observed_at"),
)
"""What one probe request (or a deliberate skip) established about one capability."""

provider_reliability_observations = Table(
    "provider_reliability_observations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "run_id",
        Integer,
        ForeignKey("provider_probe_runs.id", ondelete="CASCADE"),
        nullable=True,
    ),
    Column("provider_id", String(64), nullable=False),
    Column("computed_at", UTCDateTime, nullable=False),
    Column("method_version", String(16), nullable=False),
    Column("window_start", UTCDateTime, nullable=True),
    Column("window_end", UTCDateTime, nullable=True),
    Column("state", String(24), nullable=False),
    Column("sample_size", Integer, nullable=False),
    Column("payload_json", Text, nullable=False),
    Index(None, "provider_id", "computed_at"),
)
"""Reliability snapshot computed from capability observations (method, window, sample size)."""

provider_consistency_checks = Table(
    "provider_consistency_checks",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "run_id",
        Integer,
        ForeignKey("provider_probe_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("chain", String(32), nullable=False),
    Column("comparison", String(48), nullable=False),
    Column("provider_a", String(64), nullable=False),
    Column("provider_b", String(64), nullable=False),
    Column("infrastructure_a", String(40), nullable=True),
    Column("infrastructure_b", String(40), nullable=True),
    Column("independent", Boolean, nullable=False),
    Column("reference", String(100), nullable=True),
    Column("value_a", String(200), nullable=True),
    Column("value_b", String(200), nullable=True),
    Column("outcome", String(16), nullable=False),
    Column("note", String(300), nullable=False),
    Column("checked_at", UTCDateTime, nullable=False),
    CheckConstraint(_in("outcome", ("consistent", "conflict", "inconclusive")), name="outcome"),
    Index(None, "chain", "checked_at"),
)
"""Like-for-like comparison of two providers within one probe run (provider diagnostics only)."""

V4_TABLES = (
    provider_probe_runs,
    provider_capability_observations,
    provider_reliability_observations,
    provider_consistency_checks,
)


# ============================================================================ v5 tables
# Stage 5: ML wallet classification and AI research. Derived data only: these tables never
# modify chain observations, evidence verifications or provider observations.

LABEL_STATUSES = (
    "verified",  # strongly attributed by an independently verifiable public source
    "corroborated",  # independently corroborated by two or more sources
    "analyst_reviewed",  # accepted by a human reviewer
    "inferred",  # derived by a deterministic rule from stored evidence
    "weak",  # weak or noisy label (heuristic, third-party list of unknown quality)
    "unknown",
)

wallet_features = Table(
    "wallet_features",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("chain", String(32), nullable=False),
    Column("address", String(64), nullable=False),
    Column("feature_version", String(16), nullable=False),
    Column("as_of", UTCDateTime, nullable=False),
    Column("digest", String(64), nullable=False),
    Column("data_kind", String(24), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    UniqueConstraint("chain", "address", "feature_version", "digest"),
    Index(None, "chain", "address", "as_of"),
)
"""A feature vector snapshot (values, availability, window, coverage)."""

wallet_labels = Table(
    "wallet_labels",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("chain", String(32), nullable=False),
    Column("address", String(64), nullable=False),
    Column("dimension", String(24), nullable=False),
    Column("label", String(48), nullable=False),
    # value: True = the wallet has the label; False = it is a confirmed negative example
    Column("value", Boolean, nullable=False),
    Column("status", String(24), nullable=False),
    Column("source", String(120), nullable=False),
    Column("source_ref", String(500), nullable=True),
    # observed_at: when the label applies (training features use it as as_of: no leakage)
    Column("observed_at", UTCDateTime, nullable=False),
    Column("data_kind", String(24), nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("note", String(500), nullable=True),
    CheckConstraint(_in("status", LABEL_STATUSES), name="status"),
    CheckConstraint(_in("dimension", ("account_type", "behavioural_role")), name="dimension"),
    CheckConstraint(_in("data_kind", ("real", "synthetic")), name="data_kind"),
    UniqueConstraint("chain", "address", "label", "source", "observed_at"),
    Index(None, "chain", "label"),
)
"""Training/evaluation labels with provenance. Model predictions are never written here."""

wallet_label_reviews = Table(
    "wallet_label_reviews",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("label_id", Integer, ForeignKey("wallet_labels.id", ondelete="CASCADE"), nullable=False),
    Column("decision", String(16), nullable=False),
    Column("reviewer", String(80), nullable=False),
    Column("note", String(500), nullable=True),
    Column("reviewed_at", UTCDateTime, nullable=False),
    CheckConstraint(_in("decision", ("accepted", "rejected", "needs_evidence")), name="decision"),
)
"""Append-only reviewer decisions; the original label row is never edited."""

ml_models = Table(
    "ml_models",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(64), nullable=False),
    Column("version", String(32), nullable=False),
    Column("kind", String(24), nullable=False),
    Column("chain", String(32), nullable=False),
    Column("status", String(24), nullable=False),
    Column("feature_version", String(16), nullable=False),
    Column("taxonomy_version", String(16), nullable=False),
    Column("dataset_version", String(64), nullable=True),
    Column("data_kind", String(24), nullable=False),
    Column("artifact_path", String(300), nullable=True),
    Column("artifact_sha256", String(64), nullable=True),
    Column("params_json", Text, nullable=False),
    Column("labels_json", Text, nullable=False),
    Column("thresholds_json", Text, nullable=False),
    Column("training_run_id", Integer, nullable=True),
    Column("created_at", UTCDateTime, nullable=False),
    Column("note", String(500), nullable=True),
    UniqueConstraint("name", "version"),
    CheckConstraint(
        _in(
            "status",
            ("trained", "experimental_synthetic", "rule_based", "insufficient_data", "retired"),
        ),
        name="status",
    ),
)
"""Model registry: every version, with artifact integrity hash and provenance."""

ml_training_runs = Table(
    "ml_training_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("model_name", String(64), nullable=False),
    Column("chain", String(32), nullable=False),
    Column("started_at", UTCDateTime, nullable=False),
    Column("finished_at", UTCDateTime, nullable=True),
    Column("status", String(24), nullable=False),
    Column("data_kind", String(24), nullable=False),
    Column("dataset_version", String(64), nullable=True),
    Column("dataset_json", Text, nullable=True),
    Column("params_json", Text, nullable=False),
    Column("message", String(500), nullable=True),
    CheckConstraint(
        _in("status", ("running", "succeeded", "insufficient_data", "failed")), name="status"
    ),
)

ml_evaluation_results = Table(
    "ml_evaluation_results",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "training_run_id",
        Integer,
        ForeignKey("ml_training_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("model_name", String(64), nullable=False),
    Column("model_version", String(32), nullable=True),
    Column("split", String(24), nullable=False),
    Column("label", String(48), nullable=False),
    Column("data_kind", String(24), nullable=False),
    Column("payload_json", Text, nullable=False),
    Index(None, "model_name", "model_version"),
)
"""Per-label metrics for one split (precision, recall, F1, confusion, calibration)."""

wallet_classifications = Table(
    "wallet_classifications",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("classification_key", String(36), nullable=False, unique=True),
    Column("chain", String(32), nullable=False),
    Column("address", String(64), nullable=False),
    Column("classified_at", UTCDateTime, nullable=False),
    Column("as_of", UTCDateTime, nullable=False),
    Column("feature_version", String(16), nullable=False),
    Column("feature_digest", String(64), nullable=False),
    Column("models_json", Text, nullable=False),
    Column("status", String(24), nullable=False),
    Column("data_origin", String(32), nullable=False),
    Column("payload_json", Text, nullable=False),
    Index(None, "chain", "address", "classified_at"),
)
"""Every classification result (append-only history)."""

RESEARCH_STATES = (
    "queued",
    "planning",
    "collecting_evidence",
    "classifying",
    "analysing",
    "synthesizing",
    "completed",
    "completed_with_limitations",
    "failed",
    "cancelled",
)

research_runs = Table(
    "research_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_key", String(36), nullable=False, unique=True),
    Column("parent_run_key", String(36), nullable=True),
    Column("chain", String(32), nullable=False),
    Column("address", String(64), nullable=False),
    Column("question", String(1000), nullable=False),
    Column("request_json", Text, nullable=False),
    Column("state", String(32), nullable=False),
    Column("progress_json", Text, nullable=True),
    Column("budget_json", Text, nullable=False),
    Column("usage_json", Text, nullable=True),
    Column("data_origin", String(32), nullable=False),
    Column("cancel_requested", Boolean, nullable=False, server_default="0"),
    Column("created_at", UTCDateTime, nullable=False),
    Column("started_at", UTCDateTime, nullable=True),
    Column("finished_at", UTCDateTime, nullable=True),
    Column("error", String(500), nullable=True),
    Column("limitations_json", Text, nullable=True),
    CheckConstraint(_in("state", RESEARCH_STATES), name="state"),
    Index(None, "created_at"),
)

research_plans = Table(
    "research_plans",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False),
    Column("plan_json", Text, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
)

research_tool_calls = Table(
    "research_tool_calls",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("specialist", String(48), nullable=False),
    Column("tool", String(64), nullable=False),
    Column("args_json", Text, nullable=False),
    Column("status", String(16), nullable=False),
    Column("output_json", Text, nullable=True),
    Column("output_bytes", Integer, nullable=True),
    Column("truncated", Boolean, nullable=False, server_default="0"),
    Column("duration_ms", Integer, nullable=True),
    Column("error", String(300), nullable=True),
    Column("created_at", UTCDateTime, nullable=False),
    UniqueConstraint("run_id", "seq"),
)

research_claims = Table(
    "research_claims",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False),
    Column("claim_key", String(24), nullable=False),
    Column("specialist", String(48), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("status", String(24), nullable=False),
    Column("statement", String(1000), nullable=False),
    Column("confidence", String(8), nullable=True),
    Column("evidence_json", Text, nullable=False),
    Column("contradicts_json", Text, nullable=False),
    CheckConstraint(
        _in(
            "status",
            (
                "verified",
                "corroborated",
                "unverified",
                "inferred",
                "conflicted",
                "invalidated",
                "insufficient_evidence",
            ),
        ),
        name="status",
    ),
    UniqueConstraint("run_id", "claim_key"),
)

research_reports = Table(
    "research_reports",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "run_id",
        Integer,
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    Column("report_json", Text, nullable=False),
    Column("markdown", Text, nullable=False),
    Column("bytes", Integer, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
)

research_model_usage = Table(
    "research_model_usage",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("model", String(100), nullable=True),
    Column("purpose", String(48), nullable=False),
    Column("status", String(24), nullable=False),
    Column("prompt_bytes", Integer, nullable=False),
    Column("output_bytes", Integer, nullable=True),
    Column("duration_ms", Integer, nullable=True),
    Column("error", String(300), nullable=True),
    Column("created_at", UTCDateTime, nullable=False),
)
"""LLM call metadata only (sizes, timing, outcome); prompts and outputs are not logged here."""

V5_TABLES = (
    wallet_features,
    wallet_labels,
    wallet_label_reviews,
    ml_models,
    ml_training_runs,
    ml_evaluation_results,
    wallet_classifications,
    research_runs,
    research_plans,
    research_tool_calls,
    research_claims,
    research_reports,
    research_model_usage,
)

# ------------------------------------------------- v6: cases <-> research (ADR-030)

investigation_research_runs = Table(
    "investigation_research_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "investigation_id",
        Integer,
        ForeignKey("investigations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    # research runs are never deleted; deleting a case removes only its links
    Column("research_run_id", Integer, ForeignKey("research_runs.id"), nullable=False),
    Column("note", String(500), nullable=True),
    Column("attached_at", UTCDateTime, nullable=False),
    UniqueConstraint("investigation_id", "research_run_id"),
)

V6_TABLES = (investigation_research_runs,)

# ------------------------------------------------- v7: signals, discovery runs, alerts

signals = Table(
    "signals",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("signal_key", String(160), nullable=False, unique=True),
    Column("detector_id", String(64), nullable=False),
    Column("detector_version", String(16), nullable=False),
    Column("family", String(40), nullable=False),
    Column("chain", String(32), nullable=False),
    Column("subject_kind", String(24), nullable=False),
    Column("subject_ref", String(100), nullable=False),
    Column("title", String(200), nullable=False),
    Column("severity", String(12), nullable=False),
    Column("confidence", Float(asdecimal=False), nullable=False),
    Column("score", Float(asdecimal=False), nullable=False),
    Column("status", String(16), nullable=False),
    Column("explanation_json", Text, nullable=False),
    Column("evidence_json", Text, nullable=False),
    Column("coverage_json", Text, nullable=False),
    Column("observed_at", UTCDateTime, nullable=False),
    Column("first_seen_at", UTCDateTime, nullable=False),
    Column("last_seen_at", UTCDateTime, nullable=False),
    Column("occurrences", Integer, nullable=False),
    Column("expires_at", UTCDateTime, nullable=True),
    Column("status_note", String(300), nullable=True),
    Column("promoted_to", String(60), nullable=True),
    Column("run_id", Integer, nullable=True),
    CheckConstraint(
        "status IN ('open', 'dismissed', 'promoted', 'invalidated', 'expired')",
        name="signal_status",
    ),
    CheckConstraint("severity IN ('low', 'medium', 'high')", name="signal_severity"),
    Index(None, "status", "score"),
    Index(None, "chain", "subject_ref"),
    Index(None, "detector_id", "chain", "subject_ref", "first_seen_at"),
)
"""One deduplicated, scored, explainable candidate. ``signal_key`` is the deduplication key."""

signal_evidence = Table(
    "signal_evidence",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("signal_id", Integer, ForeignKey("signals.id", ondelete="CASCADE"), nullable=False),
    Column("chain", String(32), nullable=False),
    Column("tx_id", String(100), nullable=False),
    UniqueConstraint("signal_id", "chain", "tx_id"),
    Index(None, "chain", "tx_id"),
)
"""Stored transactions a signal rests on, so a reorganisation can invalidate it."""

discovery_runs = Table(
    "discovery_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("started_at", UTCDateTime, nullable=False),
    Column("finished_at", UTCDateTime, nullable=True),
    Column("chain", String(32), nullable=True),
    Column("status", String(16), nullable=False),
    Column("summary_json", Text, nullable=True),
)
"""Freshness and coverage record of each detector pass."""

alert_rules = Table(
    "alert_rules",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(120), nullable=False, unique=True),
    Column("enabled", Boolean, nullable=False),
    Column("min_severity", String(12), nullable=False),
    Column("min_score", Float(asdecimal=False), nullable=False),
    Column("families_json", Text, nullable=False),
    Column("chains_json", Text, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    CheckConstraint("min_severity IN ('low', 'medium', 'high')", name="rule_severity"),
)

alerts = Table(
    "alerts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("rule_id", Integer, ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False),
    Column("signal_id", Integer, ForeignKey("signals.id", ondelete="CASCADE"), nullable=False),
    Column("severity", String(12), nullable=False),
    Column("title", String(200), nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("read_at", UTCDateTime, nullable=True),
    Column("delivery", String(24), nullable=False),
    UniqueConstraint("rule_id", "signal_id"),
    Index(None, "read_at", "created_at"),
)
"""In-app alerts. One per (rule, signal), so a signal never alerts twice for the same rule."""

V7_TABLES = (signals, signal_evidence, discovery_runs, alert_rules, alerts)

# ------------------------------------------------- v8: users, sessions, audit log

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(64), nullable=False, unique=True),
    Column("password_hash", String(255), nullable=False),
    Column("role", String(12), nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("last_login_at", UTCDateTime, nullable=True),
    Column("failed_logins", Integer, nullable=False),
    Column("locked_until", UTCDateTime, nullable=True),
    CheckConstraint("role IN ('viewer', 'analyst', 'admin')", name="user_role"),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("csrf_token", String(64), nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("last_seen_at", UTCDateTime, nullable=False),
    Column("expires_at", UTCDateTime, nullable=False),
    Column("ip", String(64), nullable=True),
    Index(None, "user_id"),
)
"""Server-side sessions. Only a SHA-256 of the bearer token is stored, never the token."""

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("at", UTCDateTime, nullable=False),
    Column("actor", String(64), nullable=True),
    Column("action", String(80), nullable=False),
    Column("target", String(200), nullable=True),
    Column("outcome", String(16), nullable=False),
    Column("ip", String(64), nullable=True),
    Column("detail", String(300), nullable=True),
    Index(None, "at"),
    Index(None, "actor", "at"),
)
"""Security-relevant events and every mutating API call. Never holds request bodies or secrets."""

V8_TABLES = (users, sessions, audit_log)

# ------------------------------------------------- v9: background job history

job_runs = Table(
    "job_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("job", String(40), nullable=False),
    Column("started_at", UTCDateTime, nullable=False),
    Column("finished_at", UTCDateTime, nullable=True),
    Column("status", String(12), nullable=False),
    Column("detail", String(300), nullable=True),
    CheckConstraint("status IN ('running', 'ok', 'error', 'skipped')", name="job_status"),
    Index(None, "job", "started_at"),
)
"""One row per scheduler job execution, so health survives a restart."""

V9_TABLES = (job_runs,)

# ------------------------------------------------- v10: relationships, investigation notes

entity_relationships = Table(
    "entity_relationships",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("chain", String(32), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("source_kind", String(16), nullable=False),
    Column("source_ref", String(100), nullable=False),
    Column("target_kind", String(16), nullable=False),
    Column("target_ref", String(100), nullable=False),
    Column("basis", String(12), nullable=False),
    Column("confidence", Float(asdecimal=False), nullable=False),
    Column("weight", Integer, nullable=False),
    Column("status", String(12), nullable=False),
    Column("note", String(300), nullable=True),
    Column("first_seen_at", UTCDateTime, nullable=False),
    Column("last_seen_at", UTCDateTime, nullable=False),
    UniqueConstraint("chain", "kind", "source_ref", "target_ref"),
    CheckConstraint(
        "kind IN ('funded_by', 'deployed_by', 'transferred_to', 'interacted_with')",
        name="relationship_kind",
    ),
    CheckConstraint("basis IN ('observed', 'hypothesis')", name="relationship_basis"),
    CheckConstraint("status IN ('active', 'invalidated')", name="relationship_status"),
    Index(None, "chain", "source_ref"),
    Index(None, "chain", "target_ref"),
)
"""A typed relationship with its basis. ``basis`` is never 'verified': automatic builders only
observe, and a verified attribution has to come from a reviewed source, not from this table."""

relationship_evidence = Table(
    "relationship_evidence",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "relationship_id",
        Integer,
        ForeignKey("entity_relationships.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("chain", String(32), nullable=False),
    Column("tx_id", String(100), nullable=False),
    UniqueConstraint("relationship_id", "chain", "tx_id"),
    Index(None, "chain", "tx_id"),
)

investigation_notes = Table(
    "investigation_notes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "investigation_id",
        Integer,
        ForeignKey("investigations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("kind", String(12), nullable=False),
    Column("body", Text, nullable=False),
    Column("author", String(64), nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    CheckConstraint("kind IN ('note', 'hypothesis')", name="note_kind"),
    Index(None, "investigation_id", "created_at"),
)
"""Analyst notes. A hypothesis is a note about something not yet shown; it is stored and shown
separately from findings, which carry evidence."""

V10_TABLES = (entity_relationships, relationship_evidence, investigation_notes)


# ------------------------------------------------- v11: liquidity pool creation events

pool_events = Table(
    "pool_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("chain", String(32), nullable=False),
    Column("protocol", String(24), nullable=False),
    Column("factory", String(100), nullable=False),
    Column("pool", String(100), nullable=False),
    Column("token0", String(100), nullable=False),
    Column("token1", String(100), nullable=False),
    Column("fee", Integer, nullable=True),
    Column("tx_id", String(100), nullable=False),
    Column("log_index", Integer, nullable=False),
    Column("block_number", Integer, nullable=False),
    Column("block_hash", String(100), nullable=False),
    Column("block_time", UTCDateTime, nullable=False),
    Column("finality", String(12), nullable=False),
    Column("stored_at", UTCDateTime, nullable=False),
    UniqueConstraint("chain", "tx_id", "log_index"),
    CheckConstraint("protocol IN ('v2_like', 'v3_like')", name="pool_protocol"),
    Index(None, "chain", "pool"),
    Index(None, "chain", "block_time"),
)
"""Pool-creation events seen in finalized blocks. ``protocol`` names the event layout that matched,
not a verified DEX: any contract can emit it, so a pool is an observation about an event."""

V11_TABLES = (pool_events,)
