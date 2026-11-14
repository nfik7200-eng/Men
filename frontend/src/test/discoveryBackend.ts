// Scripted stand-in for the discovery, alert, collector, attribution and system endpoints, layered
// over the in-memory fake backend (the same way authBackend layers sign-in over it). Test code only.
// Every record below is SYNTHETIC: invented identifiers, no real chain data.
import type {
  Alert,
  RankingRunSummary,
  AlertRule,
  CollectorManifest,
  CollectorStatus,
  Detector,
  DiscoveryRunRecord,
  ExpansionOverview,
  Fact,
  MemoryProfile,
  ReviewDecision,
  EntityLabel,
  Freshness,
  LabelEvidence,
  Signal,
  SystemHealth,
} from "../api/types";
import { installFakeBackend } from "./fakeBackend";

const NOW = "2026-11-10T08:00:00.000000+00:00";
export const SYNTHETIC_ADDR = "0x" + "ab".repeat(20);
export const SYNTHETIC_ADDR_2 = "0x" + "cd".repeat(20);
export const SYNTHETIC_TX = "0x" + "11".repeat(32);
export const SYNTHETIC_SOL = "So11111111111111111111111111111111111111112";

export type DiscoveryCall = { method: string; path: string; body: Record<string, unknown> };

export type DiscoveryState = {
  discoveryRuns?: number;
  origin: "stored_observations" | "offline_fixture";
  signals: Signal[];
  alerts: Alert[];
  rules: AlertRule[];
  labels: EntityLabel[];
  detectors: Detector[];
  freshness: Freshness;
  eth: CollectorStatus;
  sol: CollectorStatus;
  system: SystemHealth;
  /** make one route answer an error: key is "METHOD /path" without query */
  failures: Record<string, { status: number; code: string; message: string }>;
  collectResult: CollectorManifest;
  /** review decisions per signal id */
  decisions: Record<number, ReviewDecision[]>;
  /** candidate-ranking evaluations, newest first */
  rankingRuns: RankingRunSummary[];
  /** remembered entities keyed "chain:ref"; anything else is not in memory */
  memory: Record<string, MemoryProfile>;
  runs: DiscoveryRunRecord[];
  expansion: Omit<ExpansionOverview, "data_origin" | "discovery_version" | "runs">;
  calls: DiscoveryCall[];
};

export function makeSignal(over: Partial<Signal> = {}): Signal {
  return {
    id: 1,
    key: "large_transfer:ethereum:synthetic:1",
    detector: { id: "large_transfer", version: "1" },
    family: "large_transfers",
    chain: "ethereum",
    subject: { kind: "token", ref: SYNTHETIC_ADDR },
    title: "SYNTHETIC unusually large transfer",
    severity: "high",
    confidence: 0.62,
    score: 72.5,
    status: "open",
    explanation: {
      statement: "SYNTHETIC statement: a stored transfer is far larger than the baseline.",
      observed: { amount: "1250.5", from: SYNTHETIC_ADDR, to: SYNTHETIC_ADDR_2, nested: { a: 1 } },
      baseline: { observations: 40, robust_z: 7.2 },
      method: "SYNTHETIC method: median/MAD on log10(amount)",
      caveat: "SYNTHETIC caveat: a large transfer says nothing about intent.",
      score_components: [
        { name: "magnitude", value: 0.8, weight: 0.5, contribution: 40, note: "robust z 7.2 against threshold 3" },
        { name: "baseline_reliability", value: 0.8, weight: 0.3, contribution: 24, note: "40 baseline transfers (50 = full)" },
        { name: "evidence_finality", value: 0.43, weight: 0.2, contribution: 8.5, note: "3 of 7 supporting transactions finalized" },
      ],
      scoring: "score = 100 * (0.5*magnitude + 0.3*baseline_reliability + 0.2*evidence_finality)",
    },
    evidence: [{ kind: "transaction", chain: "ethereum", ref: SYNTHETIC_TX }],
    coverage: {
      detector: "large_transfer",
      version: "1",
      coverage: "SYNTHETIC coverage: transfers stored for ingested wallets.",
      limitations: "SYNTHETIC limitations: no internal transfers.",
      refresh: "on each discovery pass",
      cost: "database only",
      rows_scanned: 1200,
      row_cap_reached: false,
      baseline_window_days: 30,
      lookback_hours: 24,
      evidence_finality: { finalized: 3, total: 7 },
      provisional_evidence: true,
    },
    observed_at: "2026-11-09T12:00:00+00:00",
    first_seen_at: "2026-11-09T12:05:00+00:00",
    last_seen_at: "2026-11-09T13:00:00+00:00",
    occurrences: 2,
    expires_at: "2026-11-16T12:05:00+00:00",
    status_note: null,
    promoted_to: null,
    attribution: {
      labels: {},
      note: "Only labels a person approved with evidence are shown. No label means unattributed, not 'not an exchange'.",
    },
    review_state: "new",
    ...over,
  };
}

