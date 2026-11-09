import { describe, expect, it } from 'vitest';
import { listingQueryString, parseListingQuery } from './query';

describe('query round trip', () => {
  it('keeps every filter when a search is shared as a URL', () => {
    const q = parseListingQuery('?q=pi+5&max_price=200&in_stock=1&sort=price_desc&page=3');
    expect(q).toMatchObject({ q: 'pi 5', max_price: 200, in_stock: true, sort: 'price_desc', page: 3 });
    expect(listingQueryString(q)).toBe('?q=pi+5&max_price=200&in_stock=1&sort=price_desc&page=3');
  });

  it('omits defaults so URLs stay short', () => {
    expect(listingQueryString(parseListingQuery(''))).toBe('');
  });

  it('clamps nonsense page numbers instead of sending them', () => {
    expect(parseListingQuery('?page=-4').page).toBe(1);
    expect(parseListingQuery('?page=99999').page).toBe(1000);
    expect(parseListingQuery('?page=abc').page).toBe(1);
  });

  it('ignores negative or non-numeric prices', () => {
    const q = parseListingQuery('?min_price=-5&max_price=abc');
    expect(q.min_price).toBeUndefined();
    expect(q.max_price).toBeUndefined();
  });
});
