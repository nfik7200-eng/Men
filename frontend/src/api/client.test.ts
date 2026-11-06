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
    expect(init?.headers).toEqual({ Accept: "application/json", "Content-Type": "application/json" });
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
});