export function makeFact(over: Partial<Fact> = {}): Fact {
  return {
    id: 1, key: "first_funder", value: { ref: SYNTHETIC_ADDR_2 }, evidence_class: "observed", change: "new", status: "current",
    source: "stored_transfers", source_tx: SYNTHETIC_TX, observed_at: "2026-11-09T12:00:00+00:00", recorded_at: "2026-11-09T12:05:00+00:00",
    confirmed_at: null, confirmations: 1, supersedes_id: null, ...over,
  };
}

export function makeMemory(over: Partial<MemoryProfile> = {}): MemoryProfile {
  return {
    chain: "ethereum", ref: SYNTHETIC_ADDR, in_memory: false, entity: null, counterparties: [], relationships: { items: [], total: 0 }, candidates: [],
    data_origin: "stored_observations", note: "SYNTHETIC note: memory is derived from stored data only.", ...over,
  };
}

export const SYNTHETIC_EXPANSION: DiscoveryState["expansion"] = {
  policies: [
    { id: "shared_neighbourhood", objective: "SYNTHETIC objective: find wallets sharing counterparties", seeds: "SYNTHETIC seeds: recent candidates", stopping: "SYNTHETIC stopping: depth or entity budget" },
    { id: "deployer_network", objective: "SYNTHETIC objective: contracts from one deployer", seeds: "SYNTHETIC seeds: new deployments", stopping: "SYNTHETIC stopping: row budget" },
  ],
  budgets: { max_depth: 2, max_entities: 200, max_relationship_rows: 5000, max_seeds: 20, cooldown_days: 7 },
  scheduled: false,
  source: "local database only; never contacts a provider",
};

export function makeAlert(over: Partial<Alert> = {}): Alert {
  return { id: 1, rule_id: 1, signal_id: 1, severity: "high", title: "SYNTHETIC alert: large transfer", created_at: "2026-11-09T12:06:00+00:00", read_at: null, delivery: "in_app", ...over };
}

export function makeRule(over: Partial<AlertRule> = {}): AlertRule {
  return { id: 1, name: "SYNTHETIC high severity", enabled: true, min_severity: "high", min_score: 0, families: [], chains: [], created_at: "2026-11-01T00:00:00+00:00", ...over };
}

export function makeLabel(over: Partial<EntityLabel> = {}): EntityLabel {
  return {
    id: 1, chain: "ethereum", ref: SYNTHETIC_ADDR, entity_kind: "contract", category: "exchange", name: "SYNTHETIC Exchange",
    status: "proposed", source: "analyst", proposed_by: "analyst-a",
    evidence: [{ kind: "url", ref: "https://example.org/synthetic-evidence", description: "SYNTHETIC page", added_by: "analyst-a", verified_stored: true }],
    note: null, reviewed_by: null, review_note: null, created_at: "2026-11-09T09:00:00+00:00", reviewed_at: null, ...over,
  };
}

export const defaultSystem = (): SystemHealth => ({
  status: "ok", version: "0.1.0", environment: "test", time: NOW, uptime_seconds: 4000, warnings: [],
  database: { status: "ok", schema_version: 14, size_bytes: 5_242_880, wal_bytes: 1024, journal_mode: "wal", busy_timeout_ms: 5000 },
  disk: { writes: "allowed", reasons: [], total_bytes: 100 * 2 ** 30, free_bytes: 40 * 2 ** 30, free_ratio: 0.4, bip_used_bytes: 2 ** 30, max_data_bytes: 10 * 2 ** 30, min_free_bytes: 2 ** 30 },
  process: { rss_bytes: 100_000_000, threads: 8, open_files: 20 },
  ingestion: { recent_runs: [], failed_runs_24h: 0, errors_24h: [] },
  discovery: { last_run: null, open_signals: 0, unread_alerts: 0 },
  providers: { total: 5, rpc_providers: 4, configured: 3, adapters_available: 3, healthy: 0, degraded: 0, unavailable: 0, untested: 3, disabled: 0 },
  workers: { enabled: false, running: false, jobs: [] },
  backups: { enabled: true, count: 0, latest: null },
});

