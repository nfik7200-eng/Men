// In-memory stand-in for the BIP API, used only by frontend tests. Mirrors response shapes and
// key behaviours (validation errors, duplicates, 204 deletes). Never shipped to the app.
import { vi } from "vitest";

type Json = Record<string, unknown>;
const now = "2026-11-03T12:00:00.000000Z";

export function chain(id: "ethereum" | "solana"): Json {
  return {
    chain: id, family: id === "ethereum" ? "evm" : "solana", network: id === "ethereum" ? "mainnet" : "mainnet-beta",
    position_kind: id === "ethereum" ? "block" : "slot", configured: true, adapter_implemented: false,
    connected: false, data_available: false, transactions_stored: 0, latest_stored_block: null,
    latest_stored_slot: null, last_successful_retrieval: null, status_note: "Configured only.",
  };
}

export function installFakeBackend() {
  let nextId = 1;
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
    if (path === "/chains") return json(200, [chain("ethereum"), chain("solana")]);
    if (path === "/dashboard/summary")
      return json(200, {
        generated_at: now, chains: [chain("ethereum"), chain("solana")], wallets_stored: 0,
        watchlisted_addresses: 0, transactions_stored: 0, transfers_stored: 0, watchlists: watchlists.size,
        open_investigations: 0, findings_stored: 0, recent_findings: [], recent_activity: [],
        providers: { total: 14, configured: 0, adapters_implemented: 0, connected: 0 },
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
