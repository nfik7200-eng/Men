import { RoleHint } from "../auth/RoleHint";
import { useCan } from "../auth/useAuth";
import { useState } from "react";
import { ApiError, api } from "../api/client";
import type { Opportunity, OpportunityRefresh } from "../api/types";
import { DataOriginBanner, VerificationLine } from "../components/intel";
import { EmptyState, ErrorState, Loading, Mono, PageHeader, Panel, Pill } from "../components/ui";
import { chainLabel } from "../lib/address";
import { fmtTime, humanize, shortId } from "../lib/format";
import { useApi } from "../lib/useApi";

function Lead({ o }: { o: Opportunity }) {
  return (
    <li className="lead">
      <div className="rule-head">
        <strong>{humanize(o.kind)}</strong>
        <Pill tone={o.label === "research lead" ? "info" : "warn"}>{o.label}</Pill>
        {!o.current ? <Pill tone="idle">no longer meets rule</Pill> : null}
        <span className="muted">{chainLabel(o.chain)} · {o.subject_kind}</span>
        <Mono title={o.subject}>{shortId(o.subject)}</Mono>
      </div>
      <p>
        {o.features.recent_participants} distinct participants in the recent 7 days versus{" "}
        {o.features.prior_participants} in the 7 days before ({o.features.recent_events} events).
      </p>
      <details>
        <summary>Score {o.score} and how it is computed</summary>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Feature</th><th className="num">Value</th><th className="num">Weight</th><th className="num">Contribution</th></tr></thead>
            <tbody>
              {Object.keys(o.contributions).map((k) => (
                <tr key={k}>
                  <td>{humanize(k)}</td>
                  <td className="num">{o.feature_scores[k]}</td>
                  <td className="num">{o.weights[k]}</td>
                  <td className="num">{o.contributions[k]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <dl className="kv">
          <dt>Observation period</dt><dd>{fmtTime(o.observation_period.prior_start)} to {fmtTime(o.observation_period.anchor)}</dd>
          <dt>Rule</dt><dd className="mono">{String(o.rule.id)}@{String(o.rule.version)}</dd>
          <dt>Coverage</dt><dd>{o.coverage.ingested_wallets} ingested wallets · {o.coverage.events_considered} events</dd>
          <dt>Evidence</dt><dd>{o.evidence.total} transactions · <VerificationLine v={o.verification} /></dd>
          <dt>Detected</dt><dd>first {fmtTime(o.first_detected_at)} · last {fmtTime(o.last_detected_at)}</dd>
        </dl>
        <ul>{o.uncertainty.map((u) => <li key={u}>{u}</li>)}</ul>
      </details>
      <p className="note">{o.disclaimer}</p>
    </li>
  );
}

export default function Opportunities() {
  const leads = useApi(() => api.opportunities());
  const [busy, setBusy] = useState<string | null>(null);
  const [last, setLast] = useState<OpportunityRefresh | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const can = useCan("POST", "/intelligence/opportunities/refresh/x");
  const refresh = (chain: string) => {
    setBusy(chain);
    setError(null);
    api.refreshOpportunities(chain).then(
      (r) => { setLast(r); setBusy(null); leads.reload(); },
      (e: unknown) => { setError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Refresh failed.")); setBusy(null); },
    );
  };
  return (
    <>
      <PageHeader
        title="Opportunities"
        lede="Research leads from a reproducible rule over BIP's stored data. Never investment advice, never a buy or sell signal."
        actions={
          <>
            {["ethereum", "solana"].map((c) => (
              <button key={c} type="button" className="btn" disabled={busy !== null || !can.allowed} title={can.reason} onClick={() => refresh(c)}>
                {busy === c ? "Evaluating…" : `Evaluate ${chainLabel(c)}`}
              </button>
            ))}
            <RoleHint can={can} />
          </>
        }
      />
      {error ? <ErrorState error={error} /> : null}
      {last ? (
        <Panel title={`Last evaluation · ${chainLabel(last.chain)}`}>
          <p>
            <Pill tone={last.status === "evaluated" ? "ok" : "warn"}>{humanize(last.status)}</Pill>{" "}
            {last.candidates.length} candidate(s). {last.reason ?? ""}
          </p>
        </Panel>
      ) : null}
      <Panel title="Research leads">
        {leads.status === "loading" ? <Loading /> : null}
        {leads.status === "error" ? <ErrorState error={leads.error} onRetry={leads.reload} /> : null}
        {leads.status === "ready" ? (
          <>
            <DataOriginBanner origin={leads.data.data_origin} />
            {leads.data.items.length === 0 ? (
              <EmptyState title="No research leads">
                A lead needs at least 3 ingested wallets on a chain and a subject whose distinct
                participants at least doubled week over week (minimum 3 participants, 10 events).
                With insufficient data BIP returns no candidate rather than a guess.
              </EmptyState>
            ) : (
              <ul className="rule-list">{leads.data.items.map((o) => <Lead key={o.candidate_key} o={o} />)}</ul>
            )}
          </>
        ) : null}
      </Panel>
    </>
  );
}
