import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { Anomaly, IntelProfile, VerifyResult } from "../api/types";
import { DataOriginBanner, ScopeNote, VerificationLine, VerificationPill } from "../components/intel";
import { EmptyState, ErrorState, Loading, Mono, Panel, Pill, type Tone } from "../components/ui";
import { fmtInt, fmtTime, humanize, shortId } from "../lib/format";
import { useApi } from "../lib/useApi";

const SEV: Record<string, Tone> = { low: "info", medium: "warn", high: "bad" };
const STATUS_LABEL: Record<string, string> = {
  anomaly: "anomaly",
  no_anomaly: "no anomaly",
  insufficient_data: "not enough data",
  could_not_run: "could not run",
  detected: "observed",
  not_detected: "not observed",
  not_enough_data: "not enough data",
};

function Status({ s }: { s: string }) {
  const tone: Tone = s === "anomaly" || s === "detected" ? "warn" : s === "could_not_run" ? "bad" : "idle";
  return <Pill tone={tone}>{STATUS_LABEL[s] ?? s}</Pill>;
}

function AnalysisPanel({ chain, address, profile, onDone }: {
  chain: string; address: string; profile: IntelProfile; onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const run = profile.latest_analysis;
  const analyze = () => {
    setBusy(true);
    setError(null);
    api.analyze(chain, address).then(
      (r) => {
        setNote(r.reused ? "Inputs unchanged: the previous analysis was reused." : `Analysis run ${r.run_id} completed.`);
        setBusy(false);
        onDone();
      },
      (e: unknown) => {
        setError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Analysis failed."));
        setBusy(false);
      },
    );
  };
  return (
    <Panel
      title="Analysis"
      aside={<button type="button" className="btn btn-primary" onClick={analyze} disabled={busy}>{busy ? "Analysing…" : "Run analysis"}</button>}
    >
      <ScopeNote scope={profile.scope} />
      {run ? (
        <dl className="kv">
          <dt>Latest run</dt>
          <dd>#{run.run_id} · {run.status} · {fmtTime(run.finished_at)}</dd>
          <dt>Analyzer</dt>
          <dd className="mono">{run.analyzer_version} · input {run.input_hash.slice(0, 12)}…</dd>
        </dl>
      ) : (
        <p className="note">Not analysed yet. Analysis reads stored observations only; it makes no network requests.</p>
      )}
      {note ? <p className="note" role="status">{note}</p> : null}
      {error ? <ErrorState error={error} /> : null}
    </Panel>
  );
}

function Metrics({ profile }: { profile: IntelProfile }) {
  return (
    <Panel title="Observed in retrieved dataset">
      <ul className="metric-list">
        {profile.metrics.map((m) => (
          <li key={m.id}>
            <span className="num metric-value">{fmtInt(m.value)}</span> <span>{m.label.replace(/^\d+ /, "")}</span>
          </li>
        ))}
      </ul>
      {profile.volumes.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Asset</th><th>Standard</th><th className="num">In</th><th className="num">Out</th><th className="num">Net</th><th>Transfers in / out</th></tr>
            </thead>
            <tbody>
              {profile.volumes.map((v) => (
                <tr key={v.asset}>
                  <td><Mono title={v.asset}>{v.asset === "native" ? (profile.chain === "solana" ? "SOL" : "ETH") : shortId(v.asset)}</Mono></td>
                  <td>{v.standard}</td>
                  <td className="num mono">{v.in ?? `${v.in_raw} raw`}</td>
                  <td className="num mono">{v.out ?? `${v.out_raw} raw`}</td>
                  <td className="num mono">{v.net ?? `${v.net_raw} raw`}</td>
                  <td>{v.in_count} / {v.out_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <p className="note">No USD values: BIP has no verified price source. Amounts without known decimals stay in raw units.</p>
    </Panel>
  );
}

function VerificationPanel({ chain, address, profile, onDone }: {
  chain: string; address: string; profile: IntelProfile; onDone: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, VerifyResult>>({});
  const [error, setError] = useState<ApiError | null>(null);
  const [summary, setSummary] = useState<string | null>(null);
  const fail = (e: unknown) => {
    setError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Verification failed."));
    setBusy(null);
  };
  const verifyOne = (tx: string) => {
    setBusy(tx);
    setError(null);
    api.verifyTx(chain, tx).then((r) => {
      setResults((m) => ({ ...m, [tx]: r }));
      setBusy(null);
      onDone();
    }, fail);
  };
  const verifyRecent = () => {
    setBusy("wallet");
    setError(null);
    api.verifyWallet(chain, address).then((r) => {
      const parts = Object.entries(r.summary).map(([k, n]) => `${n} ${humanize(k)}`);
      setSummary(r.requested === 0 ? "Nothing left to corroborate." : `Checked ${r.checked} of ${r.requested}: ${parts.join(", ")}.`);
      setBusy(null);
      onDone();
    }, fail);
  };
  return (
    <Panel
      title="Evidence verification"
      aside={<button type="button" className="btn" onClick={verifyRecent} disabled={busy !== null}>{busy === "wallet" ? "Checking…" : "Corroborate recent"}</button>}
    >
      <p>
        <VerificationLine v={profile.verification} />
      </p>
      <p className="note">
        Corroborated means providers on at least two independent infrastructures returned identical
        normalized fields. Each check asks other configured providers (network requests through the
        router). With only one provider configured the check cannot run, and nothing is recorded.
      </p>
      {summary ? <p className="note" role="status">{summary}</p> : null}
      {error ? <ErrorState error={error} /> : null}
      {profile.evidence_sample.length ? (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Tx</th><th>Time</th><th>Source</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {profile.evidence_sample.map((e) => {
                const r = results[e.tx_id];
                return (
                  <tr key={e.tx_id}>
                    <td><Mono title={e.tx_id}>{shortId(e.tx_id)}</Mono></td>
                    <td className="mono">{fmtTime(e.block_time)}</td>
                    <td className="mono">{e.provider}</td>
                    <td>
                      <VerificationPill label={r?.label ?? e.verification} />
                      {r?.status === "could_not_run" ? <div className="muted">{r.reason}</div> : null}
                      {r?.note ? <div className="muted">{r.note}</div> : null}
                    </td>
                    <td>
                      <button type="button" className="btn btn-small" onClick={() => verifyOne(e.tx_id)} disabled={busy !== null}>
                        {busy === e.tx_id ? "…" : "Verify"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </Panel>
  );
}

function AttachFinding({ findingId }: { findingId: string }) {
  const uid = useId();
  const cases = useApi(() => api.investigations(), []);
  const [caseId, setCaseId] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const attach = () => {
    const id = Number(caseId);
    if (!id) return;
    setError(null);
    api.attachFinding(findingId, id).then(() => {
      const title = cases.status === "ready" ? cases.data.find((c) => c.id === id)?.title : undefined;
      setMsg(`Attached to ${title ?? `case ${id}`}.`);
    }, (e: unknown) => setError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Could not attach the finding.")));
  };
  return (
    <div className="row">
      <div className="field">
        <label htmlFor={`${uid}-case`}>Case for finding {findingId}</label>
        <select id={`${uid}-case`} value={caseId} onChange={(e) => setCaseId(e.target.value)} disabled={cases.status !== "ready"}>
          <option value="">Choose a case</option>
          {cases.status === "ready" ? cases.data.map((c) => <option key={c.id} value={c.id}>{c.title}</option>) : null}
        </select>
      </div>
      <button type="button" className="btn" disabled={!caseId} onClick={attach}>Attach to case</button>
      {msg ? <span role="status">{msg}</span> : null}
      {error ? <ErrorState error={error} /> : null}
    </div>
  );
}

function AnomalyList({ items }: { items: Anomaly[] }) {
  if (!items.length) return <EmptyState title="No detector results yet">Run analysis to evaluate the detectors.</EmptyState>;
  return (
    <ul className="rule-list">
      {items.map((a) => (
        <li key={a.observation_id}>
          <div className="rule-head">
            <strong>{a.title}</strong> <Status s={a.status} />
            {a.severity ? <Pill tone={SEV[a.severity] ?? "idle"}>severity {a.severity}</Pill> : null}
            {a.confidence ? <span className="muted">confidence {a.confidence.value} ({a.confidence.label})</span> : null}
            <span className="muted mono">{a.detector_id}@{a.version}</span>
          </div>
          <div>{a.explanation}</div>
          {a.status === "anomaly" ? (
            <details>
              <summary>Method, evidence and limitations</summary>
              <dl className="kv">
                <dt>Baseline</dt><dd>{a.baseline}</dd>
                <dt>Threshold</dt><dd>{a.thresholds}</dd>
                <dt>Window</dt><dd>{fmtTime(a.window_start)} to {fmtTime(a.window_end)}</dd>
                <dt>Evidence</dt><dd>{a.evidence.total} transactions · <VerificationLine v={a.verification} /></dd>
                <dt>Limitations</dt><dd>{a.limitations.join(" ")}</dd>
                {a.finding_id ? (<><dt>Finding</dt><dd className="mono">{a.finding_id}</dd></>) : null}
              </dl>
              {a.finding_id ? <AttachFinding findingId={a.finding_id} /> : null}
            </details>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export default function WalletIntel({ chain, address, refreshKey, showOrigin = true }: {
  chain: string;
  address: string;
  refreshKey: number;
  /** false where the page already shows the data-origin banner (wallet workspace) */
  showOrigin?: boolean;
}) {
  const profile = useApi(() => api.intelProfile(chain, address), [chain, address, refreshKey]);
  const behaviour = useApi(() => api.intelBehaviour(chain, address), [chain, address, refreshKey]);
  const anomalies = useApi(() => api.anomalies(chain, address), [chain, address, refreshKey]);
  // reload() keeps the current data visible while refreshing, so panel state survives
  const reload = () => {
    profile.reload();
    behaviour.reload();
    anomalies.reload();
  };

  if (profile.status === "loading") return <Loading label="Loading wallet intelligence…" />;
  if (profile.status === "error") return <ErrorState error={profile.error} onRetry={profile.reload} />;
  const p = profile.data;
  const q = `?chain=${encodeURIComponent(chain)}&address=${encodeURIComponent(address)}`;
  return (
    <section aria-label="Wallet intelligence" className="intel">
      {showOrigin ? <DataOriginBanner origin={p.data_origin} /> : null}
      {p.scope.transactions === 0 && p.scope.transfers === 0 ? (
        <EmptyState title="Nothing to analyse yet">
          No observations are stored for this wallet. Retrieve recent activity first; analysis
          never invents data.
        </EmptyState>
      ) : (
        <>
          <div className="grid grid-2">
            <AnalysisPanel chain={chain} address={address} profile={p} onDone={reload} />
            <VerificationPanel chain={chain} address={address} profile={p} onDone={reload} />
          </div>
          <Metrics profile={p} />
          <div className="page-actions intel-links">
            <Link className="btn" to={`/flows${q}`}>Capital flows</Link>
            <Link className="btn" to={`/graph${q}`}>Relationship graph</Link>
          </div>
          <Panel title="Behaviour (descriptive rules)">
            {behaviour.status === "loading" ? <Loading /> : null}
            {behaviour.status === "error" ? <ErrorState error={behaviour.error} onRetry={behaviour.reload} /> : null}
            {behaviour.status === "ready" ? (
              <>
                <p className="note">{behaviour.data.note}</p>
                <ul className="rule-list">
                  {behaviour.data.items.map((r) => (
                    <li key={r.rule_id}>
                      <div className="rule-head">
                        <strong>{r.name}</strong> <Status s={r.status} />
                        {r.status === "detected" && r.claim_type === "hypothesis" ? <Pill tone="info">hypothesis</Pill> : null}
                        {r.confidence ? <span className="muted">confidence {r.confidence.value} ({r.confidence.label})</span> : null}
                        {r.supporting_count ? <span className="muted">{r.supporting_count} supporting tx</span> : null}
                      </div>
                      <div>{r.explanation}</div>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </Panel>
          <Panel title="Anomaly detectors">
            {anomalies.status === "loading" ? <Loading /> : null}
            {anomalies.status === "error" ? <ErrorState error={anomalies.error} onRetry={anomalies.reload} /> : null}
            {anomalies.status === "ready" ? <AnomalyList items={anomalies.data.items} /> : null}
          </Panel>
          <Panel title={`Findings (${p.findings.length})`}>
            {p.findings.length === 0 ? (
              <EmptyState title="No findings">Findings are recorded when a detector reports an anomaly or a pattern hypothesis.</EmptyState>
            ) : (
              <ul className="rule-list">
                {p.findings.map((f) => (
                  <li key={f.finding_id}>
                    <div className="rule-head">
                      <strong>{humanize(f.finding_type)}</strong> <Pill tone={f.status === "partially_verified" ? "info" : "warn"}>{humanize(f.status)}</Pill>
                      {f.claims.map((c, i) => <Pill key={i} tone="idle">{humanize(c.claim_type)}</Pill>)}
                      {f.investigation_id ? <Link to={`/investigations/${f.investigation_id}`}>investigation #{f.investigation_id}</Link> : null}
                    </div>
                    <div>{f.explanation}</div>
                    <div className="muted mono">{f.finding_id} · {f.produced_by}</div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title={`Counterparties (${p.counterparties.total})`}>
            <p className="note">Observed transfer parties only. No ownership, identity or intent is implied.</p>
            {p.counterparties.items.length === 0 ? <EmptyState title="No counterparties observed" /> : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Address</th><th className="num">Transfers</th><th>In / out</th><th>Assets</th><th>Last seen</th><th>Verification</th></tr></thead>
                  <tbody>
                    {p.counterparties.items.slice(0, 25).map((c) => (
                      <tr key={c.address}>
                        <td><Mono title={c.address}>{shortId(c.address)}</Mono>{c.resolved_from_token_account ? <div className="muted">via token account</div> : null}</td>
                        <td className="num">{c.transfers}</td>
                        <td>{c.in} / {c.out}</td>
                        <td>{c.assets.length}</td>
                        <td className="mono">{fmtTime(c.last_seen)}</td>
                        <td><VerificationPill label={c.verification.overall} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
          <Panel title={chain === "solana" ? "Program interactions" : "Contract interactions"}>
            <p className="note">{p.interactions.note}</p>
            {p.interactions.items.length === 0 ? <EmptyState title="No interactions observed" /> : (
              <ul className="metric-list">
                {p.interactions.items.slice(0, 15).map((i) => (
                  <li key={i.target}>
                    <span className="num metric-value">{i.count}</span> <Mono title={i.target}>{shortId(i.target)}</Mono>{" "}
                    <span className="muted">{i.name ?? i.kind}</span>
                  </li>
                ))}
              </ul>
            )}
            {p.deployments.length ? <p>{p.deployments.length} contract deployment(s) observed.</p> : null}
          </Panel>
          <Panel title="Limitations">
            <ul>{p.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
          </Panel>
        </>
      )}
    </section>
  );
}
