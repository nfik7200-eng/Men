import { api, ApiError } from '../api/client';
import type { ListingQuery } from '../api/client';
import { h, status } from '../components/dom';
import { formatMoney } from '../lib/format';
import { listingQueryString, parseListingQuery } from '../lib/query';
import type { Listing, ListingPage, MatchStatus } from '../api/types';

const MATCH_LABEL: Record<MatchStatus, string> = {
  self: 'Own product',
  auto: 'Automatic match',
  confirmed: 'Confirmed match',
  review: 'Awaiting review',
};
const PER_PAGE = 24;

/**
 * Browse and search collected listings. The URL is the source of truth (shareable, back-button safe).
 * Real-retailer data is the default; demo and sandbox rows appear only when asked for, and are labelled.
 * Prices are never converted or mixed; a missing price is shown as not stated.
 * The form stays mounted so focus is kept; only the results region is re-rendered.
 */
export function mountDiscovery(root: HTMLElement): void {
  document.querySelector<HTMLElement>('.server-search')?.setAttribute('hidden', '');
  const results = h('div', { class: 'discovery-results', 'aria-live': 'polite' });
  const onSearch = (next: Params): void => {
    history.pushState(null, '', `${location.pathname}${queryString(next)}`);
    show(next);
  };
  root.replaceChildren(searchForm(currentParams(), onSearch), results);

  let controller: AbortController | null = null;
  const show = (params: Params): void => {
    controller?.abort();
    controller = new AbortController();
    results.replaceChildren(status('Searching…'));
    void load(results, params, controller.signal, (p) => onSearch({ ...params, page: p }), (found) => offerCurrencies(root, found, params.currency ?? ''));
  };
  // Back and forward move through search history; re-run the view for the URL that is now current.
  // One listener at a time: a re-mount replaces the previous one instead of stacking another.
  if (onPopState) window.removeEventListener('popstate', onPopState);
  onPopState = () => {
    const params = currentParams();
    const form = root.querySelector<HTMLFormElement>('form');
    if (form) fillForm(form, params);
    show(params);
  };
  window.addEventListener('popstate', onPopState);
  show(currentParams());
}

let onPopState: (() => void) | null = null;

type Params = ListingQuery & { data: 'real' | 'demo' | 'all'; notice?: string };

function currentParams(): Params {
  const q = parseListingQuery(location.search);
  const raw = new URLSearchParams(location.search).get('data') ?? 'real';
  const data = raw === 'demo' || raw === 'all' ? raw : 'real';
  // A price limit compares amounts, so it needs one chosen currency. Without it the limit is dropped and the page says so,
  // the same rule the server applies (the API would refuse it with 422).
  if (!q.currency && (q.min_price !== undefined || q.max_price !== undefined)) {
    const { min_price: _min, max_price: _max, ...rest } = q;
    return { ...rest, data, notice: 'Choose a currency to use a price limit. The price limit was not applied.' };
  }
  return { ...q, data };
}

function queryString(p: Params): string {
  const base = new URLSearchParams(listingQueryString(p).replace(/^\?/, ''));
  if (p.data !== 'real') base.set('data', p.data);
  const s = base.toString();
  return s ? `?${s}` : '';
}

async function load(
  results: HTMLElement,
  params: Params,
  signal: AbortSignal,
  goToPage: (p: number) => void,
  offerCurrencies: (found: string[]) => void,
): Promise<void> {
  try {
    const page = await api.listings(params, signal);
    if (signal.aborted) return;
    offerCurrencies(page.currencies ?? []);
    const current = params.page ?? 1;
    const pages = Math.max(1, Math.ceil(page.total / PER_PAGE));
    results.replaceChildren(
      params.notice ? status(params.notice) : '',
      h('p', { class: 'muted small' },
        `${page.total} listing${page.total === 1 ? '' : 's'}${params.q ? ` for “${params.q}”` : ''}. `,
        'Prices in different currencies are never converted or mixed.'),
      mixedCurrencyNote(page, params) ?? '',
      page.items.length === 0
        ? status('No listings match. Try removing a filter.')
        : h('div', { class: 'grid', role: 'list' }, ...page.items.map(card)),
      h('nav', { class: 'pager', 'aria-label': 'Pages' },
        h('button', { type: 'button', class: 'secondary', disabled: current <= 1, onclick: () => goToPage(current - 1) }, '← Previous'),
        h('span', { class: 'muted' }, ` Page ${current} of ${pages} `),
        h('button', { type: 'button', class: 'secondary', disabled: current >= pages, onclick: () => goToPage(current + 1) }, 'Next →'),
      ),
    );
  } catch (err) {
    if ((err as Error).name === 'AbortError') return;
    const msg = err instanceof ApiError ? err.message : 'Listings could not be loaded.';
    results.replaceChildren(status(msg, 'error'));
  }
}

