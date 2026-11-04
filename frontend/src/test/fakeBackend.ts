// In-memory stand-in for the BIP API, used only by frontend tests. Mirrors response shapes and
// key behaviours (validation errors, duplicates, 204 deletes). Never shipped to the app.
import { vi } from "vitest";

type Json = Record<string, unknown>;
const now = "2026-11-03T12:00:00.000000Z";

export function chain(id: "ethereum" | "solana", observed = false): Json {
  return {
    chain: id, family: id === "ethereum" ? "evm" : "solana", network: id === "ethereum" ? "mainnet" : "mainnet-beta",
    position_kind: id === "ethereum" ? "block" : "slot", configured: true, adapter_implemented: true,
    connected: observed, data_available: false, transactions_stored: 0, latest_stored_block: null,
    latest_stored_slot: null, last_successful_retrieval: observed ? now : null,
    latest_observation: observed
      ? { chain: id, network: "mainnet", provider_id: id === "ethereum" ? "publicnode-eth" : "solana-public", observed_at: now,
          identity: "chain id 1 (Ethereum mainnet)", head_block: id === "ethereum" ? 20000000 : null,
          head_slot: id === "solana" ? 300000000 : null, head_hash: null, head_time: null, finalized_block: null, commitment: null }
      : null,
    usable_providers: [id === "ethereum" ? "publicnode-eth" : "solana-public"],
    status_note: "Adapter and endpoint available.",
  };
}

export function provider(id: string, lifecycle = "untested"): Json {
  return {
    id, name: id, operator: "op", kind: "rpc", protocol: "evm-jsonrpc", chains: ["ethereum"], network: "mainnet",
    access: "public", lifecycle, enabled: true, adapter_available: true, configuration_status: "not_required",
    configuration_note: "Public endpoint; no credentials needed.", endpoint_host: "ethereum-rpc.publicnode.com",
    methods: ["eth_chainId", "eth_blockNumber"], archive: "unknown", max_log_block_range: null, rate_limit: "unknown",
    client_rps: 4, limitations: ["Archive depth: unknown"], docs_url: "https://example.org/docs", terms_url: "https://example.org/terms",
    sources: [], notes: null, last_check_at: null, last_success_at: lifecycle === "healthy" ? now : null,
    last_failure_at: null, consecutive_failures: 0, last_latency_ms: lifecycle === "healthy" ? 120 : null, last_outcome: null,
  };
}

export const SOL_WALLET = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";

export const ETH_WALLET = "0x" + "aa".repeat(20);
const OTHER = "0x" + "bb".repeat(20);
const TOKEN = "0x" + "70".repeat(20);
const TX = "0x" + "b2".repeat(32);
const scope = { kind: "retrieved_dataset", statement: "Computed only from records BIP retrieved for this wallet in 1 ingestion run(s). This is not the wallet's lifetime history.",
  first_observed: now, last_observed: now, transactions: 2, transfers: 3, balance_changes: 0, truncated: false, ingestion_runs: 1, last_ingestion_at: now };
const verif = { overall: "unverified", total: 2, counts: { unverified: 2 }, corroborated_share: 0 };
const ev = { tx_id: TX, block_number: 19999995, block_time: now, provider: "publicnode-eth", verification: "unverified" };

export type IntelOptions = {
  origin?: "offline_fixture" | "stored_observations";
  leads?: boolean;
  /** Provider probe behaviour: "ok" (fake nodes answer) or "blocked" (environment DNS failure). */
  probe?: "ok" | "blocked";
};

const ENV_NOTE = "Live verification could not be completed because of an environment network restriction. Provider availability remains unknown.";

