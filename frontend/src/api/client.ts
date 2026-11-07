import type {
  CoverageResponse, DealsResponse, EventsResponse, HistoryResponse, ListingPage,
  Listing, ResearchResponse, SourcesResponse,
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

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { headers: { Accept: 'application/json' }, signal });
  } catch (err) {
    if ((err as Error).name === 'AbortError') throw err;
    throw new ApiError(0, 'Could not reach Watchtower. Check your connection and try again.');
  }
  if (!res.ok) {
    let detail = '';
    try {
      const body = (await res.json()) as { error?: string; detail?: string };
      detail = body.detail ?? body.error ?? '';
    } catch {
      /* non-JSON error page: fall through to the generic message */
    }
    const messages: Record<number, string> = {
      404: 'That item is not in the collected data.',
      422: 'Some of the inputs were not valid. Check the filters and try again.',
      429: 'Too many requests. Wait a minute and retry.',
    };
    throw new ApiError(res.status, detail || messages[res.status] || `The server returned an error (${res.status}).`);
  }
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
}

export const api = {
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
};