function searchForm(p: Params, onSearch: (next: Params) => void): HTMLFormElement {
  const f = h('form', { class: 'search island-search', role: 'search' },
    h('input', { name: 'q', 'aria-label': 'Search products', placeholder: 'Search products' }),
    h('select', { name: 'sort', 'aria-label': 'Sort' },
      h('option', { value: 'price_asc' }, 'Price, low to high'),
      h('option', { value: 'price_desc' }, 'Price, high to low'),
      h('option', { value: 'name' }, 'Name')),
    h('select', { name: 'data', 'aria-label': 'Data' },
      h('option', { value: 'real' }, 'Real retailers'),
      h('option', { value: 'demo' }, 'Demo / sandbox'),
      h('option', { value: 'all' }, 'Everything')),
    h('select', { name: 'currency', 'aria-label': 'Currency' }, h('option', { value: '' }, 'Any currency')),
    h('input', { type: 'number', name: 'min_price', min: '0', step: 'any', inputmode: 'decimal', placeholder: 'Min price', 'aria-label': 'Minimum price (needs a currency)' }),
    h('input', { type: 'number', name: 'max_price', min: '0', step: 'any', inputmode: 'decimal', placeholder: 'Max price', 'aria-label': 'Maximum price (needs a currency)' }),
    h('label', { class: 'small' }, h('input', { type: 'checkbox', name: 'in_stock' }), ' In stock only'),
    h('button', { type: 'submit' }, 'Apply'),
  );
  fillForm(f, p);
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    const d = new FormData(f);
    const data = String(d.get('data'));
    const currency = String(d.get('currency') ?? '');
    const typedMin = String(d.get('min_price') ?? '').trim();
    const typedMax = String(d.get('max_price') ?? '').trim();
    const hasLimit = typedMin !== '' || typedMax !== '';
    // Prefilled fields hold the limit that is already applied. A value that differs from it was typed by the person now.
    const typedNow = typedMin !== (f.dataset.prefillMin ?? '') || typedMax !== (f.dataset.prefillMax ?? '');
    const base = {
      q: String(d.get('q') ?? '').trim(),
      sort: String(d.get('sort')),
      data: (data === 'demo' || data === 'all' ? data : 'real') as Params['data'],
      in_stock: d.get('in_stock') === 'on',
      page: 1,
      currency,
    };
    const numeric = (text: string): number | undefined => (text === '' ? undefined : Number(text));
    let next: Params;
    let refused = false;
    if (hasLimit && !currency) {
      // A price limit compares amounts: it needs one currency. The typed values stay in the fields for the next try.
      refused = true;
      next = { ...base, notice: 'Choose a currency to use a price limit. The price limit was not applied.', min_price: undefined, max_price: undefined };
    } else if (hasLimit && !typedNow && currency !== (f.dataset.limitCurrency ?? '')) {
      // A limit that came from this page's own link belongs to its currency. Switching currency must not carry it over.
      next = { ...base, notice: 'That price limit was set for another currency and was not applied.', min_price: undefined, max_price: undefined };
    } else {
      next = { ...base, min_price: numeric(typedMin), max_price: numeric(typedMax) };
    }
    onSearch(next);
    fillForm(f, next);
    if (refused) {
      const min = f.elements.namedItem('min_price'), max = f.elements.namedItem('max_price');
      if (min instanceof HTMLInputElement) min.value = typedMin;
      if (max instanceof HTMLInputElement) max.value = typedMax;
    }
  });
  return f;
}

/** Lists the currencies in the matched set as choices, so a person can scope a price limit to one of them. */
function offerCurrencies(root: HTMLElement, found: string[], selected: string): void {
  const sel = root.querySelector<HTMLSelectElement>('select[name="currency"]');
  if (!sel) return;
  sel.replaceChildren(h('option', { value: '' }, 'Any currency'), ...found.map((c) => h('option', { value: c, selected: c === selected }, c)));
  if (selected && !found.includes(selected)) sel.append(h('option', { value: selected, selected: true }, selected));
}

function fillForm(f: HTMLFormElement, p: Params): void {
  const set = (name: string, value: string) => {
    const el = f.elements.namedItem(name);
    if (el instanceof HTMLInputElement || el instanceof HTMLSelectElement) el.value = value;
  };
  set('q', p.q ?? '');
  set('sort', p.sort ?? 'price_asc');
  set('data', p.data);
  set('currency', p.currency ?? '');
  set('min_price', p.min_price === undefined ? '' : String(p.min_price));
  set('max_price', p.max_price === undefined ? '' : String(p.max_price));
  f.dataset.limitCurrency = p.currency ?? '';   // the currency the applied limit belongs to
  f.dataset.prefillMin = p.min_price === undefined ? '' : String(p.min_price);
  f.dataset.prefillMax = p.max_price === undefined ? '' : String(p.max_price);
  const stock = f.elements.namedItem('in_stock');
  if (stock instanceof HTMLInputElement) stock.checked = Boolean(p.in_stock);
}

/** Shown when the matched set spans several currencies: prices cannot be ordered or limited across them. */
function mixedCurrencyNote(page: ListingPage, params: Params): HTMLElement | null {
  const found = page.currencies ?? [];
  if (found.length <= 1 || params.currency) return null;
  const fell = page.sort_fallback ? ' Sorted by name instead of price.' : '';
  return status(`These results include ${found.length} currencies (${found.join(', ')}).${fell} Choose one currency to compare prices.`);
}

function card(l: Listing): HTMLElement {
  const price = l.price_minor === null ? 'Price not stated' : formatMoney(l.price_minor, l.currency);
  const demo = l.source_kind !== 'live';
  return h('article', { class: 'card', role: 'listitem' },
    h('h2', { class: 'deal-title' }, h('a', { href: `/listing/${l.id}` }, l.title)),
    h('div', { class: 'price' }, price),
    h('div', { class: 'muted small' }, l.source, ' · ', l.availability.replace(/_/g, ' ')),
    h('div', { class: 'small' },
      demo ? h('span', { class: 'badge warn' }, 'Demo / sandbox') : null, ' ',
      h('span', { class: 'badge' }, MATCH_LABEL[l.match_status] ?? l.match_status)),
  );
}