export function providerIntelResponse(path: string, method: string, opts: IntelOptions, state: { probed: boolean }): Json | null {
  const origin = opts.origin ?? "stored_observations";
  const blocked = opts.probe === "blocked";
  const probedOk = state.probed && !blocked;
  const obs = (cap: string, status: string, connectivity: string, explanation: string, envRestricted = false) => ({
    provider_id: "publicnode-eth", chain: "ethereum", capability: cap, method: cap === "erc721_transfer_logs" ? null : "eth_x",
    status, connectivity, explanation, validation: status === "supported" ? "passed" : "not_applicable",
    failure_kind: envRestricted ? "dns_failure" : null, environment_restricted: envRestricted, http_status: null,
    rpc_error_code: null, latency_ms: status === "supported" ? 120 : null, response_bytes: null, reference: null, observed_at: now,
  });
  const runObs = blocked
    ? [obs("chain_id", "inconclusive", "network_blocked", `DNS resolution failed. ${ENV_NOTE}`, true),
       obs("latest_block", "not_tested", "not_tested", "Not tested: the provider could not be reached from this environment.")]
    : [obs("chain_id", "supported", "reachable", "Returned chain id 1 (Ethereum mainnet)."),
       obs("historical_block", "inconclusive", "reachable", "The provider returned null for block 19900000; support is not established."),
       obs("contract_code", "not_tested", "not_tested", "Not tested: per-provider request cap of 8 reached.")];
  const profile = (id: string, chain: string, configured: boolean, auth: boolean) => ({
    id, name: id, operator: "op", kind: "rpc", chain, network: chain === "ethereum" ? "mainnet" : "mainnet-beta",
    infrastructure: id.startsWith("publicnode") ? "allnodes" : id === "infura-eth" ? "consensys" : "solana-labs",
    access: auth ? "credentials" : "public", authentication_required: auth, endpoint_reference: auth ? "BIP_PROVIDER_INFURA_ETH_URL" : "built-in public endpoint",
    enabled: true, configured, configuration_note: configured ? "Public endpoint; no credentials needed." : "Set BIP_PROVIDER_INFURA_ETH_URL to enable this provider.",
    adapter_available: true, lifecycle: "untested", probe_eligible: configured, probe_ineligible_reason: configured ? null : "requires configuration",
    connectivity: !configured ? "authentication_required" : state.probed && id === "publicnode-eth" ? (blocked ? "network_blocked" : "reachable") : "not_tested",
    environment_restricted: state.probed && blocked && id === "publicnode-eth", environment_note: state.probed && blocked && id === "publicnode-eth" ? ENV_NOTE : null,
    last_run: null, last_probe_at: state.probed && id === "publicnode-eth" ? now : null,
    last_successful_probe_at: probedOk && id === "publicnode-eth" ? now : null, last_error: null,
    coverage: { supported: probedOk && id === "publicnode-eth" ? 1 : 0, unsupported: 0, inconclusive: probedOk && id === "publicnode-eth" ? 1 : 0, not_tested: 10, total: 12 },
    historical_coverage: { label: probedOk && id === "publicnode-eth" ? "historical_access_failed" : "insufficient_evidence", archive: "archive_capability_unverified",
      note: "No historical sample has been tested.", sample: null, observed_at: null },
    reliability_state: !(state.probed && id === "publicnode-eth") ? null : blocked ? "unknown" : "insufficient_data",
    advertised_methods: ["eth_chainId"],
    documented: { rate_limit: "unknown", archive: "unknown", max_log_block_range: null, client_rps: 4, client_rps_note: "BIP policy",
      basis: "search_derived", checked: "2026-11-03", sources: [], docs_url: "https://example.org", note: "Documented facts are not verified operational capabilities." },
  });
  const profiles = [profile("publicnode-eth", "ethereum", true, false), profile("solana-public", "solana", true, false), profile("infura-eth", "ethereum", false, true)];
  const run = () => ({
    data_origin: origin, run_id: "11111111-1111-1111-1111-111111111111", started_at: now, finished_at: now, mode: "manual",
    providers: ["publicnode-eth"], capabilities: null, budget: budget(), requests_used: blocked ? 1 : 2, requests_skipped: blocked ? 7 : 0,
    duration_ms: 1200, status: blocked ? "partial" : "completed", failure_summary: blocked ? "the provider could not be reached from this environment" : null,
    summary: { providers: { "publicnode-eth": { chain: "ethereum", connectivity: blocked ? "network_blocked" : "reachable", requests_used: blocked ? 1 : 2,
      stop_reason: blocked ? "the provider could not be reached from this environment" : null, statuses: {}, environment_restricted: blocked } },
      rate_limit_events: 0, statuses: {} },
    observations: runObs, consistency_checks: [],
  });
  const budget = () => ({ max_calls_per_provider: 8, max_calls_per_run: 48, max_run_seconds: 90, max_concurrent_providers: 2,
    request_timeout_s: 5, retries: 0, max_response_bytes: 1048576, min_interval_s: 1 });
  if (path === "/providers/profiles")
    return { data_origin: origin, items: profiles, generated_at: now,
      last_environment_block: state.probed && blocked ? { provider_id: "publicnode-eth", failure_kind: "dns_failure", observed_at: now } : null };
  const m = /^\/providers\/([a-z0-9-]+)\/profile$/.exec(path);
  if (m) {
    const p = profiles.find((x) => x.id === m[1]);
    if (!p) return null;
    return { ...p, data_origin: origin,
      capabilities: [
        { id: "chain_id", label: "Chain identification", method: "eth_chainId", description: "", requires: null, derived_from: null,
          observation: state.probed ? runObs[0] : null },
        { id: "historical_block", label: "Historical block sample", method: "eth_getBlockByNumber", description: "", requires: null, derived_from: null,
          observation: probedOk ? runObs[1] : null },
        { id: "contract_code", label: "Contract bytecode", method: "eth_getCode", description: "", requires: null, derived_from: null, observation: null },
      ],
      reliability: { provider_id: p.id, method: "reliability@1.0.0", state: p.reliability_state ?? "insufficient_data", sample_size: probedOk ? 2 : 0,
        environment_blocked: state.probed && blocked ? 1 : 0, runs: state.probed ? 1 : 0, successes: probedOk ? 2 : 0, failures: 0,
        success_rate: null, rate_limit_events: 0, timeouts: 0, invalid_responses: 0, provider_errors: 0, unauthorized: 0,
        latency: { samples: probedOk ? 1 : 0, median_ms: null, p95_ms: null }, last_success_at: null, last_failure_at: null,
        window: { start: now, end: now, first_observation: null, last_observation: null },
        rules: { min_attempts: 20, min_runs: 3 }, limitations: ["Needs at least 20 attempts across 3 runs."] },
      consistency_checks: [] };
  }
  if (path.startsWith("/providers/probe/preview"))
    return { data_origin: origin, budget: budget(), max_requests: 16, max_duration_s: 90,
      providers: [{ provider_id: "publicnode-eth", chain: "ethereum", infrastructure: "allnodes", authentication_required: false,
        planned: [{ capability: "chain_id", method: "eth_chainId", derived: [] }, { capability: "latest_block", method: "eth_getBlockByNumber", derived: [] }],
        over_budget: ["block_number", "contract_code"], max_requests: 8 }],
      ineligible: [{ provider_id: "infura-eth", reason: "requires configuration: Set BIP_PROVIDER_INFURA_ETH_URL to enable this provider." }],
      known_network_restriction: null, network_note: null, notes: [] };
  if (path === "/providers/probe-runs" && method === "POST") {
    state.probed = true;
    return run();
  }
  if (path.startsWith("/providers/probe-runs"))
    return { data_origin: origin, total: state.probed ? 1 : 0, limit: 10, offset: 0, items: state.probed ? [run()] : [] };
  return null;
}

