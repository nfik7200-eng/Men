import type {
  CoverageResponse,
  DealsResponse,
  EventsResponse,
  HistoryResponse,
  ListingPage,
  Listing,
  MatchCandidatesResponse,
  ResearchResponse,
  SourcesResponse,
  WatchlistResponse,
  AdminSettingsResponse,
} from './types';

/** A failure the UI can show in words: what went wrong and what the person can do about it. */
export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** Maps a failed response to a message the person can act on, from the server's own refusal text when it has one. */
async function errorFrom(res: Response): Promise<ApiError> {
  let detail = '';
  try {
    const body = (await res.json()) as { error?: string; detail?: string };
    detail = body.detail ?? body.error ?? '';
  } catch {
    /* non-JSON error page: fall through to the generic message */
  }
  const messages: Record<number, string> = {
    401: 'Sign in or open a Watchtower page in this browser first.',
    403: 'That action is not allowed here.',
    404: 'That item is not in the collected data.',
    409: 'That has already been decided or is already in place.',
    422: 'Some of the inputs were not valid. Check them and try again.',
    429: 'Too many requests. Wait a minute and retry.',
  };
  return new ApiError(res.status, detail || messages[res.status] || `The server returned an error (${res.status}).`);
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { headers: { Accept: 'application/json' }, signal });
  } catch (err) {
    if ((err as Error).name === 'AbortError') throw err;
    throw new ApiError(0, 'Could not reach Watchtower. Check your connection and try again.');
  }
  if (!res.ok) throw await errorFrom(res);
  return (await res.json()) as T;
}

/** A state-changing request. Same-origin only: the server checks the Origin header on every unsafe method. */
async function sendJson<T>(url: string, method: 'POST' | 'DELETE', body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers: body === undefined ? { Accept: 'application/json' } : { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, 'Could not reach Watchtower. Check your connection and try again.');
  }
  if (!res.ok) throw await errorFrom(res);
  return (await res.json()) as T;
}

function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const out = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === '' || v === false) continue;
    out.set(k, String(v));
  }
  const s = out.toString();
  return s ? `?${s}` : '';
}

export interface ListingQuery {
  q?: string;
  category?: string;
  source?: string;
  currency?: string;
  min_price?: number;
  max_price?: number;
  in_stock?: boolean;
  sort?: 'price_asc' | 'price_desc' | 'name' | 'freshness' | string;
  page?: number;
  per_page?: number;
  data?: 'real' | 'demo' | 'all';
}

export const api = {
  matchCandidates: (signal?: AbortSignal) => getJson<MatchCandidatesResponse>('/api/v1/match-candidates', signal),
  matchDecide: (candidateId: number, decision: 'accept' | 'reject') =>
    sendJson<{ id: number; decision: string }>(`/api/v1/match-candidates/${encodeURIComponent(String(candidateId))}/decision`, 'POST', { decision }),
  watchlist: (signal?: AbortSignal) => getJson<WatchlistResponse>('/api/v1/watchlist', signal),
  /** targetPrice is in MAJOR units of the listing's currency, or null to clear it. */
  watchSet: (listingId: number, targetPrice: number | null) =>
    sendJson<{ listing_id: number }>('/api/v1/watchlist', 'POST', { listing_id: listingId, target_price: targetPrice }),
  watchRemove: (listingId: number) =>
    sendJson<{ removed: number }>(`/api/v1/watchlist/${encodeURIComponent(String(listingId))}`, 'DELETE'),
  listings: (q: ListingQuery, signal?: AbortSignal) =>
    getJson<ListingPage>(`/api/v1/listings${qs({ ...q })}`, signal),
  listing: (id: number, signal?: AbortSignal) =>
    getJson<Listing>(`/api/v1/listings/${encodeURIComponent(String(id))}`, signal),
  history: (id: number, signal?: AbortSignal) =>
    getJson<HistoryResponse>(`/api/v1/listings/${encodeURIComponent(String(id))}/history`, signal),
  deals: (params: { category?: string; limit?: number } = {}, signal?: AbortSignal) =>
    getJson<DealsResponse>(`/api/v1/deals${qs({ ...params })}`, signal),
  events: (limit = 50, signal?: AbortSignal) =>
    getJson<EventsResponse>(`/api/v1/events${qs({ limit })}`, signal),
  sources: (signal?: AbortSignal) => getJson<SourcesResponse>('/api/v1/sources', signal),
  coverage: (signal?: AbortSignal) => getJson<CoverageResponse>('/api/v1/coverage', signal),
  research: (q: string, signal?: AbortSignal) =>
    getJson<ResearchResponse>(`/api/v1/research${qs({ q })}`, signal),
  /** Admin only: the token or the signed admin session is required. */
  adminSettings: (signal?: AbortSignal) => getJson<AdminSettingsResponse>('/api/v1/admin/settings', signal),
  /** Admin only. enabled must be a real boolean; the server answers with the state it stored. */
  setSourceEnabled: (key: string, enabled: boolean) =>
    sendJson<{ key: string; enabled: boolean }>(`/api/v1/admin/sources/${encodeURIComponent(key)}/enabled`, 'POST', { enabled }),
};
