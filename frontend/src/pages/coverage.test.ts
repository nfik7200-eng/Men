import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountCoverage } from './coverage';
import type { CoverageResponse, CoverageSource } from '../api/types';

const flush = () => new Promise((r) => setTimeout(r, 0));
const source = (over: Partial<CoverageSource> = {}): CoverageSource => ({
  key: 'sparkfun', name: 'SparkFun Electronics', kind: 'live', discovered: 357, rejected: {}, duplicates: 0, non_product: 0,
  eligible: 357, acquired: 90, fetched_ok: 90, failed: {}, pending_retry: 0, unknown_outcome: 0, never_attempted: 267,
  deferred_budget: 0, unfetched_with_counterpart: null, eligible_coverage: 90 / 357, extraction_success_rate: 1,
  last_fetch_at: '2026-11-06T14:36:00', last_attempt_at: '2026-11-06T14:36:00', avg_seconds_per_page: 9.9,
  freshness: { listings: 90, median_age_hours: 16, max_age_hours: 30, stale: 0 }, health: 'degraded', ...over,
});
const report = (sources: CoverageSource[]): CoverageResponse => ({
  data_class: 'live', live_merchants: 2, live_listings: 282, cross_merchant_products: 6, sources,
});

describe('acquisition monitoring island', () => {
  let root: HTMLElement;
  beforeEach(() => { document.body.innerHTML = ''; root = document.createElement('div'); document.body.append(root); });
  afterEach(() => vi.restoreAllMocks());

  it('separates discovered links from collected listings', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(report([source()])), { status: 200 })));
    mountCoverage(root);
    await flush(); await flush();
    expect(root.textContent).toContain('Discovered means a link we have seen but not read');
    expect(root.textContent).toContain('357');   // discovered
    expect(root.textContent).toContain('90');    // collected
    const stages = [...root.querySelectorAll('.stage-label')].map((e) => e.textContent);
    expect(stages).toEqual(['Discovered', 'Eligible', 'Collected']);
  });

  it('shows an unknown rate as unknown, never as 0%', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(report([source({ eligible_coverage: null, extraction_success_rate: null, avg_seconds_per_page: null })])), { status: 200 })));
    mountCoverage(root);
    await flush(); await flush();
    const rate = [...root.querySelectorAll('.fact')].find((f) => f.querySelector('dt')?.textContent === 'Coverage of eligible');
    expect(rate?.querySelector('dd')?.textContent).toBe('—');  // an unknown rate is an em dash, never 0%
    expect(root.textContent).not.toContain('0%');
  });

  it('states when no score exists for the next-in-line pages instead of showing numbers', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(report([source({ next_up: [{ url: 'https://x.example/p/1', score: null, state: null, reason: null, why: [] }] })])), { status: 200 })));
    mountCoverage(root);
    await flush(); await flush();
    expect(root.textContent).toContain('score not recorded');
  });

  it('says plainly when no real shop has been collected', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(report([])), { status: 200 })));
    mountCoverage(root);
    await flush(); await flush();
    expect(root.textContent).toContain('No real shop has been collected yet');
  });

  it('offers a retry when the report cannot be loaded', async () => {
    const f = vi.fn()
      .mockRejectedValueOnce(new TypeError('network down'))
      .mockResolvedValueOnce(new Response(JSON.stringify(report([source()])), { status: 200 }));
    vi.stubGlobal('fetch', f);
    mountCoverage(root);
    await flush(); await flush();
    expect(root.textContent).toContain('Could not reach Watchtower');
    ([...root.querySelectorAll('button')].find((b) => b.textContent === 'Try again') as HTMLButtonElement).click();
    await flush(); await flush();
    expect(root.querySelector('.source-card')).not.toBeNull();
  });
});
