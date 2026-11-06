import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { rememberWallet } from "../lib/recent";
import { classifyQuery } from "../lib/search";

const TX_MESSAGE = "This looks like a transaction, not a wallet. Transaction lookup is not available yet.";

/** Global wallet search. Opens the wallet workspace; never starts a data retrieval. */
export function WalletSearch({ compact = false }: { compact?: boolean }) {
  const navigate = useNavigate();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const id = useId();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
      if (e.key === "/" && !typing) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const q = classifyQuery(value);
    if (q.kind === "empty") return setError(null);
    if (q.kind === "invalid") return setError(q.message);
    if (q.kind === "transaction") return setError(TX_MESSAGE);
    setError(null);
    setValue("");
    rememberWallet(q.chain, q.address);
    navigate(`/wallets/${q.chain}/${encodeURIComponent(q.address)}`);
  };

  return (
    <form className={`wallet-search ${compact ? "compact" : ""}`} role="search" onSubmit={submit}>
      <label htmlFor={`${id}-q`} className={compact ? "visually-hidden" : "field-label"}>
        Search wallet address
      </label>
      <div className="wallet-search-row">
        <input
          ref={inputRef}
          id={`${id}-q`}
          type="search"
          inputMode="text"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder="Ethereum 0x… or Solana address   ( / )"
          value={value}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-err` : `${id}-hint`}
          onChange={(e) => setValue(e.target.value)}
        />
        <button type="submit" className="btn">
          Open
        </button>
      </div>
      {error ? (
        <p className="field-error" id={`${id}-err`} role="alert">
          {error}
        </p>
      ) : (
        <p className="visually-hidden" id={`${id}-hint`}>
          Opens the wallet page. Nothing is retrieved until you choose to. Ethereum checksums are not verified.
        </p>
      )}
    </form>
  );
}
