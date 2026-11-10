import { createContext, useContext, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/useAuth";
import type { Health } from "../api/types";
import { onAlertsChanged } from "../lib/alertsSync";
import { useApi, type ApiState } from "../lib/useApi";
import { Pill } from "./ui";

export type NavItem = { to: string; label: string; heading: string };
export type NavGroup = { label: string; items: NavItem[] };

/** Grouped navigation (owner-approved 2026-11-06). `label` is the short menu text; `heading` the page h1. */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Monitor",
    items: [
      { to: "/", label: "Overview", heading: "Overview" },
      { to: "/chains", label: "Chains", heading: "Chains" },
      { to: "/providers", label: "Providers", heading: "Providers" },
    ],
  },
  {
    label: "Explore",
    items: [
      { to: "/wallets", label: "Wallets", heading: "Wallet Intelligence" },
      { to: "/flows", label: "Flows", heading: "Capital Flows" },
      { to: "/graph", label: "Graph", heading: "Relationship Graph" },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { to: "/signals", label: "Signals", heading: "Signals" },
      { to: "/alerts", label: "Alerts", heading: "Alerts" },
      { to: "/research", label: "Research", heading: "AI Research" },
      { to: "/opportunities", label: "Opportunities", heading: "Opportunities" },
      { to: "/attribution", label: "Attribution", heading: "Attribution" },
    ],
  },
  {
    label: "Cases",
    items: [
      { to: "/investigations", label: "Investigations", heading: "Investigations" },
      { to: "/watchlists", label: "Watchlists", heading: "Watchlists" },
    ],
  },
  {
    label: "",
    items: [
      { to: "/coverage", label: "Coverage", heading: "Coverage" },
      { to: "/system", label: "System", heading: "System" },
      { to: "/settings", label: "Settings", heading: "Settings" },
    ],
  },
];

export const NAV: NavItem[] = NAV_GROUPS.flatMap((g) => g.items);

type HealthState = ApiState<Health> & { reload: () => void; generation: number };
const HealthContext = createContext<HealthState | null>(null);

/** One shared /health request for the badge, the banner and the version label. */
export function HealthProvider({ children }: { children: ReactNode }) {
  const health = useApi(api.health);
  // `generation` increases each time the backend comes back after being unreachable, so the page
  // can remount and every panel refetches (one Retry recovers the whole page).
  const [generation, setGeneration] = useState(0);
  const [wasDown, setWasDown] = useState(false);
  if (health.status === "error" && !wasDown) setWasDown(true);
  if (health.status === "ready" && wasDown) {
    setWasDown(false);
    setGeneration((g) => g + 1);
  }
  return <HealthContext.Provider value={{ ...health, generation }}>{children}</HealthContext.Provider>;
}

export function useHealth(): HealthState {
  const h = useContext(HealthContext);
  if (!h) throw new Error("useHealth must be used inside HealthProvider");
  return h;
}

function ConnectionBadge() {
  const health = useHealth();
  if (health.status === "loading") return <Pill tone="idle">Connecting…</Pill>;
  if (health.status === "error")
    return (
      <button type="button" className="badge-button" onClick={health.reload} title="Retry">
        <Pill tone="bad">Backend offline</Pill>
      </button>
    );
  return <Pill tone={health.data.status === "ok" ? "ok" : "warn"}>API {health.data.status}</Pill>;
}

/** A single page-level notice when the backend cannot be reached. */
export function BackendBanner() {
  const health = useHealth();
  if (health.status !== "error") return null;
  const offline = health.error.isConnectionError;
  return (
    <div className="backend-banner" role="status" aria-label="Backend status">
      <div>
        <strong>{offline ? "Backend unreachable" : "Backend health check failed"}</strong>
        <span className="muted">
          {offline
            ? " The BIP API is not running or not reachable. Start it with make api (serves http://127.0.0.1:8000), or the offline fixture backend; then Retry. Nothing is shown in place of missing data."
            : ` ${health.error.message}`}
        </span>
      </div>
      <button type="button" className="btn" onClick={health.reload}>
        Retry
      </button>
    </div>
  );
}

/**
 * Unread alert count for the navigation badge. Quiet by design: null while loading and on any
 * error (no alert, no banner), refreshed on navigation, when alerts are marked read and when the
 * backend comes back.
 */
function useUnreadAlerts(): number | null {
  const { generation } = useHealth();
  const { pathname } = useLocation();
  const unread = useApi(() => api.alerts({ unread: true, limit: 1 }), [generation]);
  const { reload } = unread;
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    reload();
  }, [pathname, reload]);
  useEffect(() => onAlertsChanged(reload), [reload]);
  return unread.status === "ready" ? unread.data.unread : null;
}

export function Sidebar({ search }: { search?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const [lastPath, setLastPath] = useState(location.pathname);
  const menuRef = useRef<HTMLButtonElement>(null);
  const navRef = useRef<HTMLElement>(null);
  const health = useHealth();
  const { mode } = useAuth();
  const unreadAlerts = useUnreadAlerts();
  const badgeId = useId();
  if (location.pathname !== lastPath) {
    setLastPath(location.pathname);
    setOpen(false);
  }

  useEffect(() => {
    if (!open) return;
    navRef.current?.querySelector<HTMLAnchorElement>("a")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        menuRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <aside className={`sidebar ${open ? "open" : ""}`}>
      <div className="brand-row">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <div>
            <strong>BIP</strong>
            <span>Blockchain intelligence</span>
          </div>
        </div>
        <button
          ref={menuRef}
          type="button"
          className="btn menu-toggle"
          aria-expanded={open}
          aria-controls="main-nav"
          onClick={() => setOpen((o) => !o)}
        >
          Menu
        </button>
      </div>
      {search ? <div className="sidebar-search">{search}</div> : null}
      <nav id="main-nav" aria-label="Main" ref={navRef}>
        {NAV_GROUPS.map((g, i) => {
          const id = `nav-group-${i}`;
          return (
            <div className="nav-group" key={id}>
              {g.label ? (
                <span className="nav-group-label" id={id}>
                  {g.label}
                </span>
              ) : null}
              <ul className="nav-list" {...(g.label ? { "aria-labelledby": id } : { "aria-label": "Other" })}>
                {g.items.map((item) => (
                  <li key={item.to}>
                    <NavLink
                      to={item.to}
                      end={item.to === "/"}
                      className="nav-link"
                      {...(item.to === "/alerts" && unreadAlerts ? { "aria-describedby": badgeId } : {})}
                    >
                      {item.label}
                    </NavLink>
                    {item.to === "/alerts" && unreadAlerts ? (
                      <>
                        <span className="nav-badge" aria-hidden="true">{unreadAlerts > 99 ? "99+" : unreadAlerts}</span>
                        <span className="visually-hidden" id={badgeId}>{unreadAlerts} unread alert{unreadAlerts === 1 ? "" : "s"}</span>
                      </>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </nav>
      <div className="sidebar-foot">
        <ConnectionBadge />
        <span className="muted">
          {health.status === "ready" ? `BIP ${health.data.version} · ${health.data.environment}` : "BIP"}
        </span>
        <span className="muted">
          {mode === "required" ? "Sign-in required · protected session" : "Local prototype · no authentication"}
        </span>
      </div>
    </aside>
  );
}
