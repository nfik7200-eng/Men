import { api, ApiError } from '../api/client';
import { h, status } from '../components/dom';
import { formatMoney, minorUnitDigits } from '../lib/format';
import type { WatchItem } from '../api/types';

/**
 * The watchlist, interactive. It reads the same JSON the server page is built from, so the two always agree.
 * A target price is typed in the listing's own currency and sent in major units; the server converts it with that
 * currency's exponent. Nothing is compared across currencies here.
 */
export function mountWatchlist(root: HTMLElement): void {
  document.querySelector<HTMLElement>('.server-watchlist')?.setAttribute('hidden', '');
  root.replaceChildren(status('Loading your watchlist…'));
  void load(root);
}

async function load(root: HTMLElement, notice?: string): Promise<void> {
  try {
    const res = await api.watchlist();
    render(root, res.items, res.limit, notice);
  } catch (err) {
    const msg = err instanceof ApiError ? err.message : 'Your watchlist could not be loaded.';
    root.replaceChildren(
      status(msg, 'error'),
      h('button', { type: 'button', class: 'secondary', onclick: () => { root.replaceChildren(status('Loading your watchlist…')); void load(root); } }, 'Try again'),
    );
  }
}

function render(root: HTMLElement, items: WatchItem[], limit: number, notice?: string): void {
  const head = h('p', { class: 'muted small' }, `${items.length} of ${limit} watched item${limit === 1 ? '' : 's'} on your plan.`);
  const message = notice ? status(notice) : '';
  if (items.length === 0) {
    root.replaceChildren(head, message, status('Nothing watched yet. Open a product and press “Watch”.'));
    return;
  }
  root.replaceChildren(head, message, ...items.map((item) => card(root, item)));
}

function card(root: HTMLElement, w: WatchItem): HTMLElement {
  const alert = w.triggered ? h('span', { class: 'badge ok' }, 'Alert') : null;
  const digits = minorUnitDigits(w.currency);
  const targetValue = w.target_price_minor === null ? '' : (w.target_price_minor / 10 ** digits).toFixed(digits);
  const note = h('div', { class: 'small', role: 'status' });
  const input = h('input', {
    type: 'number', min: '0', step: 'any', inputmode: 'decimal', value: targetValue,
    'aria-label': `Target price for ${w.title} in ${w.currency}`, placeholder: `Target in ${w.currency}`,
  });
  const save = h('button', {
    type: 'button', class: 'secondary',
    onclick: async () => {
      const raw = input.value.trim();
      const target = raw === '' ? null : Number(raw);
      if (target !== null && (!Number.isFinite(target) || target <= 0)) {
        note.replaceChildren(status('Enter a target above zero, or leave it empty to clear it.', 'error'));
        return;
      }
      try {
        await api.watchSet(w.listing_id, target);
        await load(root, target === null ? 'Target cleared.' : `Target saved for ${w.title}.`);
      } catch (err) {
        note.replaceChildren(status(err instanceof ApiError ? err.message : 'The target could not be saved.', 'error'));
      }
    },
  }, 'Save target');
  const remove = h('button', {
    type: 'button', class: 'secondary',
    onclick: async () => {
      try {
        await api.watchRemove(w.listing_id);
        await load(root, `${w.title} was removed from your watchlist.`);
      } catch (err) {
        note.replaceChildren(status(err instanceof ApiError ? err.message : 'The item could not be removed.', 'error'));
      }
    },
  }, 'Remove');
  return h('article', { class: 'card watch-card' },
    h('h3', { class: 'deal-title' }, h('a', { href: `/listing/${w.listing_id}` }, w.title), ' ', alert),
    h('div', { class: 'price' }, w.price_minor === null ? 'Price not stated' : formatMoney(w.price_minor, w.currency)),
    h('div', { class: 'muted small' }, `${w.source} · target ${w.target_price_minor === null ? 'none' : formatMoney(w.target_price_minor, w.currency)}`),
    ...w.reasons.map((r) => h('p', { class: 'small' }, `• ${r}`)),
    h('div', { class: 'watch-actions' },
      h('label', { class: 'small' }, 'Target ', input),
      save, remove),
    note,
  );
}
