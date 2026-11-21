// Scripted stand-in for the BIP authentication endpoints, layered over the in-memory fake backend.
// Test code only. Credentials here are SYNTHETIC and exist only in these tests.
import { installFakeBackend } from "./fakeBackend";

export const SYNTHETIC_PASSWORD = "synthetic-test-password-not-real";
export const SYNTHETIC_CSRF = "synthetic-csrf-token-0001";

type Role = "viewer" | "analyst" | "admin";
export type AuthBackendOptions = {
  mode?: "disabled" | "required";
  /** Already signed in (cookie present) as this user. */
  session?: { username: string; role: Role } | null;
  setupRequired?: boolean;
  /** /auth/login answers 429 with this Retry-After. */
  throttleLogin?: number;
  /** Every non-auth call answers this status/code once signed in (to test 401/403 handling). */
  protectedFailure?: { status: number; code: string; message: string } | null;
  /** Make /auth/status itself fail like an unreachable backend. */
  statusNetworkError?: boolean;
  /** Include csrf_token in /auth/status (the backend currently does not). */
  csrfInStatus?: boolean;
};

const publicUser = (username: string, role: Role) => ({
  id: 1, username, role, enabled: true, created_at: "2026-11-03T12:00:00Z", last_login_at: null,
});
const json = (status: number, body?: unknown, headers: Record<string, string> = {}) =>
  Promise.resolve(
    new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json", ...headers },
    }),
  );
const err = (status: number, code: string, message: string, headers: Record<string, string> = {}) =>
  json(status, { error: { code, message } }, headers);

export type Call = { method: string; path: string; csrf: string | null; body: unknown; credentials: RequestCredentials | undefined };

export function installAuthBackend(opts: AuthBackendOptions = {}) {
  const fake = installFakeBackend();
  const base = fake.fetchMock.getMockImplementation();
  if (!base) throw new Error("fake backend has no implementation");
  const mode = opts.mode ?? "required";
  const state = { session: opts.session ?? null, password: SYNTHETIC_PASSWORD };
  const calls: Call[] = [];

  fake.fetchMock.mockImplementation((input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = url.replace(/^\/api\/v1/, "");
    const method = (init?.method ?? "GET").toUpperCase();
    const headers = (init?.headers ?? {}) as Record<string, string>;
    const body = init?.body ? (JSON.parse(String(init.body)) as unknown) : null;
    calls.push({ method, path, csrf: headers["X-CSRF-Token"] ?? null, body, credentials: init?.credentials });

    if (path === "/auth/status") {
      if (opts.statusNetworkError) return Promise.reject(new TypeError("Failed to fetch"));
      return json(200, {
        mode,
        setup_required: opts.setupRequired ?? false,
        authenticated: state.session !== null,
        user: state.session ? publicUser(state.session.username, state.session.role) : null,
        ...(opts.csrfInStatus && state.session ? { csrf_token: SYNTHETIC_CSRF } : {}),
      });
    }
    if (mode === "required") {
      if (path === "/auth/login" && method === "POST") {
        if (opts.throttleLogin !== undefined) return err(429, "rate_limited", "Too many requests. Slow down.", { "Retry-After": String(opts.throttleLogin) });
        const b = body as { username: string; password: string };
        if (b.password !== state.password) return err(401, "invalid_credentials", "Invalid username or password.");
        const role: Role = b.username.startsWith("viewer") ? "viewer" : b.username.startsWith("admin") ? "admin" : "analyst";
        state.session = { username: b.username, role };
        return json(200, { user: publicUser(b.username, role), csrf_token: SYNTHETIC_CSRF, expires_at: "2026-11-04T00:00:00Z" });
      }
      if (path === "/health") return base(input, init);
      if (!state.session) return err(401, "unauthenticated", "Sign in to use this endpoint.");
      if (path === "/auth/me") return json(200, { mode, user: publicUser(state.session.username, state.session.role), csrf_token: SYNTHETIC_CSRF, expires_at: "2026-11-04T00:00:00Z" });
      if (method !== "GET" && headers["X-CSRF-Token"] !== SYNTHETIC_CSRF) return err(403, "csrf_failed", "The CSRF token is missing or wrong.");
      if (path === "/auth/logout" && method === "POST") {
        state.session = null;
        return json(204);
      }
      if (path === "/auth/password" && method === "POST") {
        const b = body as { current_password: string; new_password: string };
        if (b.current_password !== state.password) return err(422, "invalid_input", "the current password is not correct");
        state.password = b.new_password;
        state.session = null;
        return json(204);
      }
      if (opts.protectedFailure && !path.startsWith("/auth/")) {
        const f = opts.protectedFailure;
        if (f.status === 401) state.session = null;
        return err(f.status, f.code, f.message);
      }
    }
    return base(input, init);
  });
  return { ...fake, state, calls };
}
