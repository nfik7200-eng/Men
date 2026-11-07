import { api } from '../api/client';
import { ApiError } from '../api/client';
import { h, status } from '../components/dom';
import { buildPriceChart, isUsablePrice } from '../lib/chart';
import { formatDate, formatMoney, formatPercent, percentChange } from '../lib/format';
import type { HistoryResponse } from '../api/types';

const SVG_NS = 'http://www.w3.org/2000/svg';

/**
 * Historical price for ONE listing, from its real observations only. Shows: how many real collections,
 * the span, the historical low and high, and the current change against the first observed price.
 * With fewer than two priced observations it says so instead of drawing a line.
 */
export function mountListingChart(root: HTMLElement): void {
  const id = Number(root.dataset.listingId);
  if (!Number.isInteger(id) || id <= 0) {
    root.replaceChildren(status('This price history cannot be shown: the listing reference is missing.', 'error'));
    return;
  }
  root.replaceChildren(status('Loading price history…'));
  load(root, id);
}

async function load(root: HTMLElement, id: number): Promise<void> {
  try {
    const hist = await api.history(id);
    render(root, hist);
  } catch (err) {
    const msg = err instanceof ApiError ? err.message : 'The price history could not be loaded.';
    root.replaceChildren(
      status(`${msg} `, 'error'),
      h('button', { type: 'button', class: 'secondary', onclick: () => { root.replaceChildren(status('Loading price history…')); void load(root, id); } }, 'Try again'),
    );
  }
}

function render(root: HTMLElement, hist: HistoryResponse): void {
  const obs = hist.observations;
  const priced = obs.filter(isUsablePrice);
  const excluded = obs.length - priced.length;
  // Each point keeps its own currency; a mixed-currency history gets no trend (see buildPriceChart).
  const currencies = new Set(priced.map((o) => o.currency));
  const currency = priced[0]?.currency ?? obs[0]?.currency ?? 'USD';
  const mixed = currencies.size > 1;
  const chart = buildPriceChart(obs, { width: 640, height: 180 });
  const first = priced[0];
  const latest = priced[priced.length - 1];

  const summary = h('dl', { class: 'facts' },
    fact('Collections', String(obs.length)),
    fact('Priced collections', String(priced.length) + (excluded > 0 ? ` (${excluded} excluded: unknown, zero or negative price)` : '')),
    fact('Span', priced.length >= 2 ? `${formatDate(first?.observed_at)} to ${formatDate(latest?.observed_at)}` : 'Not enough history yet'),
    fact('Lowest seen', chart ? formatMoney(chart.min_minor, currency) : '—'),
    fact('Highest seen', chart ? formatMoney(chart.max_minor, currency) : '—'),
    fact('Change since first price', first && latest ? formatPercent(percentChange(first.price_minor, latest.price_minor), 1) : '—'),
  );

  const body: Node[] = [summary];
  if (mixed) {
    body.push(status('These collections are in more than one currency, so no trend is drawn: amounts are never converted without a sourced exchange rate.'));
  } else if (chart) {
    body.push(
      svgChart(chart, currency),
      h('p', { class: 'muted small' },
        chart.flat ? 'The price has not changed across these collections. ' : '',
        'Each step is a real collection. Between two collections the price is shown as unchanged, not estimated.'),
    );
  } else if (priced.length === 1) {
    body.push(status(`One priced collection so far (${formatMoney(priced[0]!.price_minor, currency)}). A trend needs at least two collections.`));
  } else {
    body.push(status('No priced collections yet. History appears after the next successful collection.'));
  }

  body.push(
    h('details', { class: 'history-table' },
      h('summary', {}, `All ${obs.length} collection${obs.length === 1 ? '' : 's'}`),
      table(['Observed (UTC)', 'Price', 'Stock'], obs.map((o) => [
        formatDate(o.observed_at),
        formatMoney(o.price_minor, o.currency),
        o.availability.replace(/_/g, ' '),
      ])),
    ),
  );
  root.replaceChildren(...body);
}

function fact(label: string, value: string): Node {
  return h('div', { class: 'fact' }, h('dt', {}, label), h('dd', {}, value));
}

function table(headers: string[], rows: string[][]): HTMLElement {
  return h('div', { class: 'scroll' },
    h('table', {},
      h('thead', {}, h('tr', {}, ...headers.map((t) => h('th', { scope: 'col' }, t)))),
      h('tbody', {}, ...rows.map((r) => h('tr', {}, ...r.map((c) => h('td', {}, c))))),
    ),
  );
}

function svgChart(model: NonNullable<ReturnType<typeof buildPriceChart>>, currency: string): SVGElement {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 640 180');
  svg.setAttribute('role', 'img');
  svg.setAttribute('class', 'chart');
  svg.setAttribute('aria-label',
    `Price history: ${model.points.length} real collections, lowest ${formatMoney(model.min_minor, currency)}, highest ${formatMoney(model.max_minor, currency)}.`);
  const path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('d', model.path);
  path.setAttribute('class', 'line');  // the design system's line style (.chart .line)
  path.setAttribute('fill', 'none');
  svg.append(path);
  for (const p of model.points) {
    const c = document.createElementNS(SVG_NS, 'circle');
    c.setAttribute('cx', p.x.toFixed(1));
    c.setAttribute('cy', p.y.toFixed(1));
    c.setAttribute('r', '4');
    c.setAttribute('class', 'dot');  // the design system's dot style (.chart .dot)
    const t = document.createElementNS(SVG_NS, 'title');
    t.textContent = `${formatDate(p.observed_at)}: ${formatMoney(p.price_minor, currency)}`;
    c.append(t);
    svg.append(c);
  }
  return svg;
}

