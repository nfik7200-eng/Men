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

export type NetworkObservation = {
  chain: string;
  network: string;
  provider_id: string;
  observed_at: string;
  identity: string;
  head_block: number | null;
  head_slot: number | null;
  head_hash: string | null;
  head_time: string | null;
  finalized_block: number | null;
  commitment: string | null;
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
  latest_observation: NetworkObservation | null;
  usable_providers: string[];
  status_note: string;
};

export type Lifecycle =
  | "disabled"
  | "registered"
  | "configured"
  | "adapter_available"
  | "untested"
  | "healthy"
  | "degraded"
  | "unavailable";

export type Provider = {
  id: string;
  name: string;
  operator: string;
  kind: string;
  protocol: string;
  chains: string[];
  network: string | null;
  access: "public" | "credentials";
  lifecycle: Lifecycle;
  enabled: boolean;
  adapter_available: boolean;
  configuration_status: string;
  configuration_note: string;
  endpoint_host: string | null;
  methods: string[];
  archive: string;
  max_log_block_range: number | null;
  rate_limit: string;
  client_rps: number | null;
  limitations: string[];
  docs_url: string;
  terms_url: string;
  sources: string[];
  notes: string | null;
  last_check_at: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  consecutive_failures: number;
  last_latency_ms: number | null;
  last_outcome: string | null;
};

export type HealthCheck = {
  provider_id: string;
  checked_at: string;
  ok: boolean;
  latency_ms: number | null;
  outcome: string;
  detail: string | null;
  observed_network: string | null;
  head_block: number | null;
  head_slot: number | null;
};

export type ProviderDetail = Provider & { recent_checks: HealthCheck[] };

export type IngestionJob = {
  id: number;
  chain: string;
  address: string;
  status: "running" | "succeeded" | "partial" | "failed";
  requested_limit: number;
  started_at: string;
  finished_at: string | null;
  providers: string[];
  rpc_calls: number;
  transactions_seen: number;
  transactions_stored: number;
  transfers_stored: number;
  balance_changes_stored: number;
  error_count: number;
  coverage: Record<string, unknown> | null;
  message: string | null;
  mode: string;
  errors: { occurred_at: string; stage: string; item_ref: string | null; error_code: string; detail: string }[];
};

export type ObservationRow = Record<string, unknown>;
export type ObservationPage = { total: number; limit: number; offset: number; items: ObservationRow[] };

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
  providers: {
    total: number;
    rpc_providers: number;
    configured: number;
    adapters_available: number;
    healthy: number;
    degraded: number;
    unavailable: number;
    untested: number;
    disabled: number;
  };
  ingestion: {
    runs_total: number;
    runs_by_status: Record<string, number>;
    balance_changes_stored: number;
    blocks_stored: number;
  };
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
  ingest_default_limit: number;
  ingest_max_limit: number;
  ethereum_lookback_blocks: number;
  solana_commitment: string;
  rpc_timeout_s: number;
  rpc_max_attempts: number;
};

// ---------------------------------------------------------------- Stage 3: intelligence

/** Where the analysed records came from. "offline_fixture" = deterministic test data. */
export type DataOrigin = "offline_fixture" | "stored_observations";

export type IntelEnvelope = { data_origin: DataOrigin; analyzer_version: string };

export type VerificationSummary = {
  overall: string;
  total: number;
  counts: Record<string, number>;
  corroborated_share: number | null;
};

export type Confidence = {
  value: number;
  label: "low" | "medium" | "high";
  sample_size: number;
  sample_factor: number;
  verification_factor: number;
  method: string;
};

export type Scope = {
  kind: string;
  statement: string;
  first_observed: string | null;
  last_observed: string | null;
  transactions: number;
  transfers: number;
  balance_changes: number;
  truncated: boolean;
  ingestion_runs: number;
  last_ingestion_at: string | null;
};

export type Metric = { id: string; value: number; unit: string | null; label: string };

export type TxEvidence = {
  tx_id: string;
  block_number?: number | null;
  slot?: number | null;
  position?: number | null;
  block_time: string | null;
  provider: string;
  verification: string;
  asset?: string;
  amount_raw?: string;
  event_key?: string;
};

export type Counterparty = {
  address: string;
  transfers: number;
  in: number;
  out: number;
  assets: string[];
  transactions: number;
  first_seen: string | null;
  last_seen: string | null;
  resolved_from_token_account: boolean;
  verification: VerificationSummary;
  relationship: string;
};

export type Interaction = { target: string; kind: string; count: number; tx: string[]; name: string | null };

export type AnalysisRun = {
  run_id: number;
  chain: string;
  address: string;
  analyzer_version: string;
  input_hash: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  reused?: boolean;
  result: { findings?: string[]; anomalies?: { detector_id: string; status: string; severity: string | null }[] } | null;
};

export type FindingClaim = { statement: string; claim_type: string; confidence: number | null; method: string | null };

export type FindingPayload = {
  finding_id: string;
  finding_type: string;
  status: string;
  explanation: string;
  confidence: number | null;
  limitations: string[];
  produced_by: string;
  detected_at: string;
  observation_start: string | null;
  observation_end: string | null;
  claims: FindingClaim[];
  investigation_id?: number | null;
};

