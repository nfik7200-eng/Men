import { useState, type FormEvent } from "react";
import type { DataOrigin, Scope, VerificationSummary } from "../api/types";
import { CHAINS, validateAddress } from "../lib/address";
import { fmtTime } from "../lib/format";
import { FieldError, Pill, type Tone } from "./ui";

/** Shown whenever the analysed data are deterministic offline fixtures (never real chain data). */
export function DataOriginBanner({ origin }: { origin: DataOrigin }) {
  if (origin !== "offline_fixture") return null;
  return (
    <div className="banner banner-demo" role="note">
      <strong>Demo data: offline fixtures.</strong> These records come from BIP's deterministic test
      fixtures, not from a real blockchain provider. Do not treat anything on this page as a real
      observation.
    </div>
  );
}

const VTONE: Record<string, Tone> = {
  corroborated: "ok",
  "partially corroborated": "info",
  unverified: "warn",
  conflicted: "bad",
  invalidated: "bad",
  "no evidence": "idle",
};

export function VerificationPill({ label }: { label: string }) {
  return <Pill tone={VTONE[label] ?? "idle"}>{label}</Pill>;
}

export function VerificationLine({ v }: { v: VerificationSummary }) {
  const parts = Object.entries(v.counts).map(([k, n]) => `${n} ${k}`);
  return (
    <span className="verif-line">
      <VerificationPill label={v.overall} />
      {parts.length ? <span className="muted"> {parts.join(" · ")}</span> : null}
    </span>
  );
}

export function ScopeNote({ scope }: { scope: Scope }) {
  return (
    <p className="note scope-note">
      {scope.statement} Observed range: {fmtTime(scope.first_observed)} to {fmtTime(scope.last_observed)}.
      Last ingestion: {fmtTime(scope.last_ingestion_at)}.
      {scope.truncated ? " The record limit was reached; older records were not analysed." : ""}
    </p>
  );
}

/** Chain + address form that validates locally before calling `onPick`. */
export function WalletPicker({
  initialChain = "ethereum",
  initialAddress = "",
  submitLabel,
  onPick,
}: {
  initialChain?: string;
  initialAddress?: string;
  submitLabel: string;
  onPick: (chain: string, address: string) => void;
}) {
  const [chain, setChain] = useState(initialChain);
  const [address, setAddress] = useState(initialAddress);
  const [error, setError] = useState<string | null>(null);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const problem = validateAddress(chain, address);
    setError(problem);
    if (!problem) onPick(chain, address.trim());
  };
  return (
    <form className="search" onSubmit={submit} noValidate aria-label="Choose wallet">
      <label className="field">
        <span>Chain</span>
        <select value={chain} onChange={(e) => { setChain(e.target.value); setError(null); }}>
          {CHAINS.map((c) => (
            <option key={c.id} value={c.id}>{c.label}</option>
          ))}
        </select>
      </label>
      <label className="field grow">
        <span>Address</span>
        <input
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          placeholder={chain === "ethereum" ? "0x…" : "Base58 public key"}
          autoComplete="off"
          spellCheck={false}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "picker-error" : undefined}
        />
      </label>
      <button type="submit" className="btn btn-primary">{submitLabel}</button>
      <FieldError id="picker-error" message={error} />
    </form>
  );
}

export function Freshness({ at, label = "Computed" }: { at: string | null; label?: string }) {
  return <span className="muted freshness">{label} {at ? fmtTime(at) : "on request from stored data"}</span>;
}
