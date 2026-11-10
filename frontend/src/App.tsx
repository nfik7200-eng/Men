import { useEffect, useRef } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import { UserMenu } from "./auth/UserMenu";
import { BackendBanner, HealthProvider, Sidebar, useHealth } from "./components/shell";
import { WalletSearch } from "./components/WalletSearch";
import CapitalFlows from "./pages/CapitalFlows";
import Chains from "./pages/Chains";
import GraphPage from "./pages/Graph";
import Investigations, { InvestigationDetail } from "./pages/Investigations";
import Opportunities from "./pages/Opportunities";
import Overview from "./pages/Overview";
import Providers from "./pages/Providers";
import ProviderDetail from "./pages/ProviderDetail";
import Research from "./pages/Research";
import RunPage from "./pages/research/RunPage";
import Settings from "./pages/Settings";
import Wallets from "./pages/Wallets";
import WalletWorkspacePage from "./pages/wallet/Workspace";
import Watchlists from "./pages/Watchlists";

export { NAV, NAV_GROUPS } from "./components/shell";

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
          <Route path="/opportunities" element={<Opportunities />} />
          <Route path="/research" element={<Research />} />
          <Route path="/research/runs/:runId" element={<RunPage />} />
          <Route path="/investigations" element={<Investigations />} />
          <Route path="/investigations/:id" element={<InvestigationDetail />} />
          <Route path="/providers" element={<Providers />} />
          <Route path="/providers/:id" element={<ProviderDetail />} />
          <Route path="/watchlists" element={<Watchlists />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  );
}
