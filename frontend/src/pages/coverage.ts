import { api, ApiError } from '../api/client';
import { h, status } from '../components/dom';
import { formatDate, formatPercent, timeAgo } from '../lib/format';
import type { CoverageResponse, CoverageSource } from '../api/types';

/**
 * Acquisition monitoring from the live coverage report. A discovered URL is shown as DISCOVERED, never as a product:
 * only collected listings are counted as collected. Unknown rates are shown as unknown, never as zero.
 */
export function mountCoverage(root: HTMLElement): void {
  // The island replaces the server copy of the report, so hide that copy now that the island is running.
  document.querySelector<HTMLElement>('.server-coverage')?.setAttribute('hidden', '');
  root.replaceChildren(status('Loading acquisition data…'));
  void load(root);
}

async function load(root: HTMLElement): Promise<void> {
  try {
    render(root, await api.coverage());
  } catch (err) {
    const msg = err instanceof ApiError ? err.message : 'Acquisition data could not be loaded.';
    root.replaceChildren(
      status(msg, 'error'),
      h('button', { type: 'button', class: 'secondary', onclick: () => { root.replaceChildren(status('Loading acquisition data…')); void load(root); } }, 'Try again'),
    );
  }
}

function render(root: HTMLElement, c: CoverageResponse): void {
  const headline = h('dl', { class: 'facts' },
    fact('Real shops monitored', String(c.live_merchants)),
    fact('Collected listings', String(c.live_listings)),
    fact('Products sold by 2+ shops', String(c.cross_merchant_products)),
  );
  const note = h('p', { class: 'muted small' },
    'Discovered means a link we have seen but not read. It has no price, stock or history until it is collected. ',
    'Only collected listings count as products.');
  const sources = c.sources.map(sourceCard);
  const empty = c.sources.length === 0 ? status('No real shop has been collected yet. Coverage appears after the first collection.') : null;
  root.replaceChildren(headline, note, ...sources, ...(empty ? [empty] : []));
}

function sourceCard(s: CoverageSource): HTMLElement {
  const rejected = Object.entries(s.rejected).map(([k, v]) => `${v} ${k.replace(/_/g, ' ')}`).join(', ');
  const failed = Object.entries(s.failed).map(([k, v]) => `${v} ${k.replace(/_/g, ' ')}`).join(', ');
  return h('section', { class: 'card source-card' },
    h('h2', { class: 'source-title' }, s.name, ' ', h('span', { class: `badge ${s.health === 'healthy' ? 'ok' : 'warn'}` }, s.health)),
    h('div', { class: 'funnel', role: 'list', 'aria-label': `${s.name} acquisition funnel` },
      stage('Discovered', s.discovered, 'links seen'),
      stage('Eligible', s.eligible, 'allowed to read'),
      stage('Collected', s.acquired, 'read as listings'),
    ),
    h('dl', { class: 'facts' },
      fact('Coverage of eligible', formatPercent(s.eligible_coverage === null ? null : s.eligible_coverage * 100, 0)),
      fact('Extraction success', formatPercent(s.extraction_success_rate === null ? null : s.extraction_success_rate * 100, 0)),
      fact('Not collected yet', String(s.never_attempted)),
      fact('Waiting for the page budget', String(s.deferred_budget)),
      fact('Last fetch', timeAgo(s.last_fetch_at)),
      fact('Avg. time per page', s.avg_seconds_per_page === null ? 'unknown' : `${s.avg_seconds_per_page.toFixed(1)} s`),
    ),
    rejected ? h('p', { class: 'muted small' }, 'Excluded: ', rejected) : null,
    failed ? h('p', { class: 'muted small' }, 'Failed: ', failed) : null,
    s.next_up && s.next_up.length
      ? h('details', { class: 'history-table' },
          h('summary', {}, `Next in line (${s.next_up.length} shown) — discovered, not yet collected`),
          h('p', { class: 'muted small' }, 'Ranking as of the last collection run. A dash means no score has been recorded for that page yet.'),
          h('ol', { class: 'next-up' }, ...s.next_up.map((n) => h('li', {},
            h('span', { class: 'mono small' }, n.url),
            h('div', { class: 'muted small' }, n.score === null ? 'score not recorded' : `score ${n.score}`, n.why.length ? ` · ${n.why.join('; ')}` : ''),
          ))))
      : null,
    h('p', { class: 'muted small' }, `Freshness: ${s.freshness.listings} listing${s.freshness.listings === 1 ? '' : 's'}, median ${s.freshness.median_age_hours === null ? 'unknown' : `${Math.round(s.freshness.median_age_hours)} h`} since last check, ${s.freshness.stale} stale. Last attempt ${formatDate(s.last_attempt_at)}.`),
  );
}

function stage(label: string, n: number, hint: string): HTMLElement {
  return h('div', { class: 'stage', role: 'listitem' }, h('div', { class: 'stage-n' }, String(n)), h('div', { class: 'stage-label' }, label), h('div', { class: 'muted small' }, hint));
}

function fact(label: string, value: string): Node {
  return h('div', { class: 'fact' }, h('dt', {}, label), h('dd', {}, value));
}
