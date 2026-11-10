import { describe, expect, it } from "vitest";
import { allows, requiredRole } from "./access";

// Expected values were produced by bip.api.access.required_role for the same inputs, so this table
// fails if the UI's copy of the policy drifts from the backend's.
const BACKEND_POLICY: [string, string, string | null][] = [
  ["GET", "/health", null],
  ["POST", "/auth/login", null],
  ["GET", "/auth/status", null],
  ["POST", "/auth/logout", "viewer"],
  ["POST", "/auth/password", "viewer"],
  ["GET", "/auth/me", "viewer"],
  ["GET", "/users", "admin"],
  ["POST", "/users", "admin"],
  ["GET", "/audit", "admin"],
  ["GET", "/watchlists", "viewer"],
  ["POST", "/watchlists", "analyst"],
  ["DELETE", "/watchlists/3/entries/4", "analyst"],
  ["POST", "/investigations", "analyst"],
  ["PATCH", "/investigations/2", "analyst"],
  ["POST", "/investigations/2/research-runs", "analyst"],
  ["POST", "/intelligence/analyze/ethereum/0xaa", "analyst"],
  ["POST", "/intelligence/findings/f1/attach", "analyst"],
  ["POST", "/intelligence/opportunities/refresh/ethereum", "analyst"],
  ["POST", "/intelligence/watchlists/1/analyze", "analyst"],
  ["POST", "/intelligence/verification/ethereum/0xaa", "admin"],
  ["POST", "/ml/classify/ethereum/0xaa", "analyst"],
  ["POST", "/ml/labels", "analyst"],
  ["POST", "/ml/labels/1/reviews", "analyst"],
  ["POST", "/ml/training-runs", "admin"],
  ["POST", "/research/preview", "analyst"],
  ["POST", "/research/runs", "analyst"],
  ["POST", "/research/runs/x/cancel", "analyst"],
  ["POST", "/signals/x", "analyst"],
  ["POST", "/alerts/x", "analyst"],
  ["POST", "/alert-rules", "analyst"],
  ["POST", "/discovery/run", "analyst"],
  ["POST", "/relationships/rebuild", "analyst"],
  ["POST", "/entities/labels", "analyst"],
  ["POST", "/ingestion/wallet/ethereum/0xaa", "admin"],
  ["POST", "/chains/ethereum/latest-block", "admin"],
  ["POST", "/providers/x/health-check", "admin"],
  ["PATCH", "/providers/x", "admin"],
  ["POST", "/providers/probe-runs", "admin"],
  ["GET", "/wallets", "viewer"],
  ["GET", "/providers/probe/preview", "viewer"],
  ["POST", "/something/new", "admin"],
];

describe("access policy mirror", () => {
  it.each(BACKEND_POLICY)("%s %s needs %s", (method, path, needed) => {
    expect(requiredRole(method, path)).toBe(needed);
  });

  it("ignores a query string and is case-insensitive about the method", () => {
    expect(requiredRole("get", "/watchlists?limit=5")).toBe("viewer");
    expect(requiredRole("post", "/watchlists?x=1")).toBe("analyst");
  });

  it("ranks roles viewer < analyst < admin", () => {
    expect(allows("viewer", "viewer")).toBe(true);
    expect(allows("viewer", "analyst")).toBe(false);
    expect(allows("analyst", "analyst")).toBe(true);
    expect(allows("analyst", "admin")).toBe(false);
    expect(allows("admin", "viewer")).toBe(true);
  });
});
