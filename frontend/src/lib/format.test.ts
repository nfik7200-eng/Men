import { describe, expect, it } from 'vitest';
import { formatDate, formatMoney, formatPercent, percentChange, timeAgo } from './format';

describe('formatMoney', () => {
  it('shows minor units as a currency amount', () => {
    expect(formatMoney(1905, 'USD')).toBe('$19.05');
  });

  it('never displays an unknown price as zero', () => {
    expect(formatMoney(null, 'USD')).toBe('Price not stated');
    expect(formatMoney(undefined, 'USD')).toBe('Price not stated');
    expect(formatMoney(null, 'USD')).not.toContain('0');
  });

  it('shows the ISO code, never an invented symbol, for an unknown currency', () => {
    const out = formatMoney(500, 'XYZ');
    expect(out).toContain('XYZ');
    expect(out).toContain('5.00');
    expect(out).not.toMatch(/[$£€¥]/);
  });

  it('falls back to amount plus code if Intl rejects the currency entirely', () => {
    expect(formatMoney(500, 'us')).toBe('5.00 us');
  });
});

describe('percentChange', () => {
  it('computes the change against a known reference price', () => {
    expect(percentChange(20000, 18000)).toBeCloseTo(-10);
  });

  it('returns null when either price is unknown or the reference is zero', () => {
    expect(percentChange(null, 100)).toBeNull();
    expect(percentChange(100, null)).toBeNull();
    expect(percentChange(0, 100)).toBeNull();
  });
});

describe('timeAgo', () => {
  const now = new Date('2026-11-07T12:00:00Z');
  it('reads naive server timestamps as UTC', () => {
    expect(timeAgo('2026-11-07T09:00:00', now)).toBe('3 h ago');
  });
  it('says "never" for a missing timestamp rather than a fake date', () => {
    expect(timeAgo(null, now)).toBe('never');
  });
  it('uses minutes, hours and days', () => {
    expect(timeAgo('2026-11-07T11:59:40Z', now)).toBe('just now');
    expect(timeAgo('2026-11-07T11:30:00Z', now)).toBe('30 min ago');
    expect(timeAgo('2026-11-05T12:00:00Z', now)).toBe('2 days ago');
  });
});

describe('formatDate', () => {
  it('formats in UTC with an explicit zone', () => {
    expect(formatDate('2026-11-06T13:36:00')).toBe('2026-11-06 13:36 UTC');
    expect(formatDate(null)).toBe('—');
  });
});

describe('formatPercent', () => {
  it('shows an em dash for an unknown value', () => {
    expect(formatPercent(null)).toBe('—');
    expect(formatPercent(16.4)).toBe('16%');
  });
});
