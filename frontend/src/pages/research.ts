import { api, ApiError } from '../api/client';
import { h, status } from '../components/dom';
import { formatDate, formatMoney } from '../lib/format';
import type { Listing, ResearchPick, ResearchResponse } from '../api/types';

// Examples taken from the brief. They are suggestions for the person, not product data.
const EXAMPLES = [
  'cheapest raspberry pi 5 8gb',
  'raspberry pi 5 8gb',
  'which products have insufficient price history?',
  'has the price of the raspberry pi 5 dropped recently?',
];

/**
 * Natural-language research over the collected data. Every answer comes from the server's retrieval, and each
 * recommendation links to the exact listing it is based on. Caveats and gaps are shown, never hidden.
 */
export function mountResearch(root: HTMLElement): void {
  // The island replaces the server form, so hide the no-JavaScript copy now that the island is running.
  document.querySelector<HTMLElement>('.research-server-form')?.setAttribute('hidden', '');
  document.querySelector<HTMLElement>('.research-server-result')?.setAttribute('hidden', '');
  const form = h('form', { class: 'research-form', role: 'search', 'aria-label': 'Ask the research assistant' });
  const input = h('input', {
    type: 'search', name: 'q', required: true, minlength: 3, maxlength: 500,
    placeholder: 'Ask about a product, e.g. cheapest raspberry pi 5 8gb',
    'aria-label': 'Question for the research assistant', autocomplete: 'off',
  }) as HTMLInputElement;
  const submit = h('button', { type: 'submit' }, 'Ask');
  form.append(
    h('div', { class: 'research-input-row' }, input, submit),
    h('div', { class: 'examples' }, h('span', { class: 'muted small' }, 'Try: '),
      ...EXAMPLES.map((ex) => h('button', { type: 'button', class: 'chip', onclick: () => { input.value = ex; input.focus(); } }, ex))),
  );
  form.classList.add('research-form');
  const out = h('div', { class: 'research-out', 'aria-live': 'polite' });
  root.replaceChildren(form, out);

  let current: AbortController | null = null;
  const ask = async (q: string): Promise<void> => {
    current?.abort();
    const mine = new AbortController();
    current = mine;
    submit.setAttribute('disabled', '');
    out.replaceChildren(status('Searching the collected data…'));
    try {
      const res = await api.research(q, mine.signal);
      render(out, res);
    } catch (err) {
      if ((err as Error).name === 'AbortError') return; // a newer question replaced this one
      const msg = err instanceof ApiError ? err.message : 'The question could not be answered.';
      out.replaceChildren(status(msg, 'error'));
    } finally {
      // Only the request that is still current may re-enable the button; an aborted request must not.
      if (current === mine) {
        submit.removeAttribute('disabled');
        input.focus(); // the button was disabled while it had focus: return focus to the question
      }
    }
  };

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (q.length < 3) {
      out.replaceChildren(status('Type at least 3 characters so the question can be matched against the data.', 'error'));
      input.focus();
      return;
    }
    const url = new URL(window.location.href);
    url.searchParams.set('q', q);
    window.history.replaceState(null, '', url);
    void ask(q);
  });

  // Share-friendly: a question in the URL is answered on load.
  const initial = new URLSearchParams(window.location.search).get('q')?.trim() ?? '';
  if (initial.length >= 3) { input.value = initial; void ask(initial); }
}

function render(out: HTMLElement, res: ResearchResponse): void {
  // The answer is rendered from the structured picks, not from the assistant's plain-text block: its first line is
  // the first pick, not a summary, so quoting it would read as a conclusion the assistant did not reach.
  const nodes: Node[] = [];
  if (res.caveats.length) {
    nodes.push(h('ul', { class: 'caveats', 'aria-label': 'Caveats' },
      ...res.caveats.map((c) => h('li', {}, c))));
  }

  if (res.picks.length === 0) {
    nodes.push(status('Nothing in the collected data matches this question yet. Try fewer words, or check Coverage for what has been collected.'));
  } else {
    nodes.push(h('ol', { class: 'picks' }, ...res.picks.map(pickCard)));
  }

  if (res.gaps.length) {
    nodes.push(h('div', { class: 'gaps' }, h('h3', {}, 'Not in our data'),
      h('ul', {}, ...res.gaps.map((g) => h('li', {}, g)))));
  }
  nodes.push(h('p', { class: 'muted small' },
    `Based on ${res.retrieved_listing_ids.length} collected listing${res.retrieved_listing_ids.length === 1 ? '' : 's'}. `,
    'Prices are as last observed; check the store before you buy.'));
  out.replaceChildren(...nodes);
}

function pickCard(p: ResearchPick): HTMLElement {
  const l: Listing = p.listing;
  const verified = l.match_status === 'confirmed';
  const badges: Node[] = [];
  if (l.source_kind !== 'live') badges.push(h('span', { class: 'badge warn' }, 'Test data, not a real shop'));
  if (l.match_status === 'confirmed') badges.push(h('span', { class: 'badge ok' }, 'Match confirmed by a person'));
  else if (l.match_status === 'auto') badges.push(h('span', { class: 'badge' }, 'Automatic match'));
  if (l.availability === 'out_of_stock') badges.push(h('span', { class: 'badge bad' }, 'Out of stock'));
  if (l.price_status === 'stale') badges.push(h('span', { class: 'badge warn' }, 'Stale price'));
  return h('li', { class: 'pick' },
    h('div', { class: 'pick-head' },
      h('a', { href: `/listing/${l.id}`, class: 'pick-title' }, l.title),
      h('span', { class: 'pick-price' }, formatMoney(l.price_minor, l.currency)),
    ),
    h('div', { class: 'muted small' }, `${l.source} · last observed ${formatDate(l.last_observed_at)}`),
    h('div', { class: 'badges' }, ...badges),
    p.meets.length ? h('p', { class: 'meets' }, 'Meets: ', p.meets.join('; ')) : null,
    p.misses.length ? h('p', { class: 'misses' }, 'Falls short: ', p.misses.join('; ')) : null,
    p.unknown.length ? h('p', { class: 'muted small' }, 'Not stated: ', p.unknown.join('; ')) : null,
    verified ? null : h('p', { class: 'muted small' }, 'This pairing is not yet confirmed as the same product.'),
  );
}

