import { useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api/client";
import { Pill } from "./components/ui";
import CapitalFlows from "./pages/CapitalFlows";
import Chains from "./pages/Chains";
import GraphPage from "./pages/Graph";
import Investigations, { InvestigationDetail } from "./pages/Investigations";
import Opportunities from "./pages/Opportunities";
import Overview from "./pages/Overview";
import Providers from "./pages/Providers";
import Research from "./pages/Research";
import Settings from "./pages/Settings";
import Wallets, { WalletProfilePage } from "./pages/Wallets";
import Watchlists from "./pages/Watchlists";
import { useApi } from "./lib/useApi";

export const NAV = [
  { to: "/", label: "Overview" },
  { to: "/chains", label: "Chains" },
  { to: "/wallets", label: "Wallet Intelligence" },
  { to: "/flows", label: "Capital Flows" },
  { to: "/graph", label: "Relationship Graph" },
  { to: "/opportunities", label: "Opportunities" },
  { to: "/research", label: "AI Research" },
  { to: "/investigations", label: "Investigations" },
  { to: "/providers", label: "Providers" },
  { to: "/watchlists", label: "Watchlists" },
  { to: "/settings", label: "Settings" },
] as const;

function ConnectionBadge() {
  const health = useApi(api.health);
  if (health.status === "loading") return <Pill tone="idle">Connecting…</Pill>;
  if (health.status === "error")
    return (
      <button type="button" className="badge-button" onClick={health.reload} title="Retry">
        <Pill tone="bad">Backend offline</Pill>
      </button>
    );
  return <Pill tone={health.data.status === "ok" ? "ok" : "warn"}>API {health.data.status}</Pill>;
}

function NotFound() {
  return (
    <div className="state state-empty">
      <strong>Page not found</strong>
      <div className="state-body">Use the navigation to pick a section.</div>
    </div>
  );
}

export default function App() {
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  const [lastPath, setLastPath] = useState(location.pathname);
  if (location.pathname !== lastPath) {
    setLastPath(location.pathname);
    setMenuOpen(false);
  }

  return (
    <div className="shell">
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <div className="brand-row">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true" />
            <div>
              <strong>BIP</strong>
              <span>Blockchain Intelligence · 0.5</span>
            </div>
          </div>
          <button
            type="button"
            className="btn menu-toggle"
            aria-expanded={menuOpen}
            aria-controls="main-nav"
            onClick={() => setMenuOpen((o) => !o)}
          >
            Menu
          </button>
        </div>
        <nav id="main-nav" aria-label="Main">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === "/"} className="nav-link">
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <ConnectionBadge />
          <span className="muted">Local prototype · no authentication</span>
        </div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/chains" element={<Chains />} />
          <Route path="/wallets" element={<Wallets />} />
          <Route path="/wallets/:chain/:address" element={<WalletProfilePage />} />
          <Route path="/flows" element={<CapitalFlows />} />
          <Route path="/graph" element={<GraphPage />} />
          <Route path="/opportunities" element={<Opportunities />} />
          <Route path="/research" element={<Research />} />
          <Route path="/investigations" element={<Investigations />} />
          <Route path="/investigations/:id" element={<InvestigationDetail />} />
          <Route path="/providers" element={<Providers />} />
          <Route path="/watchlists" element={<Watchlists />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  );
}
