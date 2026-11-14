// Session actions: restore on load, sign in, sign out, change password. They talk to the API
// client and write the in-memory session store; no component state lives here.
import { ApiError, api } from "../api/client";
import type { AuthUserPublic } from "../api/types";
import { loading, setSession, type AuthUser } from "./session";

const toUser = (u: AuthUserPublic): AuthUser => ({ id: u.id, username: u.username, role: u.role });

/**
 * Ask the backend whether sign-in is required and whether this browser already has a session
 * (HttpOnly cookie). Recovers the CSRF token after a reload via /auth/me. Concurrent calls share
 * one request. Anything other than mode "required" is treated as "disabled", exactly as before
 * authentication existed.
 */
export function loadSession(): Promise<void> {
  if (loading.inflight) return loading.inflight;
  const mine = ++loading.generation;
  const run = async (): Promise<void> => {
    let status;
    try {
      status = await api.authStatus();
    } catch (err) {
      if (mine !== loading.generation) return;
      // Backend down: show the normal shell (it has its own offline state) and let the provider retry.
      // Any other failure (for example an older backend without /auth/status) means no sign-in.
      if (err instanceof ApiError && err.isConnectionError) setSession({ status: "unavailable" });
      else setSession({ status: "authenticated", mode: "disabled", user: null, csrf: null, setupRequired: false });
      return;
    }
    if (mine !== loading.generation) return;
    if (status.mode !== "required") {
      setSession({ status: "authenticated", mode: "disabled", user: null, csrf: null, setupRequired: false });
      return;
    }
    const setupRequired = status.setup_required === true;
    if (!status.authenticated || !status.user) {
      setSession({ status: "anonymous", mode: "required", user: null, csrf: null, setupRequired });
      return;
    }
    let csrf = status.csrf_token ?? null;
    let user = status.user;
    if (!csrf) {
      try {
        const me = await api.authMe();
        csrf = me.csrf_token;
        if (me.user) user = me.user;
      } catch (err) {
        if (mine !== loading.generation) return;
        if (err instanceof ApiError && err.isConnectionError) setSession({ status: "unavailable" });
        else setSession({ status: "anonymous", mode: "required", user: null, csrf: null, setupRequired });
        return;
      }
    }
    if (mine !== loading.generation) return;
    if (!csrf) {
      setSession({ status: "anonymous", mode: "required", user: null, csrf: null, setupRequired });
      return;
    }
    setSession({ status: "authenticated", mode: "required", user: toUser(user), csrf, setupRequired, notice: null });
  };
  const pending = run().finally(() => {
    if (loading.inflight === pending) loading.inflight = null;
  });
  loading.inflight = pending;
  return pending;
}

/** Sign in. Throws the ApiError (401 invalid credentials, 429 throttled) for the form to show. */
export async function login(username: string, password: string): Promise<void> {
  const result = await api.login(username.trim(), password);
  loading.generation++; // a status request still in flight must not overwrite this
  setSession({
    status: "authenticated",
    mode: "required",
    user: toUser(result.user),
    csrf: result.csrf_token,
    setupRequired: false,
    notice: null,
  });
}

/** Sign out. The local session is cleared even when the call fails (for example it had already expired). */
export async function logout(): Promise<void> {
  try {
    await api.logout();
  } catch {
    /* the server side session is gone or unreachable; the cookie is HttpOnly and expires on its own */
  }
  loading.generation++;
  setSession({ status: "anonymous", user: null, csrf: null, notice: "You have signed out." });
}

/** Change the signed-in user's password. The server ends every session, so sign in again afterwards. */
export async function changePassword(current: string, next: string): Promise<void> {
  await api.changePassword(current, next);
  loading.generation++;
  setSession({
    status: "anonymous",
    user: null,
    csrf: null,
    notice: "Password changed. Sign in again with your new password.",
  });
}
