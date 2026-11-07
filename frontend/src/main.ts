// Entry point. Each server-rendered page marks the island it needs with data-island="<name>";
// only those islands are mounted, so pages that do not need the client stay pure HTML.
import { mountDiscovery } from './pages/discovery';
import { mountListingChart } from './pages/listingChart';
import { mountDeals } from './pages/deals';
import { mountResearch } from './pages/research';
import { mountCoverage } from './pages/coverage';

const islands: Record<string, (root: HTMLElement) => void> = {
  discovery: mountDiscovery,
  'listing-chart': mountListingChart,
  deals: mountDeals,
  research: mountResearch,
  coverage: mountCoverage,
};

for (const el of document.querySelectorAll<HTMLElement>('[data-island]')) {
  const name = el.dataset.island ?? '';
  const mount = islands[name];
  if (mount) mount(el);
  else el.textContent = `Unknown interface component: ${name}.`;
}
