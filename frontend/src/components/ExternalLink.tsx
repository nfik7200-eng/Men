import { safeHttpUrl } from "../lib/url";

/**
 * An outbound link for an http(s) address, plain text for anything else. The text is always
 * rendered as text (React escapes it), and a link opens in a new tab without opener access.
 */
export function ExternalLink({ url, text }: { url: string; text?: string }) {
  const href = safeHttpUrl(url);
  const shown = text ?? url;
  if (href === null) return <span className="mono wrap-any">{shown}</span>;
  return (
    <a href={href} rel="noopener noreferrer" target="_blank" className="wrap-any">
      {shown}
    </a>
  );
}