export function intelResponse(path: string, method: string, opts: IntelOptions): Json | null {
  const env = { data_origin: opts.origin ?? "stored_observations", analyzer_version: "3.0.0" };
  if (path === "/intelligence/status")
    return { ...env, verification: { stored_transactions: 2, checked: 1, corroborated: 1, conflicted: 0, invalidated: 0, unverified: 1, provider_observations: 1 },
      analysis_runs: 1, analysed_wallets: 1, anomalies: 0, findings: 0, opportunity_candidates: opts.leads ? 1 : 0 };
  if (path.endsWith("/profile"))
    return { ...env, chain: "ethereum", address: ETH_WALLET, scope,
      metrics: [{ id: "transactions", value: 2, unit: null, label: "2 transactions observed in retrieved dataset" }],
      volumes: [{ asset: "native", standard: "native", in_raw: "2000000000000000000", out_raw: "0", net_raw: "2000000000000000000", in: "2", out: "0", net: "2", in_count: 1, out_count: 0, unit_note: null }],
      counterparties: { total: 1, truncated: false, items: [{ address: OTHER, transfers: 2, in: 2, out: 0, assets: ["native"], transactions: 1, first_seen: now, last_seen: now,
        resolved_from_token_account: false, verification: verif, relationship: "observed transfer counterparty (no ownership or identity implied)" }] },
      interactions: { total: 1, items: [{ target: TOKEN, kind: "token contract", count: 1, tx: [TX], name: null }], note: "Unknown programs and contracts are left unnamed." },
      deployments: [], activity: [{ date: "2026-11-03", transactions: 2, transfers_in: 2, transfers_out: 1 }], coverage: {}, verification: verif,
      limitations: ["Metrics describe only the retrieved dataset, not lifetime activity."], evidence_sample: [ev], latest_analysis: null, findings: [] };
  if (path.endsWith("/behaviour"))
    return { ...env, scope, note: "Descriptive rules only.", items: [{ rule_id: "high_frequency", rule_version: "1.0.0", name: "High transaction frequency", status: "not_enough_data",
      claim_type: "statistical_observation", inputs: {}, magnitude: null, supporting_count: 0, confidence: null, verification: verif, explanation: "Only 2 timestamped transactions retrieved; at least 10 are needed." }] };
  if (path.startsWith("/intelligence/anomalies"))
    return { ...env, total: 1, limit: 50, offset: 0, items: [{ detector_id: "tx_frequency_spike", version: "1.0.0", title: "Transaction frequency spike", status: "insufficient_data",
      severity: null, confidence: null, window: "", minimum_data: "", baseline: "", thresholds: "", limitations: [], explanation: "2 transactions over 1 days; needs 10 over at least 8 days.",
      window_start: null, window_end: null, observation_id: "obs-1", finding_id: null, chain: "ethereum", address: ETH_WALLET, verification: verif, evidence: { total: 0, transactions: [], transfers: [] } }] };
  if (path.startsWith("/intelligence/analyze/") && method === "POST")
    return { ...env, run_id: 1, chain: "ethereum", address: ETH_WALLET, analyzer_version: "3.0.0", input_hash: "f".repeat(64), started_at: now, finished_at: now, status: "succeeded", reused: false, result: null };
  if (path.startsWith("/intelligence/verification/ethereum/wallet/") && method === "POST")
    return { ...env, requested: 2, checked: 1, summary: { could_not_run: 1 }, items: [] };
  if (path.startsWith("/intelligence/verification/") && method === "POST")
    return { ...env, tx_id: TX, status: "could_not_run", outcome: null, reason: "No provider on independent infrastructure is eligible." };
  if (path.startsWith("/intelligence/flows/"))
    return { ...env, chain: "ethereum", address: ETH_WALLET, canonical_source: "transfers", window: { since: null, until: null, first_transfer: now, last_transfer: now },
      transfers_considered: 3, transfers_without_time: 0, scope, verification: verif, usd_values: null,
      assets: [{ asset: "native", standard: "native", decimals: 18, in_raw: "2000000000000000000", out_raw: "0", net_raw: "2000000000000000000", in: "2", out: "0", net: "2", in_count: 1, out_count: 0, self_count: 0 },
        { asset: TOKEN, standard: "erc20", decimals: null, in_raw: "3000000", out_raw: "1500000", net_raw: "1500000", in: null, out: null, net: null, in_count: 1, out_count: 1, self_count: 0 }],
      counterparties: [{ counterparty: OTHER, asset: "native", standard: "native", decimals: 18, count: 1, in_raw: "2000000000000000000", out_raw: "0", net_raw: "2000000000000000000", in: "2", out: "0" }],
      time_buckets: [{ date: "2026-11-03", asset: "native", count: 1, in_raw: "2000000000000000000", out_raw: "0", in: "2", out: "0" }],
      largest_transfers: { native: [{ ...ev, asset: "native", amount_raw: "2000000000000000000", direction: "in", counterparty: OTHER, amount: "2" }] },
      repeated_paths: [], limitations: ["No USD values: no verified price source is configured."] };
  if (path.startsWith("/intelligence/graph/"))
    return { ...env, chain: "ethereum", center: ETH_WALLET, graph_version: "1.0.0", depth: 1,
      nodes: [{ id: ETH_WALLET, kind: "address", depth: 0, degree: 2, center: true }, { id: OTHER, kind: "address", depth: 1, degree: 1 }, { id: TOKEN, kind: "token", depth: 1, degree: 1 }],
      edges: [{ id: "e0", source: OTHER, target: ETH_WALLET, kind: "native_transfer", asset: "native", count: 1, amount_raw: "2000000000000000000", first_seen: now, last_seen: now, evidence: [{ ...ev, position: 19999995 }], verification: verif },
        { id: "e1", source: ETH_WALLET, target: TOKEN, kind: "token_activity", asset: TOKEN, count: 1, amount_raw: "1500000", first_seen: now, last_seen: now, evidence: [{ ...ev, position: 19999995 }], verification: verif }],
      limits: { max_depth: 1, max_nodes: 200, max_edges: 500, timeout_s: 5 }, truncation: { truncated: false, node_limit_reached: false, edge_limit_reached: false, timed_out: false, unexpanded_frontier: 1 },
      verification: verif, statement: "Edges are observed on-chain interactions from BIP's stored data. They do not establish common ownership, control or identity." };
  const lead = { candidate_key: "a".repeat(64), kind: "rising_token_participation", chain: "ethereum", subject: TOKEN, subject_kind: "token", label: "unverified research lead", score: 0.51, current: true,
    features: { recent_participants: 6, prior_participants: 2, growth_ratio: 3, recent_events: 12, corroborated_share: 0 },
    feature_scores: { participant_growth: 0.53, breadth: 0.6, activity: 0.24, corroboration: 0 }, weights: { participant_growth: 0.4, breadth: 0.3, activity: 0.2, corroboration: 0.1 },
    contributions: { participant_growth: 0.21, breadth: 0.18, activity: 0.05, corroboration: 0 }, observation_period: { recent_start: now, anchor: now, prior_start: now },
    evidence: { tx_ids: [TX], total: 12 }, verification: verif, coverage: { ingested_wallets: 3, events_considered: 25 },
    uncertainty: ["Participation is measured only over wallets someone chose to ingest into BIP (selection bias)."], disclaimer: "Research lead only. Not investment advice; no buy or sell recommendation.",
    rule: { id: "rising_participation", version: "1.0.0" }, first_detected_at: now, last_detected_at: now };
  if (path.startsWith("/intelligence/opportunities/refresh/"))
    return { ...env, chain: path.split("/").pop() ?? "", status: "insufficient_data", reason: "Needs stored events and at least 3 ingested wallets.", candidates: [], ingested_wallets: 1, events_considered: 0 };
  if (path.startsWith("/intelligence/opportunities"))
    return { ...env, total: opts.leads ? 1 : 0, limit: 50, offset: 0, items: opts.leads ? [lead] : [], disclaimer: "Research leads only. Not investment advice." };
  return null;
}

