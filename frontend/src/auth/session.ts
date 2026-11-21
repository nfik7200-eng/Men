// The browser-side session: who is signed in and the CSRF token. Held in memory only (never in
// localStorage / sessionStorage): the HttpOnly cookie carries the session, and after a reload the
// token is recovered from the backend (GET /auth/me). A plain module store so the API client can
// read it without React; components subscribe with useAuth().

export type AuthMode = "disabled" | "required";
export type Role = "viewer" | "analyst" | "admin";
export type AuthUser = { id: number; username: string; role: Role };

/**
 * loading      status not known yet (first request in flight)
 * anonymous    sign-in required and nobody is signed in
 * authenticated  signed in, or sign-in is disabled on this server (user is then null)
 * unavailable  the backend could not be reached; the app shell renders its offline state and retries
 */
export type SessionStatus = "loading" | "anonymous" | "authenticated" | "unavailable";

export type SessionState = {
  status: SessionStatus;
  mode: AuthMode | null;
  user: AuthUser | null;
  csrf: string | null;
  /** The server has sign-in on but no users yet. */
  setupRequired: boolean;
  /** A one-off message for the sign-in screen (session ended, signed out, password changed). */
  notice: string | null;
};

const INITIAL: SessionState = {
  status: "loading",
  mode: null,
  user: null,
  csrf: null,
  setupRequired: false,
  notice: null,
};

let state: SessionState = INITIAL;

/** Bookkeeping for loadSession(): which request is current and the one in flight, if any. */
export const loading: { generation: number; inflight: Promise<void> | null } = { generation: 0, inflight: null };
const listeners = new Set<() => void>();

export function getSession(): SessionState {
  return state;
}

export function subscribeSession(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function setSession(patch: Partial<SessionState>): void {
  state = { ...state, ...patch };
  for (const l of [...listeners]) l();
}

/** The token for X-CSRF-Token, or null when there is none (sign-in disabled, or anonymous). */
export function csrfToken(): string | null {
  return state.csrf;
}

/**
 * The backend answered 401 to a session-protected call: the session ended (expired, signed out
 * elsewhere, password changed). Back to the sign-in screen with a message. Ignored when sign-in is
 * not required, because then no 401 can be about a session.
 */
export function sessionEnded(notice = "Your session has ended. Sign in again."): void {
  if (state.mode !== "required" || state.status === "anonymous") return;
  setSession({ status: "anonymous", user: null, csrf: null, notice });
}

/** Test helper: back to the initial, unknown state. */
export function resetSession(): void {
  state = INITIAL;
  loading.generation = 0;
  loading.inflight = null;
  for (const l of [...listeners]) l();
}
