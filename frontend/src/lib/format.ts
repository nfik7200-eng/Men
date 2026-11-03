const numberFormat = new Intl.NumberFormat("en-US");

export const fmtInt = (n: number): string => numberFormat.format(n);

export function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toISOString().replace("T", " ").slice(0, 19) + " UTC";
}

export function fmtBytes(n: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export const shortId = (id: string, keep = 6): string =>
  id.length <= keep * 2 + 1 ? id : `${id.slice(0, keep + (id.startsWith("0x") ? 2 : 0))}…${id.slice(-keep)}`;

export const humanize = (s: string): string => s.replace(/[_.]/g, " ");