export type ResearchOptions = { llm?: boolean; models?: boolean };

const lr = (label: string, status: string, confidence: number | null, source: string, extra: Json = {}) => ({
  label, status, confidence, source, model_status: source.startsWith("wallet-classifier") ? "experimental_synthetic" : null,
  calibrated: source.startsWith("wallet-classifier") ? true : null, explanation: `${label} explanation`,
  evidence: [{ type: "transaction", ref: TX, note: "stored" }], supporting_features: [], caveat: null, ...extra,
});

export function classificationFixture(): Json {
  return {
    classification_id: "c1c1c1c1-0000-0000-0000-000000000001", wallet_address: ETH_WALLET, chain: "ethereum",
    classification_timestamp: now, as_of: now, status: "classified_with_limitations", taxonomy_version: "1.0.0",
    feature_version: "1.0.0", feature_digest: "d".repeat(64),
    rules: { name: "bip-rules", version: "1.0.0", feature_version: "1.0.0", status: "rule_based", data_kind: "stored_observations" },
    model: { name: "wallet-classifier-ethereum", version: "1.0.0", feature_version: "1.0.0", status: "experimental_synthetic", data_kind: "synthetic" },
    account_type: lr("externally_owned_account", "rule_based", null, "bip-rules@1.0.0"),
    behavioural_roles: [lr("high_frequency_activity", "low_confidence", 0.93, "wallet-classifier-ethereum@1.0.0"),
      lr("automated_bot", "low_confidence", 0.55, "wallet-classifier-ethereum@1.0.0")],
    not_assigned: [], contradictions: [{ between: ["bip-rules:low_activity_or_new", "wallet-classifier-ethereum@1.0.0:high_frequency_activity"], note: "both are kept and neither is preferred automatically." }],
    anomaly: { status: "not_available", reason: "no trained anomaly model" },
    coverage: { transactions_observed: 12, counterparties_observed: 3, observation_period_days: 2, data_completeness: "partial" },
    supporting_evidence_refs: [TX],
    limitations: ["The active model was trained on SYNTHETIC data. Its predictions demonstrate the pipeline only and are not evidence about this wallet."],
    data_origin: "stored_observations",
  };
}

