import { describe, expect, it, vi } from "vitest";
import { ApiError, api } from "./client";

const respond = (status: number, body: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("api client", () => {
  it("returns parsed JSON and calls the versioned path", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(200, { status: "ok" }));
    await expect(api.health()).resolves.toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/health", expect.objectContaining({ headers: { Accept: "application/json" } }));
  });

  it("sends JSON bodies with a content type", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(201, { id: 1 }));
    await api.createWatchlist("Whales", null);
    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ name: "Whales", description: null }));
    expect(init?.headers).toEqual({
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-BIP-Client": "web", // write proof when sign-in is off
    });
  });

  it("sends no write proof on reads", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(200, { status: "ok" }));
    await api.health();
    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.headers).toEqual({ Accept: "application/json" });
  });

  it("turns the error envelope into an ApiError", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      respond(409, { error: { code: "conflict", message: "already exists" } }),
    );
    const err = await api.createWatchlist("x", null).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).code).toBe("conflict");
    expect((err as ApiError).message).toBe("already exists");
  });

  it("reports connection failures distinctly", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    const err = (await api.health().catch((e: unknown) => e)) as ApiError;
    expect(err.isConnectionError).toBe(true);
    expect(err.message).toMatch(/Cannot reach the BIP backend/);
  });

  it("handles non-JSON error bodies and 204 responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response("<html>", { status: 500 }));
    const err = (await api.health().catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(500);
    expect(err.code).toBe("http_error");
    expect(err.isConnectionError).toBe(false);
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(api.deleteWatchlist(1)).resolves.toBeUndefined();
  });

  it("encodes path parameters", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(404, { error: { code: "not_found", message: "x" } }));
    await api.wallet("solana", "a/b?c").catch(() => undefined);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/wallets/solana/a%2Fb%3Fc");
  });

  it("treats a gateway error without a BIP envelope as the backend being unreachable", async () => {
    // The Vite dev/preview proxy answers 502 with an empty body when the backend is not running.
    for (const status of [502, 503, 504]) {
      vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response("", { status }));
      const err = (await api.health().catch((e: unknown) => e)) as ApiError;
      expect(err.isConnectionError).toBe(true);
      expect(err.code).toBe("backend_unreachable");
      expect(err.message).toMatch(/Cannot reach the BIP backend/);
    }
  });

  it("keeps a backend-reported 503 (with its envelope) as a backend error, not a connection error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      respond(503, { error: { code: "database_unavailable", message: "database is unavailable" } }),
    );
    const err = (await api.health().catch((e: unknown) => e)) as ApiError;
    expect(err.isConnectionError).toBe(false);
    expect(err.code).toBe("database_unavailable");
  });

  it("requests the wallet status endpoint with encoded segments", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(200, { stored: false }));
    await expect(api.walletStatus("ethereum", "0xAB")).resolves.toEqual({ stored: false });
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/wallets/ethereum/0xAB/status");
  });

  it("builds the research history, rerun, label and provider-detail requests", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(respond(200, {})));
    await api.researchHistory({ limit: 20, offset: 40 });
    await api.researchHistory({ chain: "solana", address: "Ab", limit: 5 });
    await api.researchRerun("r-1");
    await api.labels(25, 0);
    await api.addLabels([{ chain: "ethereum", address: "0x1", label: "exchange_hot_wallet", value: true, status: "analyst_reviewed", source: "analyst", source_ref: null, observed_at: "2026-11-06T00:00:00Z", note: null }]);
    await api.reviewLabel(7, { decision: "accepted", reviewer: "me", note: null });
    await api.taxonomy();
    await api.walletFeatures("ethereum", "0x1");
    await api.capabilityHistory("publicnode-eth");
    await api.providerReliability("publicnode-eth");
    await api.providerConsistency("publicnode-eth");
    const urls = fetchMock.mock.calls.map((c) => `${c[1]?.method ?? "GET"} ${String(c[0])}`);
    expect(urls).toEqual([
      "GET /api/v1/research/runs?limit=20&offset=40",
      "GET /api/v1/research/runs?limit=5&offset=0&chain=solana&address=Ab",
      "POST /api/v1/research/runs/r-1/rerun",
      "GET /api/v1/ml/labels?limit=25&offset=0",
      "POST /api/v1/ml/labels",
      "POST /api/v1/ml/labels/7/reviews",
      "GET /api/v1/ml/taxonomy",
      "GET /api/v1/ml/features/ethereum/0x1",
      "GET /api/v1/providers/publicnode-eth/capabilities/history",
      "GET /api/v1/providers/publicnode-eth/reliability",
      "GET /api/v1/providers/publicnode-eth/consistency",
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[4]?.[1]?.body)).labels).toHaveLength(1);
  });
});
