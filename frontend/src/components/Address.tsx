import { useState } from "react";
import { Link } from "react-router-dom";

/** Middle-truncated identifier (8 + … + 6), full value in the title and for screen readers. */
export function shortId(value: string): string {
  return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

export function Address({ value, chain, link = false, copy = true }: {
  value: string;
  chain?: string;
  /** link to the wallet workspace (needs chain) */
  link?: boolean;
  copy?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const short = shortId(value);
  const truncated = short !== value;
  const text = (
    <>
      <code className="mono addr" title={value} aria-hidden={truncated ? true : undefined}>
        {short}
      </code>
      {truncated ? <span className="visually-hidden">{value}</span> : null}
    </>
  );
  const doCopy = () => {
    void navigator.clipboard?.writeText(value).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      },
      () => undefined,
    );
  };
  return (
    <span className="addr-wrap">
      {link && chain ? <Link to={`/wallets/${chain}/${encodeURIComponent(value)}`}>{text}</Link> : text}
      {copy && value.length > 18 ? (
        <button type="button" className="btn btn-icon" aria-label="Copy address" onClick={doCopy}>
          {copied ? "Copied" : "Copy"}
        </button>
      ) : null}
    </span>
  );
}
