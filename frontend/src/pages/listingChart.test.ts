import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountListingChart } from './listingChart';
import type { HistoryResponse } from '../api/types';

function root(id: number | string): HTMLElement {
  const el = document.createElement('div');
  el.dataset.listingId = String(id);
  document.body.append(el);
  return el;
}
const flush = () => new Promise((r) => setTimeout(r, 0));
const hist = (prices: Array<number | null>): HistoryResponse => ({
  listing_id: 1,
  observations: prices.map((p, i) => ({
    observed_at: `2026-11-0${i + 1}T12:00:00`, price_minor: p, original_price_minor: null,
    currency: 'USD', availability: 'in_stock', shipping_minor: null,
  })),
});

describe('listing price history island', () => {
  beforeEach(() => { document.body.innerHTML = ''; });
  afterEach(() => { vi.restoreAllMocks(); });

  it('refuses to load without a valid listing reference', () => {
    const el = root('abc');
    mountListingChart(el);
    expect(el.textContent).toContain('listing reference is missing');
  });

  it('draws a chart from real priced observations and states the historical low', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(hist([2000, 1800, 1800])), { status: 200 })));
    const el = root(1);
    mountListingChart(el);
    await flush(); await flush();
    expect(el.querySelector('svg.chart')).not.toBeNull();
    expect(el.textContent).toContain('Lowest seen');
    expect(el.textContent).toContain('$18.00');
  });

  it('never draws a line for a single priced collection', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(hist([2000])), { status: 200 })));
    const el = root(1);
    mountListingChart(el);
    await flush(); await flush();
    expect(el.querySelector('svg.chart')).toBeNull();
    expect(el.textContent).toContain('A trend needs at least two collections');
  });

  it('treats unknown prices as unknown, not zero', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(hist([null, null])), { status: 200 })));
    const el = root(1);
    mountListingChart(el);
    await flush(); await flush();
    expect(el.textContent).toContain('No priced collections yet');
    expect(el.textContent).not.toContain('$0');
  });

  it('shows a retry control on failure and retries when pressed', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'listing not found' }), { status: 404 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(hist([2000, 1900])), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const el = root(1);
    mountListingChart(el);
    await flush(); await flush();
    const retry = [...el.querySelectorAll('button')].find((b) => b.textContent === 'Try again');
    expect(retry).toBeDefined();
    expect(el.textContent).toContain('listing not found');
    retry!.click();
    await flush(); await flush();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(el.querySelector('svg.chart')).not.toBeNull();
  });
});

describe('listing history: unusable prices are counted and explained, never shown as prices', () => {
  beforeEach(() => { document.body.innerHTML = ''; });
  it('reports how many collections were excluded and why', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(hist([0, 2000, -500])), { status: 200 })));
    const el = root(1);
    mountListingChart(el);
    await flush(); await flush();
    expect(el.textContent).toContain('2 excluded: unknown, zero or negative price');
    // The facts never use the excluded rows as a price (the table still lists every collection, so check the facts).
    const lowest = [...el.querySelectorAll('.fact')].find((f) => f.querySelector('dt')?.textContent === 'Lowest seen');
    // One usable price is not a range: the lowest/highest facts stay unset rather than repeating a single value.
    expect(lowest?.querySelector('dd')?.textContent).toBe('—');
    expect(el.querySelector('svg.chart')).toBeNull();
  });

  it('does not draw a trend across currencies', async () => {
    const mixed = { listing_id: 1, observations: [
      { observed_at: '2026-11-01T12:00:00', price_minor: 2000, original_price_minor: null, currency: 'USD', availability: 'in_stock', shipping_minor: null },
      { observed_at: '2026-11-02T12:00:00', price_minor: 1500, original_price_minor: null, currency: 'EUR', availability: 'in_stock', shipping_minor: null },
    ] };
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(mixed), { status: 200 })));
    const el = root(1);
    mountListingChart(el);
    await flush(); await flush();
    expect(el.querySelector('svg.chart')).toBeNull();
    expect(el.textContent).toContain('more than one currency');
  });
});
