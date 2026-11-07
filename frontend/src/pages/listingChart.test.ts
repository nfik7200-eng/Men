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
