// Money and time formatting. Money is integer minor units; never display null as 0.

const CURRENCY_LOCALE = 'en-US';

/** Minor units -> display string. Returns a clear "not stated" label for unknown prices (never "0"). */
export function formatMoney(minor: number | null | undefined, currency: string): string {
  if (minor === null || minor === undefined) return 'Price not stated';
  const major = minor / 100;
  try {
    return new Intl.NumberFormat(CURRENCY_LOCALE, { style: 'currency', currency }).format(major);
  } catch {
    // Unknown ISO code: show the amount with the code rather than guessing a symbol.
    return `${major.toFixed(2)} ${currency}`;
  }
}

/** Percentage change between a reference and a current price, or null when either is unknown. */
export function percentChange(reference: number | null, current: number | null): number | null {
  if (reference === null || current === null || reference === 0) return null;
  return ((current - reference) / reference) * 100;
}

/** "3 h ago", "2 days ago". Accepts an ISO timestamp; null when absent. */
export function timeAgo(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return 'never';
  const then = new Date(iso.endsWith('Z') || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  const minutes = Math.round((now.getTime() - then.getTime()) / 60000);
  if (Number.isNaN(minutes)) return 'unknown';
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso.endsWith('Z') || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
}

export function formatPercent(value: number | null, digits = 0): string {
  return value === null ? '—' : `${value.toFixed(digits)}%`;
}
