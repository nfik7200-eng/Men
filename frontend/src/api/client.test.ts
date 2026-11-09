import { describe, it, expect, vi, afterEach } from 'vitest';
import { api, ApiError } from './client';

afterEach(() => vi.restoreAllMocks());

describe('admin settings client', () => {
  it('posts a strict boolean to the per-source enabled route', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ key: 'adafruit', enabled: false }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const out = await api.setSourceEnabled('adafruit', false);
    expect(out).toEqual({ key: 'adafruit', enabled: false });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/v1/admin/sources/adafruit/enabled');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ enabled: false });
  });

  it('turns a 403 into an ApiError with the status kept', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'Admin authentication required.' }), { status: 403 })));
    const err = await api.adminSettings().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 403 });
  });
});
