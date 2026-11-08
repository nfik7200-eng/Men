import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountDiscovery } from './discovery';
import type { Listing, ListingPage } from '../api/types';

const flush = () => new Promise((r) => setTimeout(r, 0));
const listing = (over: Partial<Listing> = {}): Listing => ({
  id: 3, title: 'Arduino Nano', brand: null, category: 'mcu', url: 'https://shop.example/p/3', source: 'sparkfun',
  source_kind: 'live', currency: 'USD', price_minor: 2350, original_price_minor: null, availability: 'in_stock', specs: {},
  active: true, product_id: null, match_status: 'auto', match_confidence: 0.9, last_observed_at: null, price_status: 'verified',
  freshness: null, ...over,
});
const page = (items: Listing[], total = items.length): ListingPage => ({ total, page: 1, items });
const ok = (b: unknown) => new Response(JSON.stringify(b), { status: 200 });

describe('browse listings island', () => {
  let root: HTMLElement;
  beforeEach(() => {
    window.history.replaceState(null, '', '/search');
    document.body.innerHTML = '<div class="server-search">server form</div>';
    root = document.createElement('div');
    document.body.append(root);
  });
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it('hides the server copy and requests real data by default', async () => {
    const f = vi.fn(async (_u: RequestInfo | URL) => ok(page([listing()])));
    vi.stubGlobal('fetch', f);
    mountDiscovery(root);
    expect(document.querySelector<HTMLElement>('.server-search')?.hidden).toBe(true);
    await flush(); await flush();
    expect(String(f.mock.calls[0]?.[0])).toContain('data=real');
    expect(root.textContent).toContain('Arduino Nano');
  });

  it('labels a non-live row as demo data and an auto match as automatic, not confirmed', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok(page([listing({ source_kind: 'demo', match_status: 'auto' })]))));
    mountDiscovery(root);
    await flush(); await flush();
    expect(root.textContent).toContain('Demo / sandbox');
    expect(root.textContent).toContain('Automatic match');
    expect(root.textContent).not.toContain('Confirmed match');
  });

  it('shows a missing price as not stated, never as zero', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok(page([listing({ price_minor: null })]))));
    mountDiscovery(root);
    await flush(); await flush();
    expect(root.textContent).toContain('Price not stated');
  });

  it('keeps the search state in the URL and sends it on submit', async () => {
    const f = vi.fn(async (_u: RequestInfo | URL) => ok(page([listing()])));
    vi.stubGlobal('fetch', f);
    window.history.replaceState(null, '', '/search?q=arduino');
    mountDiscovery(root);
    await flush(); await flush();
    const input = root.querySelector<HTMLInputElement>('input[name="q"]');
    expect(input?.value).toBe('arduino');
    const form = root.querySelector<HTMLFormElement>('form');
    const sel = form?.querySelector<HTMLSelectElement>('select[name="data"]');
    if (sel) sel.value = 'all';
    if (input) input.value = 'nano';
    form?.dispatchEvent(new Event('submit', { cancelable: true }));
    await flush(); await flush();
    expect(window.location.search).toContain('q=nano');
    expect(window.location.search).toContain('data=all');
    expect(String(f.mock.calls.at(-1)?.[0])).toContain('data=all');
  });

  it('says when results span several currencies and asks for one', async () => {
    const body = { total: 2, page: 1, items: [listing()], currencies: ['JPY', 'USD'], sort_fallback: 'name' };
    vi.stubGlobal('fetch', vi.fn(async (_u: RequestInfo | URL) => ok(body)));
    mountDiscovery(root);
    await flush(); await flush();
    expect(root.textContent).toContain('These results include 2 currencies (JPY, USD).');
    expect(root.textContent).toContain('Sorted by name instead of price.');
  });

  it('offers no next page beyond the last one', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok(page([listing()], 1))));
    mountDiscovery(root);
    await flush(); await flush();
    const next = [...root.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent?.includes('Next'));
    expect(next?.disabled).toBe(true);
  });

  it('says plainly when nothing matches and offers no invented results', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok(page([], 0))));
    mountDiscovery(root);
    await flush(); await flush();
    expect(root.textContent).toContain('No listings match');
  });
});
