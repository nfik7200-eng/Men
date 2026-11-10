import { useSyncExternalStore } from "react";
import { allows, requiredRole } from "./access";
import { getSession, subscribeSession, type AuthMode, type Role, type SessionState } from "./session";

export type Auth = SessionState & {
  /** The signed-in user's role; null when sign-in is disabled or nobody is signed in. */
  role: Role | null;
  mode: AuthMode | null;
};

/** The current session. Works without a provider (then: nothing known, nothing restricted). */
export function useAuth(): Auth {
  const s = useSyncExternalStore(subscribeSession, getSession, getSession);
  return { ...s, role: s.user?.role ?? null };
}

export type Access = { allowed: boolean; reason: string | undefined; needs: Role | null };

/**
 * Whether the signed-in role may call `method path`. Without sign-in (disabled mode, or no
 * provider) everything is allowed, as before. The server stays authoritative; this only spares
 * the user an action that would be refused.
 */
export function useCan(method: string, path: string): Access {
  const { mode, user } = useAuth();
  const needs = requiredRole(method, path);
  if (mode !== "required" || user === null || needs === null) return { allowed: true, reason: undefined, needs };
  if (allows(user.role, needs)) return { allowed: true, reason: undefined, needs };
  return { allowed: false, reason: `Needs the ${needs} role. You are signed in as ${user.role}.`, needs };
}
