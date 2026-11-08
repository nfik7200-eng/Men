import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountDeals } from './deals';
import type { DealsResponse, Listing } from '../api/types';

const flush = () => new Promise((r) => setTimeout(r, 0));
const listing = (over: Partial<Listing> = {}): Listing => ({
  id: 7, title: 'Raspberry Pi Zero 2 W', brand: null, category: 'sbc', url: 'https://shop.example/p/7', source: 'SparkFun',
  source_kind: 'live', currency: 'USD', price_minor: 1500, original_price_minor: null, availability: 'in_stock', specs: {},
  active: true, product_id: null, match_status: 'self', match_confidence: null, last_observed_at: null, price_status: 'verified',
  freshness: null, ...over,
});
const body = (items: Listing[]): DealsResponse => ({ items });

describe('deal radar island', () => {
  let root: HTMLElement;
  beforeEach(() => {
    document.body.innerHTML = '<div class="server-deals">server copy</div>';
    root = document.createElement('div');
    document.body.append(root);
  });
  afterEach(() => vi.restoreAllMocks());

  it('hides the server copy and shows the server label and evidence', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body([listing({
      deal: { score: 42, label: 'Below comparable listings (estimate)', confidence: 'low', warnings: ['Only one day of history'],
        components: [{ name: 'price_gap', points: 30, max_points: 40, evidence: 'estimate', detail: 'below median of 4 listings' }] },
    })])), { status: 200 })));
    mountDeals(root);
    expect(document.querySelector<HTMLElement>('.server-deals')?.hidden).toBe(true);
    await flush(); await flush();
    expect(root.textContent).toContain('Below comparable listings (estimate)');
    expect(root.textContent).toContain('42 points');
    expect(root.textContent).toContain('Only one day of history');
    expect(root.textContent).toContain('price gap: 30 of 40 (estimate)');
  });

  it('shows a missing price as not stated, never as zero', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body([listing({ price_minor: null })])), { status: 200 })));
    mountDeals(root);
    await flush(); await flush();
    expect(root.textContent).toContain('Price not stated');
    expect(root.textContent).not.toMatch(/\$0|0\.00/);
  });

  it('marks an unscored listing as not scored and invents no label', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body([listing()])), { status: 200 })));
    mountDeals(root);
    await flush(); await flush();
    expect(root.textContent).toContain('Not scored');
    expect(root.textContent).not.toContain('Verified');
  });

  it('says plainly when there are no deals', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body([])), { status: 200 })));
    mountDeals(root);
    await flush(); await flush();
    expect(root.textContent).toContain('No scored listings yet');
  });

  it('offers a retry when the deals cannot be loaded', async () => {
    const f = vi.fn()
      .mockRejectedValueOnce(new TypeError('network down'))
      .mockResolvedValueOnce(new Response(JSON.stringify(body([listing()])), { status: 200 }));
    vi.stubGlobal('fetch', f);
    mountDeals(root);
    await flush(); await flush();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain('Could not reach Watchtower');
    root.querySelector<HTMLButtonElement>('button')?.click();
    await flush(); await flush();
    expect(f).toHaveBeenCalledTimes(2);
    expect(root.textContent).toContain('Raspberry Pi Zero 2 W');
  });
});
