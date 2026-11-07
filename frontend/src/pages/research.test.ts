import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountResearch } from './research';
import type { ResearchResponse, Listing } from '../api/types';

const flush = () => new Promise((r) => setTimeout(r, 0));
const listing = (over: Partial<Listing> = {}): Listing => ({
  id: 362, title: 'Raspberry Pi 5 - 8GB', brand: 'Raspberry Pi', category: 'maker-electronics', url: 'https://x.example/a',
  source: 'SparkFun Electronics', source_kind: 'live', currency: 'USD', price_minor: 18000, original_price_minor: null,
  availability: 'in_stock', specs: {}, active: true, product_id: 359, match_status: 'auto', match_confidence: 0.95,
  last_observed_at: '2026-11-06T14:26:00', price_status: 'verified', freshness: 'fresh', ...over,
});
const answer = (picks: ResearchResponse['picks'] = [], extra: Partial<ResearchResponse> = {}): ResearchResponse => ({
  text: '1. …', interpretation: {}, caveats: [], gaps: [], retrieved_listing_ids: picks.map((p) => p.listing.id),
  llm_summary: null, picks, ...extra,
});

describe('research island', () => {
  let root: HTMLElement;
  beforeEach(() => {
    document.body.innerHTML = '';
    root = document.createElement('div');
    document.body.append(root);
    window.history.replaceState(null, '', '/research');
  });
  afterEach(() => vi.restoreAllMocks());

  it('refuses a question shorter than 3 characters without calling the server', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    mountResearch(root);
    const input = root.querySelector('input') as HTMLInputElement;
    input.value = 'ab';
    root.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    await flush();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(root.textContent).toContain('at least 3 characters');
  });

  it('shows real picks with a link to the exact listing they came from', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(answer([{ listing: listing(), meets: [], misses: [], unknown: [], import_estimate: null }])), { status: 200 })));
    mountResearch(root);
    const input = root.querySelector('input') as HTMLInputElement;
    input.value = 'cheapest raspberry pi 5 8gb';
    root.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    await flush(); await flush();
    const link = root.querySelector('a.pick-title') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('/listing/362');
    expect(root.textContent).toContain('$180.00');
    expect(root.textContent).toContain('Automatic match');
  });

  it('labels an unconfirmed pairing as not yet confirmed', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(answer([{ listing: listing({ match_status: 'auto' }), meets: [], misses: [], unknown: [], import_estimate: null }])), { status: 200 })));
    mountResearch(root);
    (root.querySelector('input') as HTMLInputElement).value = 'raspberry pi 5 8gb';
    root.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    await flush(); await flush();
    expect(root.textContent).toContain('not yet confirmed as the same product');
  });

  it('marks test-catalogue results as not real shops', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(answer([{ listing: listing({ source_kind: 'demo' }), meets: [], misses: [], unknown: [], import_estimate: null }])), { status: 200 })));
    mountResearch(root);
    (root.querySelector('input') as HTMLInputElement).value = 'raspberry pi 5 8gb';
    root.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    await flush(); await flush();
    expect(root.textContent).toContain('Test data, not a real shop');
  });

  it('says plainly when nothing matches, instead of showing an empty list', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(answer([], { gaps: ['No listing for that product'] })), { status: 200 })));
    mountResearch(root);
    (root.querySelector('input') as HTMLInputElement).value = 'zzzz unobtainium';
    root.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    await flush(); await flush();
    expect(root.textContent).toContain('Nothing in the collected data matches this question yet');
    expect(root.textContent).toContain('Not in our data');
  });

  it('answers a question carried in the URL on load (shareable)', async () => {
    window.history.replaceState(null, '', '/research?q=cheapest+raspberry+pi+5+8gb');
    const fetchMock = vi.fn(async (_url: string) => new Response(JSON.stringify(answer()), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    mountResearch(root);
    await flush(); await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const requested = String(fetchMock.mock.calls[0]?.[0] ?? '');
    expect(requested).toContain('q=cheapest+raspberry+pi+5+8gb');
  });

  it('turns a server refusal into a clear message, not a blank panel', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'Some of the inputs were not valid.' }), { status: 422 })));
    mountResearch(root);
    (root.querySelector('input') as HTMLInputElement).value = 'raspberry pi';
    root.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    await flush(); await flush();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain('not valid');
  });
});
