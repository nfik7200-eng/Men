import { afterEach, describe, expect, it, vi } from "vitest";
import { getSession, resetSession, setSession } from "../auth/session";
import { ApiError, api } from "./client";

// SYNTHETIC token, test only.
const CSRF = "synthetic-csrf-token-0001";

const respond = (status: number, body?: unknown, headers: Record<string, string> = {}) =>
  new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
const envelope = (code: string, message: string) => ({ error: { code, message } });
const signedIn = () =>
  setSession({ status: "authenticated", mode: "required", user: { id: 1, username: "analyst-a", role: "analyst" }, csrf: CSRF });

afterEach(() => resetSession());

describe("api client: credentials and CSRF", () => {
  it("always sends same-origin credentials", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(200, []));
    await api.watchlists();
    expect(fetchMock.mock.calls[0]?.[1]?.credentials).toBe("same-origin");
  });

  it("sends X-CSRF-Token on unsafe methods only, and only when a token is held", async () => {
    signedIn();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(respond(200, {})));
    await api.watchlists();
    await api.createWatchlist("w", null);
    await api.updateInvestigation(1, "closed");
    await api.deleteWatchlist(1);
    const headerOf = (i: number) => (fetchMock.mock.calls[i]?.[1]?.headers ?? {}) as Record<string, string>;
    expect(headerOf(0)["X-CSRF-Token"]).toBeUndefined();
    expect(headerOf(1)["X-CSRF-Token"]).toBe(CSRF);
    expect(headerOf(2)["X-CSRF-Token"]).toBe(CSRF);
    expect(headerOf(3)["X-CSRF-Token"]).toBe(CSRF);
    resetSession();
    await api.createWatchlist("w", null);
    expect(headerOf(4)["X-CSRF-Token"]).toBeUndefined();
  });

  it("keeps the token out of web storage", async () => {
    signedIn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(200, {}));
    await api.createWatchlist("w", null);
    expect(JSON.stringify({ ...localStorage })).not.toContain(CSRF);
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(CSRF);
  });
});

describe("api client: 401", () => {
  it("returns to the sign-in state with a message when a protected call says unauthenticated", async () => {
    signedIn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(401, envelope("unauthenticated", "Sign in to use this endpoint.")));
    const err = (await api.watchlists().catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(401);
    expect(getSession()).toMatchObject({ status: "anonymous", user: null, csrf: null });
    expect(getSession().notice).toMatch(/session has ended/i);
  });

  it("does not treat a wrong password at sign-in as an ended session", async () => {
    setSession({ status: "anonymous", mode: "required" });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(401, envelope("invalid_credentials", "Invalid username or password.")));
    const err = (await api.login("a", "b").catch((e: unknown) => e)) as ApiError;
    expect(err.code).toBe("invalid_credentials");
    expect(err.message).toBe("Invalid username or password.");
    expect(getSession().notice).toBeNull();
  });

  it("ignores 401 when sign-in is not required", async () => {
    setSession({ status: "authenticated", mode: "disabled" });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(401, envelope("unauthenticated", "x")));
    await api.watchlists().catch(() => undefined);
    expect(getSession().status).toBe("authenticated");
  });
});

describe("api client: 403 and 429 wording", () => {
  const failWith = async (status: number, code: string, message: string, headers: Record<string, string> = {}) => {
    signedIn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(status, envelope(code, message), headers));
    return (await api.createWatchlist("w", null).catch((e: unknown) => e)) as ApiError;
  };

  it("explains a missing or wrong CSRF token", async () => {
    const err = await failWith(403, "csrf_failed", "The CSRF token is missing or wrong.");
    expect(err.code).toBe("csrf_failed");
    expect(err.message).toMatch(/security token/i);
    expect(err.message).toMatch(/reload/i);
    expect(getSession().status).toBe("authenticated"); // not a sign-out
  });

  it("explains a role that is too low and keeps the server's reason", async () => {
    const err = await failWith(403, "forbidden", "This action needs the analyst role.");
    expect(err.message).toBe("Your role does not allow this. This action needs the analyst role.");
    expect(getSession().status).toBe("authenticated");
  });

  it("explains a denied origin", async () => {
    const err = await failWith(403, "origin_denied", "This origin may not make changes.");
    expect(err.message).toMatch(/address is not allowed to make changes/i);
  });

  it("shows Retry-After on a 429", async () => {
    const err = await failWith(429, "rate_limited", "Too many requests. Slow down.", { "Retry-After": "17" });
    expect(err.status).toBe(429);
    expect(err.retryAfter).toBe(17);
    expect(err.message).toBe("Too many requests. Try again in 17 seconds.");
  });

  it("handles a 429 without or with a bad Retry-After", async () => {
    expect((await failWith(429, "rate_limited", "x")).message).toMatch(/wait a moment/i);
    expect((await failWith(429, "rate_limited", "x", { "Retry-After": "soon" })).retryAfter).toBeNull();
    expect((await failWith(429, "rate_limited", "x", { "Retry-After": "1" })).message).toBe("Too many requests. Try again in 1 second.");
  });

  it("leaves other errors untouched", async () => {
    const err = await failWith(409, "conflict", "already exists");
    expect(err.message).toBe("already exists");
    expect(err.retryAfter).toBeNull();
  });
});
