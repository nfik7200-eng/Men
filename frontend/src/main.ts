// Entry point. Each server-rendered page marks the island it needs with data-island="<name>";
// only those islands are mounted, so pages that do not need the client stay pure HTML.
import { mountDiscovery } from './pages/discovery';
import { mountListingChart } from './pages/listingChart';
import { mountDeals } from './pages/deals';
import { mountResearch } from './pages/research';
import { mountCoverage } from './pages/coverage';
import { mountWatchlist } from './pages/watchlist';
import { mountMatchReview } from './pages/matchReview';
import { mountAdminSettings } from './pages/adminSettings';

const islands: Record<string, (root: HTMLElement) => void> = {
  discovery: mountDiscovery,
  'listing-chart': mountListingChart,
  deals: mountDeals,
  research: mountResearch,
  coverage: mountCoverage,
  watchlist: mountWatchlist,
  'match-review': mountMatchReview,
  'admin-settings': mountAdminSettings,
};

for (const el of document.querySelectorAll<HTMLElement>('[data-island]')) {
  const name = el.dataset.island ?? '';
  const mount = islands[name];
  el.hidden = false; // reveal the slot only now that a script is running it
  if (mount) mount(el);
  else el.textContent = `Unknown interface component: ${name}.`;
}
