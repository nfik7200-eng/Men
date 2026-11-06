import { useEffect, useRef } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import { BackendBanner, HealthProvider, Sidebar } from "./components/shell";
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
  return (
    <div className="shell">
      <a className="skip-link" href="#content">
        Skip to content
      </a>
      <Sidebar />
      <main className="main" id="content" tabIndex={-1}>
        <BackendBanner />
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
