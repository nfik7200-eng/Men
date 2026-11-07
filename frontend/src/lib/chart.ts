// Pure geometry for the price-history chart. It draws ONLY the observations it is given, as step points
// (a price holds until the next observation). It never interpolates, never extends the line past the last
// observation, and never invents an axis range for flat data.
import type { Observation } from '../api/types';

export interface ChartPoint {
  x: number;
  y: number;
  price_minor: number;
  observed_at: string;
}

export interface ChartModel {
  points: ChartPoint[];
  path: string;
  min_minor: number;
  max_minor: number;
  flat: boolean;
  start: string;
  end: string;
}

export interface ChartOptions {
  width: number;
  height: number;
  pad?: number;
}

/**
 * Returns null unless there are at least two real, priced observations. A single point is shown as a
 * number by the caller, not as a line, so nobody reads a trend into one collection.
 */
export function buildPriceChart(observations: Observation[], opts: ChartOptions): ChartModel | null {
  const priced = observations
    .filter((o): o is Observation & { price_minor: number } => o.price_minor !== null && o.price_minor > 0)
    .map((o) => ({ t: Date.parse(normalise(o.observed_at)), price_minor: o.price_minor, observed_at: o.observed_at }))
    .filter((o) => Number.isFinite(o.t))
    .sort((a, b) => a.t - b.t);
  if (priced.length < 2) return null;

  const pad = opts.pad ?? 14;
  const innerW = opts.width - pad * 2;
  const innerH = opts.height - pad * 2;
  const t0 = priced[0]!.t;
  const t1 = priced[priced.length - 1]!.t;
  const span = Math.max(t1 - t0, 1);
  const values = priced.map((p) => p.price_minor);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const flat = min === max;
  // Flat data sits mid-height. No invented +/- range: the line says "unchanged" honestly.
  const yFor = (v: number) => (flat ? pad + innerH / 2 : pad + (1 - (v - min) / (max - min)) * innerH);
  const xFor = (t: number) => pad + ((t - t0) / span) * innerW;

  const points: ChartPoint[] = priced.map((p) => ({ x: xFor(p.t), y: yFor(p.price_minor), price_minor: p.price_minor, observed_at: p.observed_at }));
  // Step path: hold each price until the next observation, then move.
  const segs: string[] = [];
  points.forEach((pt, i) => {
    if (i === 0) segs.push(`M${pt.x.toFixed(1)},${pt.y.toFixed(1)}`);
    else {
      const prev = points[i - 1]!;
      segs.push(`L${pt.x.toFixed(1)},${prev.y.toFixed(1)}`, `L${pt.x.toFixed(1)},${pt.y.toFixed(1)}`);
    }
  });
  return {
    points,
    path: segs.join(' '),
    min_minor: min,
    max_minor: max,
    flat,
    start: priced[0]!.observed_at,
    end: priced[priced.length - 1]!.observed_at,
  };
}

function normalise(iso: string): string {
  return iso.endsWith('Z') || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`;
}
