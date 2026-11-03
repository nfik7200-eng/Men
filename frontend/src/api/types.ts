// Response types mirroring bip/api/schemas.py and bip/db/records.py.
// Timestamps are ISO-8601 UTC strings. Raw on-chain amounts are decimal strings.

export type Health = {
  status: "ok" | "degraded";
  version: string;
  environment: string;
  database: "ok" | "unavailable";
  storage_writes: "allowed" | "refused" | "unknown";
  timestamp: string;
};

export type ChainStatus = {
  chain: string;
  family: string;
  network: string;
  position_kind: "block" | "slot";
  configured: boolean;
  adapter_implemented: boolean;
  connected: boolean;
  data_available: boolean;
  transactions_stored: number;
  latest_stored_block: number | null;
  latest_stored_slot: number | null;
  last_successful_retrieval: string | null;
  status_note: string;
};

export type Provider = {
  id: string;
  name: string;
  kind: string;
  chains: string[];
  implemented_capabilities: string[];
  adapter_implemented: boolean;
  configuration_status: "not_configured" | "configured" | "not_required";
  connection_status: "unavailable";
  status_reason: string;
  research_grade: string;
  docs_url: string;
  last_check_at: string | null;
  last_success_at: string | null;
};

export type FindingSummary = {
  finding_id: string;
  finding_type: string;
  status: string;
  detected_at: string;
  investigation_id: number | null;
  claim_count: number;
  evidence_count: number;
};

export type ActivityEvent = { event_type: string; summary: string; created_at: string };

export type DashboardSummary = {
  generated_at: string;
  chains: ChainStatus[];
  wallets_stored: number;
  watchlisted_addresses: number;
  transactions_stored: number;
  transfers_stored: number;
  watchlists: number;
  open_investigations: number;
  findings_stored: number;
  recent_findings: FindingSummary[];
  recent_activity: ActivityEvent[];
  providers: { total: number; configured: number; adapters_implemented: number; connected: number };
};

export type WalletSummary = {
  chain: string;
  address: string;
  first_observed_at: string | null;
  last_observed_at: string | null;
  transaction_count: number;
  transfer_count: number;
};

export type TransactionRecord = {
  chain: string;
  tx_id: string;
  block_number: number | null;
  slot: number | null;
  block_time: string | null;
  from_address: string | null;
  to_address: string | null;
  status: string | null;
  source_provider: string;
  verification_status: string;
  finality: string;
};

export type TransferRecord = {
  chain: string;
  tx_id: string;
  event_key: string;
  block_number: number | null;
  slot: number | null;
  standard: string;
  token: string | null;
  from_address: string | null;
  to_address: string | null;
  amount_raw: string;
  decimals: number | null;
  classification: string;
  source_provider: string;
  verification_status: string;
};

export type WalletProfile = WalletSummary & {
  first_seen_source: string;
  first_seen_ingestion: string;
  first_seen_verification: string;
  recent_transactions: TransactionRecord[];
  recent_transfers: TransferRecord[];
};

export type WalletList = { total: number; limit: number; offset: number; items: WalletSummary[] };

export type WatchlistEntry = {
  id: number;
  chain: string;
  address: string;
  note: string | null;
  added_at: string;
};

export type WatchlistSummary = {
  id: number;
  name: string;
  description: string | null;
  entry_count: number;
  created_at: string;
  updated_at: string;
};

export type Watchlist = WatchlistSummary & { entries: WatchlistEntry[] };

export type InvestigationStatus = "open" | "in_progress" | "closed";

export type InvestigationSummary = {
  id: number;
  title: string;
  description: string | null;
  status: InvestigationStatus;
  subject_chain: string | null;
  subject_address: string | null;
  finding_count: number;
  created_at: string;
  updated_at: string;
};

export type Investigation = InvestigationSummary & { findings: FindingSummary[] };

export type PublicSettings = {
  version: string;
  environment: string;
  log_level: string;
  enabled_chains: string[];
  live_requests_enabled: boolean;
  cross_verification_sample_rate: number;
  max_request_bytes: number;
  cors_origins: string[];
  database_engine: "sqlite";
  database_schema_version: number;
  storage: {
    bip_used_bytes: number;
    max_data_bytes: number;
    writes_allowed: boolean;
    reasons: string[];
  } | null;
  authentication: "not_implemented";
};
