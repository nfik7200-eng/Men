import { describe, expect, it } from 'vitest';
import type { Observation } from '../api/types';
import { buildPriceChart } from './chart';

const obs = (day: number, price: number | null, avail: Observation['availability'] = 'in_stock'): Observation => ({
  observed_at: `2026-11-${String(day).padStart(2, '0')}T12:00:00`,
  price_minor: price,
  original_price_minor: null,
  currency: 'USD',
  availability: avail,
  shipping_minor: null,
});
const opts = { width: 640, height: 200 };

describe('buildPriceChart', () => {
  it('draws nothing for zero or one real observation (no trend from a single point)', () => {
    expect(buildPriceChart([], opts)).toBeNull();
    expect(buildPriceChart([obs(1, 2000)], opts)).toBeNull();
  });

  it('never plots an unknown price as zero', () => {
    expect(buildPriceChart([obs(1, null), obs(2, null), obs(3, 2000)], opts)).toBeNull();
  });

  it('plots only the real points it was given, in time order', () => {
    const c = buildPriceChart([obs(3, 1800), obs(1, 2000)], opts)!;
    expect(c.points.map((p) => p.price_minor)).toEqual([2000, 1800]);
    expect(c.start.startsWith('2026-11-01')).toBe(true);
    expect(c.end.startsWith('2026-11-03')).toBe(true);
  });

  it('does not invent a y-range for flat prices', () => {
    const c = buildPriceChart([obs(1, 6795), obs(2, 6795)], opts)!;
    expect(c.flat).toBe(true);
    expect(c.min_minor).toBe(6795);
    expect(c.max_minor).toBe(6795);
    // Mid-height, not stretched to a fake +/-5% band.
    const ys = c.points.map((p) => p.y);
    expect(new Set(ys).size).toBe(1);
  });

  it('spans the full height for a real movement', () => {
    const c = buildPriceChart([obs(1, 2000), obs(2, 1800)], opts)!;
    expect(c.flat).toBe(false);
    const [hi, lo] = [c.points[0]!.y, c.points[1]!.y];
    expect(hi).toBeLessThan(lo); // higher price is higher on the chart (smaller y)
  });

  it('draws a step path: a price holds until the next observation', () => {
    const c = buildPriceChart([obs(1, 2000), obs(2, 1800)], opts)!;
    expect(c.points).toHaveLength(2);
    const a = c.points[0]!;
    const b = c.points[1]!;
    // Horizontal hold at the first price up to the second observation's x, then down.
    expect(c.path).toBe(`M${a.x.toFixed(1)},${a.y.toFixed(1)} L${b.x.toFixed(1)},${a.y.toFixed(1)} L${b.x.toFixed(1)},${b.y.toFixed(1)}`);
  });
});

describe('usable price rule (one definition for chart and facts)', () => {
  it('excludes zero and negative prices from the chart and says so', () => {
    expect(buildPriceChart([obs(1, 0), obs(2, 2000)], opts)).toBeNull();
    expect(buildPriceChart([obs(1, -500), obs(2, 2000), obs(3, 1800)], opts)!.points.map((p) => p.price_minor)).toEqual([2000, 1800]);
  });

  it('refuses to draw a trend across currencies (no FX is applied)', () => {
    const mixed: Observation[] = [
      { ...obs(1, 2000), currency: 'USD' },
      { ...obs(2, 1500), currency: 'EUR' },
    ];
    expect(buildPriceChart(mixed, opts)).toBeNull();
  });
});