export function researchResponse(path: string, method: string, opts: ResearchOptions, state: { started: boolean; polls: number; cancelled: boolean }): Json | null {
  const llm = opts.llm ?? false;
  if (path.startsWith("/ml/classify/") && method === "POST") return classificationFixture();
  if (path.startsWith("/ml/classifications/")) return { items: [classificationFixture()], total: 1 };
  const model = { name: "wallet-classifier-ethereum", version: "1.0.0", kind: "multilabel_random_forest", chain: "ethereum", status: "experimental_synthetic",
    feature_version: "1.0.0", taxonomy_version: "1.0.0", dataset_version: "e".repeat(64), data_kind: "synthetic", artifact_sha256: "f".repeat(64), params: {},
    labels: { modelled: ["automated_bot"], skipped: {}, summary: { test: { macro_f1: 0.91, weighted_f1: 0.9, labels_evaluated: 1 } } },
    thresholds: { automated_bot: { high: 0.7, moderate: 0.6, basis: "validation split" } }, training_run_id: 1, created_at: now,
    note: "Trained on SYNTHETIC data: demonstrates the pipeline only; metrics are not real-world performance." };
  if (path === "/ml/models") return { items: opts.models ? [model] : [], active: { ethereum: opts.models ? model : null, solana: null } };
  if (path.startsWith("/ml/models/")) return { ...model, evaluation: [{ split: "test", label: "automated_bot", data_kind: "synthetic",
    metrics: { precision: 0.95, recall: 0.9, f1: 0.92, support: 10, n: 48, brier: 0.04, at_high_threshold: { threshold: 0.7, predicted: 9, precision: 1 } } }] };
  if (path.startsWith("/ml/training-runs") && method === "POST") return { status: "insufficient_data", message: "0 labelled wallet snapshot(s) with trainable labels; at least 30 are needed. No model was trained." };
  if (path.startsWith("/ml/training-runs")) return { items: [] };
  if (path === "/research/config")
    return { provider: { name: llm ? "ollama" : "none", model: llm ? "llama3:8b" : null, available: llm, reason: llm ? "Local Ollama model available." : "No AI provider is configured (BIP_AI__PROVIDER=none). Deterministic analysis and ML classification still run." },
      configured: {}, how_to_configure: "Set BIP_AI__PROVIDER=ollama", budget: { max_tool_calls: 20 }, focus_areas: [], tools: [] };
  const budget = { max_seconds: 120, max_tool_calls: 20, max_model_calls: 8, max_tool_output_bytes: 102400, max_report_bytes: 30720, max_concurrent: 2 };
  if (path === "/research/preview")
    return { request: {}, plan: { focus: ["classification", "evidence"], steps: [{ seq: 1, specialist: "classification_analyst", tool: "wallet_classification" },
      { seq: 2, specialist: "evidence_reviewer", tool: "transaction_evidence" }], skipped_over_budget: [], synthesis: "x" }, budget,
      llm: { provider: llm ? "ollama" : "none", model: llm ? "llama3:8b" : null, available: llm, reason: llm ? "ok" : "No AI provider is configured." } };
  const run = (st: string) => ({ run_id: "aaaaaaaa-0000-0000-0000-000000000001", parent_run_id: null, chain: "ethereum", address: ETH_WALLET, question: "q",
    request: { focus: ["classification"], use_llm: true }, state: st, progress: st === "collecting_evidence" ? { step: 1, of: 2, tool: "wallet_classification" } : null,
    budget, usage: st.startsWith("completed") ? { tool_calls: 2, model_calls: llm ? 1 : 0 } : null, data_origin: "stored_observations",
    cancel_requested: state.cancelled, created_at: now, finished_at: null, error: null, limitations: [] });
  if (path === "/research/runs" && method === "POST") { state.started = true; state.polls = 0; return run("queued"); }
  if (path.endsWith("/cancel")) { state.cancelled = true; return { cancel_requested: true }; }
  if (path.endsWith("/report"))
    return { bytes: 1200, markdown: "# report", report: {
      executive_summary: "Deterministic summary: model predictions are inferences.", summary_source: "deterministic", question: "q",
      subject: { chain: "ethereum", address: ETH_WALLET }, classification: { account_type: null, behavioural_roles: [], model: null, status: null },
      sections: {
        observations: [{ claim_key: "c-1", specialist: "flow_analyst", kind: "blockchain_observation", status: "unverified", statement: "Asset native: in 2, out 0.", confidence: null, evidence: [{ type: "transaction", ref: TX }], contradicts: [] }],
        classification: [{ claim_key: "c-2", specialist: "classification_analyst", kind: "model_prediction", status: "inferred", statement: "Behavioural role 'automated_bot' — model prediction, not a verified fact.", confidence: "low", evidence: [{ type: "classification", ref: "c1" }], contradicts: [] }],
        ai_interpretations: [], hypotheses: [{ claim_key: "c-3", specialist: "behaviour_analyst", kind: "hypothesis", status: "insufficient_evidence", statement: "Possible service-like pattern.", confidence: null, evidence: [{ type: "rule", ref: "x" }], contradicts: [] }],
        missing_information: [], signals: [] },
      contradictions: [], claim_status_counts: { unverified: 1, inferred: 1, insufficient_evidence: 1 }, next_steps: ["Corroborate key transactions."],
      limitations: ["No AI model available (No AI provider is configured.). The report contains deterministic analysis and ML classification only."],
      legend: {}, disclaimer: "No identity attribution and no financial advice. Predictions and interpretations are not facts.", evidence_references: ["transaction:" + TX] } };
  if (path.startsWith("/research/runs/")) {
    state.polls += 1;
    if (state.cancelled) return run("cancelled");
    return run(state.polls < 2 ? "collecting_evidence" : "completed_with_limitations");
  }
  return null;
}

