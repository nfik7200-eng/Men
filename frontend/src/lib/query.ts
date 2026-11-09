// URL <-> filter state. Lets people share and reload a search, and keeps the back button meaningful.
import type { ListingQuery } from '../api/client';

export function parseListingQuery(search: string): ListingQuery {
  const p = new URLSearchParams(search);
  const num = (k: string) => {
    const v = p.get(k);
    if (v === null || v === '') return undefined;
    const n = Number(v);
    return Number.isFinite(n) && n >= 0 ? n : undefined;
  };
  const page = Math.max(1, Math.min(1000, Math.trunc(num('page') ?? 1)));
  return {
    q: p.get('q') ?? '',
    category: p.get('category') ?? '',
    source: p.get('source') ?? '',
    currency: p.get('currency') ?? '',
    min_price: num('min_price'),
    max_price: num('max_price'),
    in_stock: p.get('in_stock') === '1',
    sort: p.get('sort') ?? 'price_asc',
    page,
    per_page: 24,
  };
}

export function listingQueryString(q: ListingQuery): string {
  const p = new URLSearchParams();
  const put = (k: string, v: string | number | boolean | undefined) => {
    if (v === undefined || v === '' || v === false) return;
    p.set(k, v === true ? '1' : String(v));
  };
  put('q', q.q);
  put('category', q.category);
  put('source', q.source);
  put('currency', q.currency);
  put('min_price', q.min_price);
  put('max_price', q.max_price);
  put('in_stock', q.in_stock);
  if (q.sort && q.sort !== 'price_asc') put('sort', q.sort);
  if (q.page && q.page > 1) put('page', q.page);
  const s = p.toString();
  return s ? `?${s}` : '';
}
