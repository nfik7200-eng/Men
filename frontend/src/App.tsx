import { useEffect, useRef } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import { UserMenu } from "./auth/UserMenu";
import { BackendBanner, HealthProvider, Sidebar, useHealth } from "./components/shell";
import { WalletSearch } from "./components/WalletSearch";
import Alerts from "./pages/Alerts";
import Attribution from "./pages/Attribution";
import CapitalFlows from "./pages/CapitalFlows";
import Chains from "./pages/Chains";
import Coverage from "./pages/Coverage";
import Models from "./pages/Models";
import GraphPage from "./pages/Graph";
import GraphExplorerPage from "./pages/GraphExplorer";
import Investigations, { CaseDetailPage, CaseWorkbench, InvestigationDetail } from "./pages/Investigations";
import Opportunities from "./pages/Opportunities";
import Overview from "./pages/Overview";
import Providers from "./pages/Providers";
import ProviderDetail from "./pages/ProviderDetail";
import Research from "./pages/Research";
import RunPage from "./pages/research/RunPage";
import Settings from "./pages/Settings";
<<<<<<< HEAD
import SignalDetailPage from "./pages/signals/SignalDetail";
import Signals from "./pages/Signals";
import System from "./pages/System";
import Wallets from "./pages/Wallets";
import WalletWorkspacePage from "./pages/wallet/Workspace";
=======
import Curation from "./pages/Curation";
import Strategies from "./pages/Strategies";
import Wallets, { WalletProfilePage } from "./pages/Wallets";
>>>>>>> 7ec5992 (Implement BIP Crypto Detective & Investigation Platform (Phases 1-3))
import Watchlists from "./pages/Watchlists";

<<<<<<< HEAD
export { NAV, NAV_GROUPS } from "./components/shell";
=======
export const NAV = [
  { to: "/", label: "Overview" },
  { to: "/chains", label: "Chains" },
  { to: "/wallets", label: "Wallet Intelligence" },
  { to: "/flows", label: "Capital Flows" },
  { to: "/graph", label: "Relationship Graph" },
  { to: "/explorer", label: "Temporal Explorer" },
  { to: "/opportunities", label: "Opportunities" },
  { to: "/strategies", label: "Strategies & Trackers" },
  { to: "/research", label: "AI Research" },
  { to: "/curation", label: "Model Curation" },
  { to: "/cases", label: "Case Workbench" },
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
>>>>>>> 7ec5992 (Implement BIP Crypto Detective & Investigation Platform (Phases 1-3))

function NotFound() {
  return (
    <div className="state state-empty">
      <strong>Page not found</strong>
      <div className="state-body">Use the navigation to pick a section.</div>
    </div>
  );
}

/** Moves focus to the page heading after client-side navigation (not on first load). */
function useFocusOnNavigate() {
  const { pathname } = useLocation();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    document.querySelector<HTMLElement>("main h1")?.focus();
  }, [pathname]);
}

export default function App() {
  return (
    <HealthProvider>
      <Shell />
    </HealthProvider>
  );
}

function Shell() {
  useFocusOnNavigate();
  const { generation } = useHealth();
  return (
    <div className="shell">
      <a className="skip-link" href="#content">
        Skip to content
      </a>
      <Sidebar />
      <main className="main" id="content" tabIndex={-1}>
        <div className="topbar">
          <WalletSearch compact />
          <UserMenu />
        </div>
        <BackendBanner />
        <Routes key={generation}>
          <Route path="/" element={<Overview />} />
          <Route path="/chains" element={<Chains />} />
          <Route path="/wallets" element={<Wallets />} />
          <Route path="/wallets/:chain/:address/:tab?" element={<WalletWorkspacePage />} />
          <Route path="/flows" element={<CapitalFlows />} />
          <Route path="/graph" element={<GraphPage />} />
          <Route path="/explorer" element={<GraphExplorerPage />} />
          <Route path="/opportunities" element={<Opportunities />} />
          <Route path="/strategies" element={<Strategies />} />
          <Route path="/curation" element={<Curation />} />
          <Route path="/research" element={<Research />} />
<<<<<<< HEAD
          <Route path="/research/runs/:runId" element={<RunPage />} />
=======
          <Route path="/cases" element={<CaseWorkbench />} />
          <Route path="/cases/:id" element={<CaseDetailPage />} />
>>>>>>> 7ec5992 (Implement BIP Crypto Detective & Investigation Platform (Phases 1-3))
          <Route path="/investigations" element={<Investigations />} />
          <Route path="/investigations/:id" element={<InvestigationDetail />} />
          <Route path="/providers" element={<Providers />} />
          <Route path="/providers/:id" element={<ProviderDetail />} />
          <Route path="/watchlists" element={<Watchlists />} />
          <Route path="/signals" element={<Signals />} />
          <Route path="/signals/:id" element={<SignalDetailPage />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/coverage" element={<Coverage />} />
          <Route path="/models" element={<Models />} />
          <Route path="/attribution" element={<Attribution />} />
          <Route path="/system" element={<System />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  );
}