export type IntelProfile = IntelEnvelope & {
  chain: string;
  address: string;
  scope: Scope;
  metrics: Metric[];
  volumes: { asset: string; standard: string; in_raw: string; out_raw: string; net_raw: string; in: string | null; out: string | null; net: string | null; in_count: number; out_count: number; unit_note: string | null }[];
  counterparties: { total: number; items: Counterparty[]; truncated: boolean };
  interactions: { total: number; items: Interaction[]; note: string };
  deployments: (TxEvidence & { created_contract: string })[];
  activity: { date: string; transactions: number; transfers_in: number; transfers_out: number }[];
  coverage: Record<string, unknown>;
  verification: VerificationSummary;
  limitations: string[];
  evidence_sample: TxEvidence[];
  latest_analysis: AnalysisRun | null;
  findings: FindingPayload[];
};

export type BehaviourRule = {
  rule_id: string;
  rule_version: string;
  name: string;
  status: "detected" | "not_detected" | "not_enough_data";
  claim_type: string;
  inputs: Record<string, unknown>;
  magnitude: Record<string, unknown> | null;
  supporting_count: number;
  confidence: Confidence | null;
  verification: VerificationSummary;
  explanation: string;
};

export type Behaviour = IntelEnvelope & { items: BehaviourRule[]; note: string; scope: Scope };

export type Anomaly = {
  detector_id: string;
  version: string;
  title: string;
  status: "anomaly" | "no_anomaly" | "insufficient_data" | "could_not_run";
  severity: "low" | "medium" | "high" | null;
  confidence: Confidence | null;
  window: string;
  minimum_data: string;
  baseline: string;
  thresholds: string;
  limitations: string[];
  explanation: string;
  window_start: string | null;
  window_end: string | null;
  observation_id: string;
  finding_id: string | null;
  chain: string;
  address: string;
  verification: VerificationSummary;
  evidence: { total: number; transactions: TxEvidence[]; transfers: TxEvidence[] };
};

export type AnomalyPage = IntelEnvelope & { total: number; limit: number; offset: number; items: Anomaly[] };

export type FlowAsset = {
  asset: string; standard: string; decimals: number | null;
  in_raw: string; out_raw: string; net_raw: string; in: string | null; out: string | null; net: string | null;
  in_count: number; out_count: number; self_count: number;
};

export type Flows = IntelEnvelope & {
  chain: string;
  address: string;
  canonical_source: string;
  window: { since: string | null; until: string | null; first_transfer: string | null; last_transfer: string | null };
  transfers_considered: number;
  assets: FlowAsset[];
  counterparties: { counterparty: string; asset: string; count: number; in_raw: string; out_raw: string; net_raw: string; in: string | null; out: string | null }[];
  time_buckets: { date: string; asset: string; count: number; in_raw: string; out_raw: string; in: string | null; out: string | null }[];
  largest_transfers: Record<string, (TxEvidence & { direction: string; counterparty: string | null; amount: string | null })[]>;
  repeated_paths: { from: string; to: string; asset: string; count: number }[];
  verification: VerificationSummary;
  limitations: string[];
  scope: Scope;
};

export type GraphNode = { id: string; kind: "address" | "contract" | "token" | "program"; depth: number; degree: number; center?: boolean };
export type GraphEdge = {
  id: string; source: string; target: string; kind: string; asset: string | null; count: number;
  amount_raw: string | null; first_seen: string | null; last_seen: string | null;
  evidence: TxEvidence[]; verification: VerificationSummary;
};
export type Graph = IntelEnvelope & {
  chain: string; center: string; depth: number; nodes: GraphNode[]; edges: GraphEdge[];
  limits: { max_depth: number; max_nodes: number; max_edges: number; timeout_s: number };
  truncation: { truncated: boolean; node_limit_reached: boolean; edge_limit_reached: boolean; timed_out: boolean; unexpanded_frontier: number };
  verification: VerificationSummary;
  statement: string;
};

export type Opportunity = {
  candidate_key: string; kind: string; chain: string; subject: string; subject_kind: string;
  label: string; score: number; current: boolean;
  features: Record<string, number>; feature_scores: Record<string, number>; weights: Record<string, number>;
  contributions: Record<string, number>;
  observation_period: { recent_start: string; anchor: string; prior_start: string };
  evidence: { tx_ids: string[]; total: number };
  verification: VerificationSummary;
  coverage: { ingested_wallets: number; events_considered: number };
  uncertainty: string[]; disclaimer: string;
  rule: Record<string, unknown>;
  first_detected_at: string; last_detected_at: string;
};
export type OpportunityPage = IntelEnvelope & { total: number; limit: number; offset: number; items: Opportunity[]; disclaimer: string };
export type OpportunityRefresh = IntelEnvelope & { chain: string; status: string; reason: string | null; candidates: Opportunity[]; ingested_wallets: number; events_considered: number };

export type VerifyResult = IntelEnvelope & {
  tx_id: string; status: "checked" | "could_not_run"; outcome: string | null; label?: string;
  note?: string; reason?: string; independent_providers?: number;
};
export type WalletVerify = IntelEnvelope & { requested: number; checked: number; summary: Record<string, number>; items: VerifyResult[] };

export type IntelStatus = IntelEnvelope & {
  verification: { stored_transactions: number; checked: number; corroborated: number; conflicted: number; invalidated: number; unverified: number; provider_observations: number };
  analysis_runs: number; analysed_wallets: number; anomalies: number; findings: number; opportunity_candidates: number;
};
