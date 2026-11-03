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

export function installFakeBackend() {
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

  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = url.replace(/^\/api\/v1/, "");
    const method = init?.method ?? "GET";
    const body = init?.body ? (JSON.parse(String(init.body)) as Json) : {};
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