export const SYNTHETIC_DETECTORS: Detector[] = [
  {
    id: "large_transfer", version: "1", family: "large_transfers", title: "Large transfer", method: "median/MAD on log10(amount)",
    data_required: "stored transfers", coverage: "Transfers of ingested wallets.", limitations: "No internal transfers.",
    refresh: "each pass", cost: "database only", confidence_cap: 0.8, chains: ["ethereum", "solana"],
  },
  {
    id: "new_pool", version: "2", family: "pools", title: "New liquidity pool", method: "factory logs",
    data_required: "collector data", coverage: "Uniswap v2/v3-style factories only.", limitations: "Other layouts are invisible.",
    refresh: "each pass", cost: "database only", confidence_cap: 0.6, chains: ["ethereum"],
  },
];

export const manifest = (over: Partial<CollectorManifest> = {}): CollectorManifest => ({
  status: "succeeded", message: "Scanned 20 finalized block(s): 1 creation(s), 0 large transfer(s).",
  started_at: "2026-11-10T07:00:00+00:00", finished_at: "2026-11-10T07:00:30+00:00", blocks_scanned: 20, transactions_seen: 3000,
  contract_creations_stored: 1, large_transfers_stored: 0, first_block: 100, last_block: 119, finalized_head: 130, rpc_calls: 61,
  providers: ["publicnode-eth"], not_covered: ["x"], ...over,
});