export function installFakeBackend(intel: IntelOptions = {}, research: ResearchOptions = {}) {
  let nextId = 1;
  let ethObserved = false;
  let healthy = false;
  let ingested = false;
  const watchlists = new Map<number, { id: number; name: string; description: string | null; entries: Json[] }>();
  const json = (status: number, body?: unknown) =>
    Promise.resolve(new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
  const err = (status: number, code: string, message: string) => json(status, { error: { code, message } });
  const summary = (w: { id: number; name: string; description: string | null; entries: Json[] }) => ({
    id: w.id, name: w.name, description: w.description, entry_count: w.entries.length, created_at: now, updated_at: now,
  });

  const probeState = { probed: false };
  const researchState = { started: false, polls: 0, cancelled: false };
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = url.replace(/^\/api\/v1/, "");
    const method = init?.method ?? "GET";
    const body = init?.body ? (JSON.parse(String(init.body)) as Json) : {};
    if (path.startsWith("/ml/") || path.startsWith("/research/")) {
      const r = researchResponse(path, method, research, researchState);
      return r ? json(method === "POST" && (path.startsWith("/research/runs") && !path.includes("/cancel") || path.startsWith("/ml/classify")) ? (path === "/research/runs" ? 202 : 201) : 200, r) : err(404, "not_found", "Not Found");
    }
    if (path.startsWith("/providers/profiles") || path.startsWith("/providers/probe") || /^\/providers\/[a-z0-9-]+\/profile$/.test(path)) {
      const r = providerIntelResponse(path, method, intel, probeState);
      return r ? json(path === "/providers/probe-runs" && method === "POST" ? 201 : 200, r) : err(404, "not_found", "Not Found");
    }
    if (path.startsWith("/intelligence/")) {
      const r = intelResponse(path, method, intel);
      return r ? json(method === "POST" && path.includes("/analyze/") ? 201 : 200, r) : err(404, "not_found", "Not Found");
    }
    if (path === "/health") return json(200, { status: "ok", version: "0.1.0", environment: "test", database: "ok", storage_writes: "allowed", timestamp: now });
    if (path === "/chains") return json(200, [chain("ethereum", ethObserved), chain("solana")]);
    if (path === "/chains/ethereum/latest-block" && method === "POST") {
      ethObserved = true;
      return json(200, chain("ethereum", true).latest_observation);
    }
    if (path === "/providers") return json(200, [provider("publicnode-eth", healthy ? "healthy" : "untested"), provider("alchemy-eth", "adapter_available")]);
    if (path === "/providers/publicnode-eth/health-check" && method === "POST") {
      healthy = true;
      return json(200, { provider_id: "publicnode-eth", checked_at: now, ok: true, latency_ms: 140, outcome: "ok", detail: null,
        observed_network: "0x1", head_block: 20000000, head_slot: null });
    }
    if (path.startsWith("/ingestion/jobs?")) return json(200, ingested ? [job()] : []);
    if (path.startsWith("/ingestion/wallet/") && method === "POST") {
      ingested = true;
      return json(201, job());
    }
    if (path.startsWith("/wallets/solana/") && path.includes("/transactions"))
      return json(200, ingested ? { total: 1, limit: 50, offset: 0, items: [{ tx_id: "5".repeat(64), slot: 299999990, block_time: null,
        status: "success", fee_raw: "5000", parse_status: "partial", unknown_instruction_count: 1, source_provider: "solana-public",
        verification_status: "unverified", finality: "finalized" }] } : { total: 0, limit: 50, offset: 0, items: [] });
    if (path.includes("/transfers") || path.includes("/balance-changes") || path.includes("/transactions"))
      return json(200, { total: 0, limit: 50, offset: 0, items: [] });
    if (path === "/dashboard/summary")
      return json(200, {
        ingestion: { runs_total: ingested ? 1 : 0, runs_by_status: ingested ? { partial: 1 } : {}, balance_changes_stored: 0, blocks_stored: 0 },
        generated_at: now, chains: [chain("ethereum"), chain("solana")], wallets_stored: 0,
        watchlisted_addresses: 0, transactions_stored: 0, transfers_stored: 0, watchlists: watchlists.size,
        open_investigations: 0, findings_stored: 0, recent_findings: [], recent_activity: [],
        providers: { total: 16, rpc_providers: 10, configured: 2, adapters_available: 10, healthy: healthy ? 1 : 0,
          degraded: 0, unavailable: 0, untested: healthy ? 1 : 2, disabled: 0 },
      });
    if (path.startsWith("/wallets?")) return json(200, { total: 0, limit: 50, offset: 0, items: [] });
    if (path === `/wallets/ethereum/${ETH_WALLET}`)
      return json(200, { chain: "ethereum", address: ETH_WALLET, transaction_count: 2, transfer_count: 3, first_observed_at: now, last_observed_at: now,
        first_seen_source: "publicnode-eth", first_seen_ingestion: "on_demand", first_seen_verification: "unverified", recent_transactions: [], recent_transfers: [] });
    if (path.startsWith("/wallets/")) return err(404, "not_found", "no data is stored for this address");
    if (path === "/watchlists" && method === "GET") return json(200, [...watchlists.values()].map(summary));
    if (path === "/watchlists" && method === "POST") {
      const name = String(body.name);
      if ([...watchlists.values()].some((w) => w.name === name)) return err(409, "conflict", `a watchlist named '${name}' already exists`);
      const w = { id: nextId++, name, description: (body.description as string | null) ?? null, entries: [] };
      watchlists.set(w.id, w);
      return json(201, { ...summary(w), entries: [] });
    }
    const m = /^\/watchlists\/(\d+)(?:\/entries(?:\/(\d+))?)?$/.exec(path);
    if (m) {
      const w = watchlists.get(Number(m[1]));
      if (!w) return err(404, "not_found", "watchlist not found");
      if (path.endsWith("/entries") && method === "POST") {
        const address = String(body.address).toLowerCase();
        if (w.entries.some((e) => e.address === address && e.chain === body.chain))
          return err(409, "conflict", `${String(body.chain)} address ${address} is already on this watchlist`);
        const entry = { id: nextId++, chain: body.chain, address, note: body.note ?? null, added_at: now };
        w.entries.push(entry);
        return json(201, entry);
      }
      if (m[2] && method === "DELETE") {
        w.entries = w.entries.filter((e) => e.id !== Number(m[2]));
        return json(204);
      }
      if (method === "DELETE") {
        watchlists.delete(w.id);
        return json(204);
      }
      return json(200, { ...summary(w), entries: w.entries });
    }
    return err(404, "not_found", "Not Found");
  });
  return { fetchMock, watchlists };
}

function job(): Json {
  return {
    id: 1, chain: "solana", address: SOL_WALLET, status: "partial", requested_limit: 20, started_at: now, finished_at: now,
    providers: ["solana-public"], rpc_calls: 7, transactions_seen: 2, transactions_stored: 1, transfers_stored: 0,
    balance_changes_stored: 0, error_count: 1, coverage: { not_covered: ["signatures older than the bounded window"] },
    message: "1 new transactions, 0 new transfers, 0 new balance changes from 2 seen.", mode: "synchronous",
    errors: [{ occurred_at: now, stage: "sol_tx", item_ref: null, error_code: "not_available", detail: "history not available" }],
  };
}
