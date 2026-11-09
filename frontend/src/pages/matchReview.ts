import { api, ApiError } from '../api/client';
import { h, status } from '../components/dom';
import { formatMoney } from '../lib/format';
import type { MatchCandidate, MatchSide } from '../api/types';

/**
 * Admin review of uncertain product matches, interactive. A merge changes how prices are compared, so it asks first.
 * The server decides; this page only shows the evidence and sends the decision. Signed-out visitors are told to sign in.
 */
export function mountMatchReview(root: HTMLElement): void {
  document.querySelector<HTMLElement>('.server-match')?.setAttribute('hidden', '');
  root.replaceChildren(status('Loading the review queue…'));
  void load(root);
}

async function load(root: HTMLElement, notice?: string): Promise<void> {
  try {
    const res = await api.matchCandidates();
    render(root, res.items, notice);
  } catch (err) {
    const denied = err instanceof ApiError && (err.status === 401 || err.status === 403);
    const msg = denied ? 'Sign in to the admin area to review matches.'
      : err instanceof ApiError ? err.message : 'The review queue could not be loaded.';
    root.replaceChildren(
      status(msg, 'error'),
      ...(denied ? [] : [h('button', { type: 'button', class: 'secondary', onclick: () => { root.replaceChildren(status('Loading the review queue…')); void load(root); } }, 'Try again')]),
    );
  }
}

function render(root: HTMLElement, items: MatchCandidate[], notice?: string): void {
  const message = notice ? status(notice) : '';
  if (items.length === 0) {
    root.replaceChildren(message, status('No uncertain matches awaiting review.'));
    return;
  }
  root.replaceChildren(message, h('p', { class: 'muted small' }, `${items.length} question${items.length === 1 ? '' : 's'} waiting for a person.`),
    ...items.map((c) => card(root, c)));
}

function side(label: string, l: MatchSide): HTMLElement {
  const price = l.price_minor === null ? 'Price not stated' : formatMoney(l.price_minor, l.currency);
  return h('div', { class: 'match-side' },
    h('div', { class: 'small muted' }, label),
    h('div', {}, l.title),
    h('div', { class: 'small' }, `${l.source} · ${price} · ${l.availability.replace(/_/g, ' ')}`));
}

function card(root: HTMLElement, c: MatchCandidate): HTMLElement {
  const note = h('div', { class: 'small', role: 'status' });
  const decide = (decision: 'accept' | 'reject', label: string) => async () => {
    if (decision === 'accept' && !window.confirm('Merge these listings into one product? This changes how prices are compared.')) return;
    try {
      await api.matchDecide(c.id, decision);
      await load(root, `Decision recorded: ${label}.`);
    } catch (err) {
      note.replaceChildren(status(err instanceof ApiError ? err.message : 'The decision could not be saved.', 'error'));
    }
  };
  return h('article', { class: 'card match-card' },
    h('p', { class: 'small muted' }, `Evidence score ${c.score.toFixed(2)} · a person decides`),
    ...c.reasons.map((r) => h('p', { class: 'small' }, `• ${r}`)),
    side('Listing under review', c.listing),
    ...c.members.slice(0, 5).map((m) => side('Already in this product', m)),
    c.differing.length ? h('p', { class: 'small' }, `Specs that differ: ${c.differing.join(', ')}`) : h('p', { class: 'small muted' }, 'No differing specs recorded.'),
    h('div', { class: 'watch-actions' },
      h('button', { type: 'button', onclick: decide('accept', 'same product, merged') }, 'Same product — merge'),
      h('button', { type: 'button', class: 'secondary', onclick: decide('reject', 'different products') }, 'Different products')),
    note,
  );
}
