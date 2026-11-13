// Who may call what, mirrored from bip/api/access.py so the UI can hide or disable actions the
// server would refuse. Advisory only: the server decides, and every 403 is still handled.
import type { Role } from "./session";

export const ROLE_RANK: Record<Role, number> = { viewer: 0, analyst: 1, admin: 2 };

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const PUBLIC_PATHS = new Set(["/health", "/auth/login", "/auth/status"]);
const SELF_SERVICE = new Set(["/auth/logout", "/auth/password", "/auth/me"]);
const ADMIN_ANY = /^\/(users|audit)(\/|$)/;
const ANALYST_WRITE: RegExp[] = [
  /^\/watchlists(\/|$)/,
  /^\/investigations(\/|$)/,
  /^\/intelligence\/analyze\//,
  /^\/intelligence\/findings\//,
  /^\/intelligence\/opportunities\//,
  /^\/intelligence\/watchlists\//,
  /^\/ml\/classify\//,
  /^\/ml\/labels(\/|$)/,
  /^\/research\//,
  /^\/signals\//,
  /^\/alerts\//,
  /^\/alert-rules(\/|$)/,
  /^\/discovery\/run$/,
  /^\/discovery\/expand\//,
  /^\/entities\/memory\/refresh\//,
  /^\/relationships\/rebuild$/,
  /^\/entities\/labels(\/|$)/,
];

/** Role needed for `method` on `path` (relative to /api/v1, without the query string); null if public. */
export function requiredRole(method: string, path: string): Role | null {
  const p = path.split("?")[0] ?? path;
  if (PUBLIC_PATHS.has(p)) return null;
  if (SELF_SERVICE.has(p)) return "viewer";
  if (ADMIN_ANY.test(p)) return "admin";
  if (SAFE_METHODS.has(method.toUpperCase())) return "viewer";
  if (ANALYST_WRITE.some((re) => re.test(p))) return "analyst";
  return "admin";
}

export function allows(role: Role, needed: Role): boolean {
  return ROLE_RANK[role] >= ROLE_RANK[needed];
}