export function installDiscoveryBackend(initial: Partial<DiscoveryState> = {}, existing: ReturnType<typeof installFakeBackend> = installFakeBackend()) {
  const base = existing.fetchMock.getMockImplementation();
  if (!base) throw new Error("no base fetch implementation");
  const state: DiscoveryState = {
    origin: "stored_observations",
    signals: [],
    alerts: [],
    rules: [],
    labels: [],
    detectors: SYNTHETIC_DETECTORS,
    freshness: { last_run_at: null, last_run_status: null, age_seconds: null, stale: true },
    eth: {
      data_origin: "stored_observations", discovery_version: "1", version: "3", chain: "ethereum", last_block_scanned: null, last_run: null,
      limits: { blocks_per_run: 20, max_rpc_calls_per_run: 150, min_native_value_eth: 10 },
      not_covered: ["ordinary transactions below the value threshold (counted, not stored)", "Solana (no collector)"],
    },
    sol: {
      data_origin: "stored_observations", discovery_version: "1", version: "1", chain: "solana", last_slot_scanned: null, last_run: null, coverage_gaps: [],
      limits: { blocks_per_run: 10, max_lag_slots: 2000, max_rpc_calls_per_run: 150, run_deadline_s: 90 },
      not_covered: ["SPL token transfers"],
      recognised_programs: [{ program_id: "SynthProgram1111111111111111111111111111111", name: "SYNTHETIC AMM", source_url: "https://example.org/amm", verified_at: "2026-10-01" }],
    },
    system: defaultSystem(),
    failures: {},
    collectResult: manifest(),
    decisions: {},
    rankingRuns: [],
    memory: {},
    runs: [],
    expansion: SYNTHETIC_EXPANSION,
    calls: [],
    ...initial,
  };
  let nextId = 100;
  const json = (status: number, body?: unknown) =>
    Promise.resolve(new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
  const err = (status: number, code: string, message: string) => json(status, { error: { code, message } });
  const env = () => ({ data_origin: state.origin, discovery_version: "1" });
  const page = <T,>(items: T[], q: URLSearchParams) => {
    const limit = Number(q.get("limit") ?? 50);
    const offset = Number(q.get("offset") ?? 0);
    return { total: items.length, limit, offset, items: items.slice(offset, offset + limit) };
  };
  const unread = () => state.alerts.filter((a) => a.read_at === null).length;

  existing.fetchMock.mockImplementation((input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const full = url.replace(/^\/api\/v1/, "");
    const [path = "", qs = ""] = full.split("?");
    const q = new URLSearchParams(qs);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
    const mine = /^\/(signals|alerts|alert-rules|discovery|system|entities\/labels|entities\/memory|ml\/signal-ranking)/.test(path);
    if (!mine) return base(input, init);
    state.calls.push({ method, path: full, body });
    const failure = state.failures[`${method} ${path}`];
    if (failure) return err(failure.status, failure.code, failure.message);

    if (path === "/discovery/run" && method === "POST") {
      state.discoveryRuns = (state.discoveryRuns ?? 0) + 1;
      return json(200, {
        ...env(), run_id: state.discoveryRuns, status: "succeeded", chains: ["ethereum"],
        detectors: { "large_transfer_mad:ethereum": { created: 2, merged: 1 }, "new_contract_deployment:ethereum": { created: 1, merged: 0 } },
        errors: [], expired: 0, alerts_created: 1,
      });
    }
    if (path === "/discovery/runs") return json(200, { ...env(), items: state.runs.slice(0, Number(q.get("limit") ?? 10)) });
    if (path === "/discovery/expansion") {
      return json(200, { ...env(), ...state.expansion, runs: state.runs.filter((r) => r.summary?.policy !== undefined) });
    }
    const expand = /^\/discovery\/expand\/(\w+)$/.exec(path);
    if (expand && method === "POST") {
      const summary = { policy: { id: String(body.policy) }, seeds_used: 3, entities_examined: 12, relationship_rows_read: 40, truncated: [] as string[], detectors: { "shared_neighbourhood:ethereum": { created: 1, merged: 0 } } };
      const run: DiscoveryRunRecord = { id: nextId++, chain: expand[1] ?? null, status: "succeeded", started_at: NOW, finished_at: NOW, summary };
      state.runs.unshift(run);
      return json(201, { ...env(), ...summary, run_id: run.id, status: "succeeded" });
    }
    if (path === "/entities/memory/search") return json(200, { items: [], last_refresh: null, data_origin: state.origin });
    const refresh = /^\/entities\/memory\/refresh\/(\w+)$/.exec(path);
    if (refresh && method === "POST") {
      return json(201, { chain: refresh[1], rows_read: { transfers: 10 }, entities_touched: 4, facts_new: 6, facts_changed: 1, facts_confirmed: 2, contradictions: 1, facts_outdated: 0, truncated_sources: [], source: "local database only; never contacts a provider", finished_at: NOW });
    }
    const mem = /^\/entities\/memory\/(\w+)\/([^/]+)$/.exec(path);
    if (mem && method === "GET") {
      const key = `${mem[1] ?? ""}:${decodeURIComponent(mem[2] ?? "")}`;
      return json(200, state.memory[key] ?? makeMemory({ chain: mem[1] ?? "", ref: decodeURIComponent(mem[2] ?? ""), data_origin: state.origin }));
    }
    if (path === "/discovery/feed") {
      const open = state.signals.filter((s) => s.status === "open").sort((a, b) => b.score - a.score);
      return json(200, { ...env(), ...page(open, q), freshness: state.freshness, scope: "SYNTHETIC scope: candidates over stored data, not findings." });
    }
    if (path === "/signals" && method === "GET") {
      let items = [...state.signals];
      const eq = (key: "status" | "family" | "chain" | "severity") => {
        const v = q.get(key);
        if (v) items = items.filter((s) => s[key] === v);
      };
      (["status", "family", "chain", "severity"] as const).forEach(eq);
      if (q.get("review_state")) items = items.filter((s) => s.review_state === q.get("review_state"));
      if (q.get("detector")) items = items.filter((s) => s.detector.id === q.get("detector"));
      if (q.get("min_score")) items = items.filter((s) => s.score >= Number(q.get("min_score")));
      items.sort((a, b) => (q.get("sort") === "recent" ? String(b.last_seen_at).localeCompare(String(a.last_seen_at)) : b.score - a.score));
      return json(200, { ...env(), ...page(items, q) });
    }
    const ranked = /^\/signals\/(\d+)\/ranking$/.exec(path);
    if (ranked) {
      return json(200, {
        ...env(),
        signal_id: Number(ranked[1]),
        available: true,
        feature_version: "1",
        observed_at: NOW,
        features: { magnitude: 0.8, relationship_degree: 2, shared_funder_cluster: 3 },
        graph_features: { relationship_degree: 2, shared_funder_cluster: 3 },
        rule_score: { value: 55, kind: "baseline (rules)" },
        anomaly: { value: 0.42, kind: "unsupervised", most_unusual: [{ feature: "magnitude", robust_z: 4.1 }], note: null },
        supervised: { status: "not_ready" },
        authority: "A ranking aid only. A person confirms or rejects; the model cannot.",
      });
    }
    if (path === "/ml/signal-ranking" && method === "GET") {
      return json(200, {
        feature_version: "1",
        ranker_version: "1",
        snapshots: 3,
        labels: {},
        labels_excluded_because: "the database is not marked as holding real network data",
        gate: { needed_per_class: 50, chains: {} },
        supervised: "not_ready",
        scorers: ["rule_score (baseline)", "anomaly (unsupervised)", "supervised (gated)"],
        last_run: state.rankingRuns[0] ?? null,
        authority: "Scores rank candidates for review; only a person confirms or rejects.",
      });
    }
    if (path === "/ml/signal-ranking/runs") return json(200, { items: state.rankingRuns });
    if (path === "/ml/signal-ranking/evaluate" && method === "POST") {
      const run = {
        id: state.rankingRuns.length + 1,
        created_at: NOW,
        actor: "local",
        status: "not_ready" as const,
        feature_version: "1",
        ranker_version: "1",
        dataset_hash: "SYNTHETIC",
        window_from: null,
        window_to: null,
        labelled: 0,
        label_counts: {},
        reason: "the test split needs confirmed and rejected candidates",
        scorers: {},
      };
      state.rankingRuns = [run, ...state.rankingRuns];
      return json(201, run);
    }
    const sig = /^\/signals\/(\d+)(?:\/(dismiss|promote|review|decisions))?$/.exec(path);
    if (sig) {
      const found = state.signals.find((s) => s.id === Number(sig[1]));
      if (!found) return err(404, "not_found", `signal ${sig[1] ?? ""} not found`);
      if (sig[2] === "decisions") return json(200, { signal_id: found.id, review_state: found.review_state, items: state.decisions[found.id] ?? [] });
      if (sig[2] === "review" && method === "POST") {
        if (body.state === found.review_state) return err(422, "invalid_input", "the signal is already in that review state");
        if (body.reason_code === "other" && !String(body.note ?? "").trim()) return err(422, "invalid_input", "a note is required when the reason is other");
        const d: ReviewDecision = {
          id: nextId++,
          from_state: found.review_state,
          to_state: body.state as ReviewDecision["to_state"],
          reason_code: body.reason_code as ReviewDecision["reason_code"],
          note: (body.note as string | undefined) ?? null,
          actor: "local",
          decided_at: new Date(Date.parse(NOW) + nextId * 1000).toISOString(),
          duplicate_of: (body.duplicate_of as number | undefined) ?? null,
          correction: (body.correction as string | undefined) ?? null,
          wallet_label: (body.wallet_label as string | undefined) ?? null,
          definitions_version: "1",
        };
        state.decisions[found.id] = [...(state.decisions[found.id] ?? []), d]; // oldest first, as the API returns them
        found.review_state = d.to_state;
        return json(200, { signal_id: found.id, review_state: found.review_state, history: state.decisions[found.id] });
      }
      if (sig[2] === "dismiss" && method === "POST") {
        if (found.status !== "open") return err(422, "invalid_input", `only an open signal can be dismissed (it is ${found.status})`);
        found.status = "dismissed";
        found.status_note = typeof body.note === "string" ? body.note : "dismissed by user";
      }
      if (sig[2] === "promote" && method === "POST") {
        if (body.target === "watchlist" && found.subject.kind === "asset") return err(422, "invalid_input", "a native-asset signal has no address to watch");
        found.status = "promoted";
        found.promoted_to = body.target === "watchlist" ? `watchlist:${String(body.watchlist_id)}` : `investigation:${String(body.investigation_id)}`;
      }
      return json(200, {
        ...env(),
        ...found,
        decisions: state.decisions[found.id] ?? [],
        ground_truth: {
          label: null,
          quality: "unlabelled",
          eligible: false,
          excluded_because: "the database is not marked as holding real network data",
          reviewers: [],
          definitions_version: "1",
        },
      });
    }
    if (path === "/discovery/detectors") return json(200, { ...env(), detectors: state.detectors });
    if (path === "/discovery/collector") return json(200, state.eth);
    if (path === "/discovery/collector/solana") return json(200, state.sol);
    if (path === "/discovery/collect/ethereum" || path === "/discovery/collect/solana") return json(201, { ...env(), ...state.collectResult });
    if (path === "/alerts" && method === "GET") {
      const items = (q.get("unread") === "true" ? state.alerts.filter((a) => a.read_at === null) : state.alerts).slice().reverse();
      return json(200, { ...env(), ...page(items, q), unread: unread() });
    }
    if (path === "/alerts/channels") return json(200, { ...env(), available: ["in_app"], unavailable: { email: "not configured: needs an approved mail service" } });
    if (path === "/alerts/read-all") {
      const n = unread();
      state.alerts.forEach((a) => { a.read_at ??= NOW; });
      return json(200, { ...env(), marked_read: n });
    }
    const rd = /^\/alerts\/(\d+)\/read$/.exec(path);
    if (rd) {
      const a = state.alerts.find((x) => x.id === Number(rd[1]));
      if (!a) return err(404, "not_found", "alert not found");
      a.read_at ??= NOW;
      return json(200, { ...env(), id: a.id, read: true });
    }
    if (path === "/alert-rules" && method === "GET") return json(200, { ...env(), items: state.rules });
    if (path === "/alert-rules" && method === "POST") {
      if (state.rules.some((r) => r.name === body.name)) return err(422, "invalid_input", `an alert rule named '${String(body.name)}' already exists`);
      const rule: AlertRule = { id: nextId++, name: String(body.name), enabled: body.enabled !== false, min_severity: body.min_severity as AlertRule["min_severity"], min_score: Number(body.min_score), families: body.families as string[], chains: body.chains as string[], created_at: NOW };
      state.rules.push(rule);
      return json(201, { ...env(), ...rule });
    }
    const rule = /^\/alert-rules\/(\d+)$/.exec(path);
    if (rule && method === "PATCH") {
      const r = state.rules.find((x) => x.id === Number(rule[1]));
      if (!r) return err(404, "not_found", "rule not found");
      if (typeof body.enabled === "boolean") r.enabled = body.enabled;
      return json(200, { ...env(), ...r });
    }
    if (path === "/entities/labels" && method === "GET") {
      const status = q.get("status");
      const items = state.labels.filter((l) => !status || l.status === status).slice().reverse();
      return json(200, { total: items.length, items, scope: "SYNTHETIC scope: labels attribute organisations and protocols, never people.", data_origin: state.origin });
    }
    if (path === "/entities/labels" && method === "POST") {
      const evidence = (body.evidence as { kind: LabelEvidence["kind"]; ref: string; description?: string }[]).map((e) => ({ ...e, added_by: "local", verified_stored: true }));
      const label = makeLabel({ id: nextId++, chain: String(body.chain), ref: String(body.ref), entity_kind: body.entity_kind as EntityLabel["entity_kind"], category: body.category as EntityLabel["category"], name: String(body.name), proposed_by: "local", evidence, note: (body.note as string | null) ?? null });
      state.labels.push(label);
      return json(201, label);
    }
    const rv = /^\/entities\/labels\/(\d+)\/review$/.exec(path);
    if (rv && method === "POST") {
      const l = state.labels.find((x) => x.id === Number(rv[1]));
      if (!l) return err(404, "not_found", "label not found");
      const extra = (body.evidence as { kind: LabelEvidence["kind"]; ref: string }[]).map((e) => ({ ...e, added_by: "local", verified_stored: true }));
      const all = [...l.evidence, ...extra];
      if (body.decision === "approve" && !all.some((e) => ["url", "transaction", "signal"].includes(e.kind) && !(e.added_by ?? "").startsWith("ai:")))
        return err(422, "invalid_input", "approval needs at least one evidence item (url, transaction or signal) added by a person");
      l.status = body.decision === "approve" ? "approved" : "rejected";
      l.reviewed_by = "local";
      l.review_note = String(body.note);
      l.reviewed_at = NOW;
      l.evidence = all;
      return json(200, l);
    }
    if (path === "/system/health") return json(200, { ...state.system, data_origin: state.origin });
    if (path === "/system/backups" && method === "POST") {
      const b = { name: "bip-backup-synthetic.sqlite3", size_bytes: 2_097_152, created_at: NOW };
      state.system = { ...state.system, backups: { ...state.system.backups, count: state.system.backups.count + 1, latest: b } };
      return json(201, { ...b, pruned: [] });
    }
    return err(404, "not_found", "Not Found");
  });
  return { ...existing, state, posts: (prefix: string) => state.calls.filter((c) => c.method === "POST" && c.path.startsWith(prefix)) };
}
