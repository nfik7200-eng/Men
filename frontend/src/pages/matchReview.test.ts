import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountMatchReview } from './matchReview';
import type { MatchCandidate } from '../api/types';

const flush = () => new Promise((r) => setTimeout(r, 0));
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const candidate = (over: Partial<MatchCandidate> = {}): MatchCandidate => ({
  id: 4, score: 0.62, reasons: ['Same brand and model number in both titles'],
  listing: { id: 90, title: 'Board X v2', source: 'sparkfun', currency: 'USD', price_minor: 1800, availability: 'in_stock', specs: {} },
  members: [{ id: 12, title: 'Board X', source: 'adafruit', currency: 'USD', price_minor: 1700, availability: 'in_stock', specs: {} }],
  spec_keys: ['voltage'], differing: ['voltage'], ...over,
});

function stubApi(routes: Record<string, () => Response>) {
  const calls: Array<{ method: string; url: string; body: unknown }> = [];
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    calls.push({ method, url: String(url), body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const handler = routes[`${method} ${String(url)}`];
    if (!handler) throw new Error(`unexpected ${method} ${String(url)}`);
    return handler();
  }));
  return { calls };
}

describe('match review island', () => {
  let root: HTMLElement;
  beforeEach(() => {
    document.body.innerHTML = '<div class="server-match">server queue</div>';
    root = document.createElement('div');
    document.body.append(root);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('shows both sides, the differing specs and the evidence, and hides the server queue', async () => {
    stubApi({ 'GET /api/v1/match-candidates': () => json({ items: [candidate()] }) });
    mountMatchReview(root);
    await flush(); await flush();
    expect(document.querySelector<HTMLElement>('.server-match')?.hidden).toBe(true);
    expect(root.textContent).toContain('Board X v2');
    expect(root.textContent).toContain('Already in this product');
    expect(root.textContent).toContain('Specs that differ: voltage');
    expect(root.textContent).toContain('Evidence score 0.62 · a person decides');
  });

  it('asks before a merge, and only sends the decision when the person confirms', async () => {
    let loads = 0;
    const { calls } = stubApi({
      'GET /api/v1/match-candidates': () => json(loads++ === 0 ? { items: [candidate()] } : { items: [] }),
      'POST /api/v1/match-candidates/4/decision': () => json({ id: 4, decision: 'accept' }),
    });
    vi.stubGlobal('confirm', vi.fn(() => false));
    mountMatchReview(root);
    await flush(); await flush();
    [...root.querySelectorAll('button')].find((b) => b.textContent === 'Same product — merge')?.click();
    await flush(); await flush();
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0);     // declined: nothing sent
    vi.stubGlobal('confirm', vi.fn(() => true));
    [...root.querySelectorAll('button')].find((b) => b.textContent === 'Same product — merge')?.click();
    await flush(); await flush(); await flush();
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ decision: 'accept' });
    expect(root.textContent).toContain('Decision recorded: same product, merged.');
  });

  it('sends a rejection without asking, since it changes nothing that is compared', async () => {
    let loads = 0;
    const { calls } = stubApi({
      'GET /api/v1/match-candidates': () => json(loads++ === 0 ? { items: [candidate()] } : { items: [] }),
      'POST /api/v1/match-candidates/4/decision': () => json({ id: 4, decision: 'reject' }),
    });
    const confirm = vi.fn(() => false);
    vi.stubGlobal('confirm', confirm);
    mountMatchReview(root);
    await flush(); await flush();
    [...root.querySelectorAll('button')].find((b) => b.textContent === 'Different products')?.click();
    await flush(); await flush(); await flush();
    expect(confirm).not.toHaveBeenCalled();
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ decision: 'reject' });
  });

  it('says plainly when nothing is waiting for review', async () => {
    stubApi({ 'GET /api/v1/match-candidates': () => json({ items: [] }) });
    mountMatchReview(root);
    await flush(); await flush();
    expect(root.textContent).toContain('No uncertain matches awaiting review.');
  });

  it('asks a signed-out visitor to sign in instead of showing an error code', async () => {
    stubApi({ 'GET /api/v1/match-candidates': () => json({ error: 'Admin authentication required.' }, 403) });
    mountMatchReview(root);
    await flush(); await flush();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain('Sign in to the admin area');
  });
});
