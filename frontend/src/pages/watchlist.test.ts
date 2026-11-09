import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountWatchlist } from './watchlist';
import type { WatchItem, WatchlistResponse } from '../api/types';

const flush = () => new Promise((r) => setTimeout(r, 0));
const item = (over: Partial<WatchItem> = {}): WatchItem => ({
  listing_id: 7, title: 'Pi Zero 2 W', url: 'https://shop.example/p/7', source: 'sparkfun', currency: 'USD',
  price_minor: 1500, target_price_minor: 1200, baseline_price_minor: 1600, notify_restock: true, triggered: false,
  reasons: ['Price down 6.3% since you started watching'], ...over,
});
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

/** Routes by method and path so each test states exactly what the server answers. */
function stubApi(routes: Record<string, () => Response>) {
  const calls: Array<{ method: string; url: string; body: unknown }> = [];
  const f = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const path = String(url);
    calls.push({ method, url: path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const handler = routes[`${method} ${path}`];
    if (!handler) throw new Error(`unexpected request ${method} ${path}`);
    return handler();
  });
  vi.stubGlobal('fetch', f);
  return { calls };
}

describe('watchlist island', () => {
  let root: HTMLElement;
  beforeEach(() => {
    document.body.innerHTML = '<div class="server-watchlist">server copy</div>';
    root = document.createElement('div');
    document.body.append(root);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('hides the server copy and shows the watched item with its target and reason', async () => {
    const body: WatchlistResponse = { limit: 30, items: [item()] };
    stubApi({ 'GET /api/v1/watchlist': () => json(body) });
    mountWatchlist(root);
    await flush(); await flush();
    expect(document.querySelector<HTMLElement>('.server-watchlist')?.hidden).toBe(true);
    expect(root.textContent).toContain('Pi Zero 2 W');
    expect(root.textContent).toContain('target $12.00');
    expect(root.textContent).toContain('Price down 6.3% since you started watching');
  });

  it('saves a typed target in the listing currency and reloads', async () => {
    const before: WatchlistResponse = { limit: 30, items: [item()] };
    const after: WatchlistResponse = { limit: 30, items: [item({ target_price_minor: 1100 })] };
    let loads = 0;
    const { calls } = stubApi({
      'GET /api/v1/watchlist': () => json(loads++ === 0 ? before : after),
      'POST /api/v1/watchlist': () => json({ listing_id: 7, target_price_minor: 1100, currency: 'USD' }, 201),
    });
    mountWatchlist(root);
    await flush(); await flush();
    const input = root.querySelector<HTMLInputElement>('input[type="number"]');
    if (input) input.value = '11';
    [...root.querySelectorAll('button')].find((b) => b.textContent === 'Save target')?.click();
    await flush(); await flush(); await flush();
    const post = calls.find((c) => c.method === 'POST');
    expect(post?.body).toEqual({ listing_id: 7, target_price: 11 });   // major units; the server applies the currency rule
    expect(root.textContent).toContain('Target saved for Pi Zero 2 W.');
  });

  it('removes an item with a DELETE and shows the empty state when nothing is left', async () => {
    let loads = 0;
    const { calls } = stubApi({
      'GET /api/v1/watchlist': () => json(loads++ === 0 ? { limit: 30, items: [item()] } : { limit: 30, items: [] }),
      'DELETE /api/v1/watchlist/7': () => json({ removed: 7 }),
    });
    mountWatchlist(root);
    await flush(); await flush();
    [...root.querySelectorAll('button')].find((b) => b.textContent === 'Remove')?.click();
    await flush(); await flush(); await flush();
    expect(calls.some((c) => c.method === 'DELETE' && c.url === '/api/v1/watchlist/7')).toBe(true);
    expect(root.textContent).toContain('Nothing watched yet.');
  });

  it('explains a signed-out visitor how to start a watchlist instead of showing an empty list', async () => {
    stubApi({ 'GET /api/v1/watchlist': () => json({ error: 'Open a Watchtower page in this browser first' }, 401) });
    mountWatchlist(root);
    await flush(); await flush();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain('Open a Watchtower page');
  });

  it('refuses a non-positive target before sending anything', async () => {
    const { calls } = stubApi({ 'GET /api/v1/watchlist': () => json({ limit: 30, items: [item()] }) });
    mountWatchlist(root);
    await flush(); await flush();
    const input = root.querySelector<HTMLInputElement>('input[type="number"]');
    if (input) input.value = '-3';
    [...root.querySelectorAll('button')].find((b) => b.textContent === 'Save target')?.click();
    await flush(); await flush();
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0);
    expect(root.textContent).toContain('Enter a target above zero');
  });
});
