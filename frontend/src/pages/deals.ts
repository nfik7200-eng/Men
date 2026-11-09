import { api, ApiError } from '../api/client';
import { h, status } from '../components/dom';
import { formatMoney } from '../lib/format';
import type { Deal, Listing } from '../api/types';

/**
 * Deal radar from the live deals endpoint. The score, label and evidence come from the server (deals.py is the
 * authority); this island only lays them out. A missing price is shown as "Price not stated", never as zero, and a
 * label is never upgraded or invented here.
 */
export function mountDeals(root: HTMLElement): void {
  // The island replaces the server copy of the table, so hide that copy now that the island is running.
  document.querySelector<HTMLElement>('.server-deals')?.setAttribute('hidden', '');
  root.replaceChildren(status('Loading deals…'));
  void load(root);
}

async function load(root: HTMLElement): Promise<void> {
  try {
    const res = await api.deals({ limit: 30 });
    render(root, res.items);
  } catch (err) {
    const msg = err instanceof ApiError ? err.message : 'Deals could not be loaded.';
    root.replaceChildren(
      status(msg, 'error'),
      h('button', { type: 'button', class: 'secondary', onclick: () => { root.replaceChildren(status('Loading deals…')); void load(root); } }, 'Try again'),
    );
  }
}

function render(root: HTMLElement, items: Listing[]): void {
  const intro = h('p', { class: 'muted' },
    'Scores are explainable sums. A verified price drop needs 3+ observations over 7+ days of our own history. ',
    'Without that, we only say a price is below comparable listings (an estimate), not that it is a proven discount.');
  if (items.length === 0) {
    root.replaceChildren(intro, status('No scored listings yet. Deals appear once real shops have been collected.'));
    return;
  }
  root.replaceChildren(intro, h('div', { class: 'deal-list' }, ...items.map(dealCard)));
}

function dealCard(l: Listing): HTMLElement {
  const d: Deal | undefined = l.deal;
  const price = l.price_minor === null ? 'Price not stated' : formatMoney(l.price_minor, l.currency);
  const scoreText = d && d.score !== null ? `${d.score} points` : 'Not scored';
  return h('article', { class: 'card deal-card' },
    h('h2', { class: 'deal-title' }, h('a', { href: `/listing/${l.id}` }, l.title)),
    h('div', { class: 'muted small' }, l.source, ' · ', l.availability.replace(/_/g, ' ')),
    h('div', { class: 'deal-price price' }, price),
    d
      ? h('div', { class: 'deal-meta' },
          h('span', { class: 'badge' }, d.label),
          ' ',
          h('span', { class: 'muted small' }, scoreText, ' · confidence ', d.confidence))
      : h('div', { class: 'muted small' }, 'Not scored'),
    d && d.warnings.length ? h('ul', { class: 'deal-warnings small' }, ...d.warnings.map((w) => h('li', {}, w))) : null,
    d && d.components.some((c) => c.points > 0)
      ? h('ul', { class: 'deal-components small' },
          ...d.components.filter((c) => c.points > 0).map((c) =>
            h('li', {}, `${c.name.replace(/_/g, ' ')}: ${c.points} of ${c.max_points} (${c.evidence}). ${c.detail}`)))
      : null,
  );
}
