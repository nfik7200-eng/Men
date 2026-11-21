/**
 * The normalised address when `value` is a plain http(s) URL, otherwise null. Anything else
 * (javascript:, data:, relative paths, text with spaces) must be shown as text, never as a link.
 */
export function safeHttpUrl(value: string): string | null {
  if (!/^https?:\/\/\S+$/i.test(value)) return null;
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}
