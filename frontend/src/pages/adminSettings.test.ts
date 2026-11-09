import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountAdminSettings } from './adminSettings';
import type { AdminSettingsResponse } from '../api/types';

const flush = () => new Promise((r) => setTimeout(r, 0));
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const settings = (adafruitEnabled: boolean): AdminSettingsResponse => ({
  sources: [
    { key: 'adafruit', name: 'Adafruit', kind: 'live', enabled: adafruitEnabled, configured: true, page_budget: 40 },
    { key: 'books_toscrape', name: 'Books to Scrape', kind: 'demo', enabled: true, configured: true, page_budget: null },
  ],
  scheduler: { enabled: false, env: false, ceiling: 500 },
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

describe('admin settings island', () => {
  let root: HTMLElement;
  beforeEach(() => {
    document.body.innerHTML = '<div class="server-sources">server table</div>';
    root = document.createElement('div');
    document.body.append(root);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('renders one row per source, the scheduler as read-only text, and hides the server table', async () => {
    stubApi({ 'GET /api/v1/admin/settings': () => json(settings(true)) });
    mountAdminSettings(root);
    await flush(); await flush();
    expect(document.querySelector<HTMLElement>('.server-sources')?.hidden).toBe(true);
    expect(root.querySelectorAll('tbody tr')).toHaveLength(2);
    expect(root.textContent).toContain('Scheduled collection: disabled');
    expect(root.querySelector('[data-scheduler] button')).toBeNull();
  });

  it('posts the requested change and then shows the state the server reports, not the click', async () => {
    // The admin clicks Disable on an enabled source, but the server stores it as enabled (for example, another admin
    // changed it first). The reload must show the server's state.
    let reads = 0;
    const { calls } = stubApi({
      'GET /api/v1/admin/settings': () => json(settings(true)),
      'POST /api/v1/admin/sources/adafruit/enabled': () => json({ key: 'adafruit', enabled: true }),
    });
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      calls.push({ method, url: String(url), body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (method === 'GET') { reads += 1; return json(settings(true)); }
      return json({ key: 'adafruit', enabled: true });
    }));
    mountAdminSettings(root);
    await flush(); await flush();
    (root.querySelector('[data-source="adafruit"] button') as HTMLButtonElement).click();
    await flush(); await flush(); await flush();
    const post = calls.find((c) => c.method === 'POST');
    expect(post?.body).toEqual({ enabled: false });
    expect(reads).toBeGreaterThanOrEqual(2);
    expect(root.querySelector('[data-source="adafruit"]')?.textContent).toContain('Enabled');
  });

  it('asks a signed-out visitor to sign in, with no retry button', async () => {
    stubApi({ 'GET /api/v1/admin/settings': () => json({ detail: 'Admin authentication required.' }, 403) });
    mountAdminSettings(root);
    await flush(); await flush();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain('Sign in to the admin area');
    expect(root.querySelector('button')).toBeNull();
  });

  it('shows a refusal from the server as an error, and keeps the table', async () => {
    stubApi({
      'GET /api/v1/admin/settings': () => json(settings(true)),
      'POST /api/v1/admin/sources/adafruit/enabled': () => json({ detail: 'enabled must be true or false' }, 422),
    });
    mountAdminSettings(root);
    await flush(); await flush();
    (root.querySelector('[data-source="adafruit"] button') as HTMLButtonElement).click();
    await flush(); await flush(); await flush();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain('enabled must be true or false');
    expect(root.querySelectorAll('tbody tr')).toHaveLength(2);
  });
});
