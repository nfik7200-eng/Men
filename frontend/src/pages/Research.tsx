import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { Classification, LabelResult, MLModel, ResearchClaim, ResearchPreview, ResearchRun } from "../api/types";
import { DataOriginBanner, WalletPicker } from "../components/intel";
import { EmptyState, ErrorState, Loading, PageHeader, Panel, Pill, type Tone } from "../components/ui";
import { chainLabel } from "../lib/address";
import { fmtTime, humanize, shortId } from "../lib/format";
import { useApi } from "../lib/useApi";

const STATUS_TONE: Record<string, Tone> = {
  high_confidence: "ok", moderate_confidence: "info", low_confidence: "warn", rule_based: "idle",
  insufficient_evidence: "idle",
};
const CLAIM_TONE: Record<string, Tone> = {
  verified: "ok", corroborated: "ok", unverified: "warn", inferred: "info", conflicted: "bad",
  invalidated: "bad", insufficient_evidence: "idle",
};
const KIND_LABEL: Record<string, string> = {
  blockchain_observation: "Observed blockchain data",
  deterministic_rule: "Deterministic rule",
  model_prediction: "ML prediction",
  statistical_signal: "Statistical signal",
  ai_interpretation: "AI interpretation",
  hypothesis: "Hypothesis",
  missing_information: "Missing information",
};
const TERMINAL = new Set(["completed", "completed_with_limitations", "failed", "cancelled"]);
const FOCUS = ["classification", "behaviour", "flows", "contracts", "graph", "anomalies", "opportunities", "evidence"];

function origin(o: string) {
  return o === "offline_fixture" ? "offline_fixture" : "stored_observations";
}

function LabelRow({ l }: { l: LabelResult }) {
  return (
    <li>
      <div className="rule-head">
        <strong>{humanize(l.label)}</strong>
        <Pill tone={STATUS_TONE[l.status] ?? "idle"}>{humanize(l.status)}</Pill>
        {l.confidence !== null ? <span className="muted">model score {l.confidence.toFixed(2)} (not certainty)</span> : <span className="muted">no probability</span>}
        {l.model_status === "experimental_synthetic" ? <Pill tone="warn">synthetic-trained model</Pill> : null}
        {l.calibrated === false ? <Pill tone="idle">uncalibrated</Pill> : null}
        <span className="muted mono">{l.source}</span>
      </div>
      <div>{l.explanation}</div>
      {l.caveat ? <div className="muted">{l.caveat}</div> : null}
      {l.evidence.length ? (
        <div className="muted">Evidence: {l.evidence.slice(0, 4).map((e) => `${e.type} ${shortId(e.ref, 5)}`).join(" · ")}</div>
      ) : null}
      {l.supporting_features.length ? (
        <div className="muted">Top model features: {l.supporting_features.map((f) => `${f.feature}=${f.value ?? "unavailable"}`).join(", ")}</div>
      ) : null}
    </li>
  );
}

function ClassificationView({ c }: { c: Classification }) {
  return (
    <div className="stack" aria-label="Classification result">
      <DataOriginBanner origin={origin(c.data_origin)} />
      <p>
        <Pill tone={c.status === "classified" ? "ok" : c.status === "insufficient_evidence" ? "idle" : "warn"}>{humanize(c.status)}</Pill>{" "}
        <span className="muted">
          {fmtTime(c.classification_timestamp)} · rules {c.rules.name}@{c.rules.version}
          {c.model ? ` · model ${c.model.name}@${c.model.version} (${humanize(c.model.status)})` : " · no trained model"}
          {" "}· features v{c.feature_version}
        </span>
      </p>
      <h3>Account type</h3>
      <ul className="rule-list"><LabelRow l={c.account_type} /></ul>
      <h3>Behavioural roles</h3>
      {c.behavioural_roles.length ? <ul className="rule-list">{c.behavioural_roles.map((r) => <LabelRow key={`${r.source}:${r.label}`} l={r} />)}</ul>
        : <EmptyState title="No behavioural role assigned">The available evidence does not support any role.</EmptyState>}
      {c.contradictions.length ? (
        <div className="notice" role="note">
          <strong>Conflicting classifications</strong>
          <ul>{c.contradictions.map((x, i) => <li key={i}>{x.between.join(" vs ")}: {x.note}</li>)}</ul>
        </div>
      ) : null}
      <dl className="kv">
        <dt>Coverage</dt>
        <dd>{String(c.coverage.transactions_observed ?? "—")} transactions · {String(c.coverage.counterparties_observed ?? "—")} counterparties · {String(c.coverage.observation_period_days ?? "—")} days · {String(c.coverage.data_completeness)}</dd>
        <dt>Anomaly model</dt>
        <dd>{c.anomaly.status === "scored" ? `score ${c.anomaly.score} · percentile ${c.anomaly.training_percentile} · ${c.anomaly.outlier ? "outlier" : "not an outlier"} (${c.anomaly.note})` : c.anomaly.reason ?? c.anomaly.status}</dd>
      </dl>
      <ul className="list">{c.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
    </div>
  );
}

function ClaimList({ claims }: { claims: ResearchClaim[] }) {
  return (
    <ul className="rule-list">
      {claims.map((c) => (
        <li key={c.claim_key}>
          <div className="rule-head">
            <Pill tone={CLAIM_TONE[c.status] ?? "idle"}>{humanize(c.status)}</Pill>
            <span className="muted">{KIND_LABEL[c.kind] ?? c.kind} · {humanize(c.specialist)}</span>
            {c.confidence ? <span className="muted">confidence {c.confidence}</span> : null}
          </div>
          <div>{c.statement}</div>
          <div className="muted mono">{c.evidence.slice(0, 4).map((e) => `${e.type}:${shortId(e.ref, 5)}`).join(" · ")}</div>
        </li>
      ))}
    </ul>
  );
}

export function ReportView({ runId }: { runId: string }) {
  const rep = useApi(() => api.researchReport(runId), [runId]);
  if (rep.status === "loading") return <Loading label="Loading report…" />;
  if (rep.status === "error") return <ErrorState error={rep.error} onRetry={rep.reload} />;
  const r = rep.data.report;
  const titles: [string, string][] = [["observations", "Observed blockchain data"], ["classification", "Classification (rules and ML predictions)"],
    ["signals", "Statistical signals"], ["ai_interpretations", "AI interpretations"], ["hypotheses", "Hypotheses"], ["missing_information", "Missing information"]];
  return (
    <div className="stack" aria-label="Research report">
      <Panel title={`Executive summary (${humanize(r.summary_source)})`}>
        <p>{r.executive_summary}</p>
        <p className="muted">{Object.entries(r.claim_status_counts).map(([k, v]) => `${v} ${humanize(k)}`).join(" · ")}</p>
        <p className="note">{r.disclaimer}</p>
      </Panel>
      {titles.map(([key, title]) => (r.sections[key]?.length ? (
        <Panel key={key} title={title}><ClaimList claims={r.sections[key]} /></Panel>
      ) : null))}
      {r.contradictions.length ? <Panel title="Contradictory findings"><ClaimList claims={r.contradictions} /></Panel> : null}
      <div className="grid grid-2">
        <Panel title="Limitations"><ul className="list">{r.limitations.map((l) => <li key={l}>{l}</li>)}</ul></Panel>
        <Panel title="Recommended next investigations"><ul className="list">{r.next_steps.map((l) => <li key={l}>{l}</li>)}</ul></Panel>
      </div>
      <details>
        <summary>{r.evidence_references.length} evidence references</summary>
        <p className="mono muted">{r.evidence_references.join(" ")}</p>
      </details>
    </div>
  );
}

export function Investigation({ chain, address }: { chain: string; address: string }) {
  const [question, setQuestion] = useState("What kind of wallet is this, and what does the evidence support?");
  const [focus, setFocus] = useState<string[]>([]);
  const [preview, setPreview] = useState<ResearchPreview | null>(null);
  const [run, setRun] = useState<ResearchRun | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const body = () => ({ chain, address, question, ...(focus.length ? { focus } : {}) });
  const fail = (e: unknown) => { setError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Request failed.")); setBusy(false); };

  useEffect(() => {
    if (!run || TERMINAL.has(run.state)) return;
    const t = setTimeout(() => { api.researchRun(run.run_id).then(setRun, fail); }, 800);
    return () => clearTimeout(t);
  }, [run]);

  return (
    <Panel title="AI investigation">
      <label className="field">
        <span>Research question</span>
        <textarea value={question} maxLength={1000} rows={2} onChange={(e) => { setQuestion(e.target.value); setPreview(null); }} />
      </label>
      <fieldset className="probe-select">
        <legend>Focus (none = inferred from the question; classification and evidence are always included)</legend>
        {FOCUS.map((f) => (
          <label key={f} className="check">
            <input type="checkbox" checked={focus.includes(f)} onChange={() => { setPreview(null); setFocus((x) => (x.includes(f) ? x.filter((y) => y !== f) : [...x, f])); }} />
            {f}
          </label>
        ))}
      </fieldset>
      <div className="row">
        <button type="button" className="btn" disabled={busy || question.trim().length < 3} onClick={() => { setBusy(true); setError(null); api.researchPreview(body()).then((p) => { setPreview(p); setBusy(false); }, fail); }}>
          Preview research plan
        </button>
        <button type="button" className="btn btn-primary" disabled={busy || !preview || (run !== null && !TERMINAL.has(run.state))}
          onClick={() => { setBusy(true); setError(null); api.researchStart(body()).then((r) => { setRun(r); setBusy(false); }, fail); }}>
          Start investigation
        </button>
        {run && !TERMINAL.has(run.state) ? (
          <button type="button" className="btn btn-danger" onClick={() => { api.researchCancel(run.run_id).then(() => api.researchRun(run.run_id).then(setRun), fail); }}>Cancel</button>
        ) : null}
      </div>
      {error ? <ErrorState error={error} /> : null}
      {preview ? (
        <div className="stack" aria-label="Research plan">
          <p>
            <strong>{preview.plan.steps.length} tool calls</strong> (max {preview.budget.max_tool_calls}) · up to {preview.budget.max_seconds} s · ≤ {preview.budget.max_model_calls} model calls ·{" "}
            AI: {preview.llm.available ? `${preview.llm.provider} ${preview.llm.model ?? ""}` : "not available"}
          </p>
          {!preview.llm.available ? <p className="notice" role="note">{preview.llm.reason}</p> : null}
          <ol>{preview.plan.steps.map((s) => <li key={s.seq}>{humanize(s.specialist)}: <span className="mono">{s.tool}</span></li>)}</ol>
          <p className="note">The plan is fixed before any model call. The AI cannot choose tools, change statuses or add evidence.</p>
        </div>
      ) : null}
      {run ? (
        <div className="stack" role="status" aria-label="Investigation progress">
          <p>
            <Pill tone={run.state === "completed" ? "ok" : run.state === "failed" ? "bad" : run.state === "cancelled" ? "idle" : "info"}>{humanize(run.state)}</Pill>{" "}
            {run.progress && !TERMINAL.has(run.state) ? `step ${run.progress.step} of ${run.progress.of}: ${run.progress.tool}` : null}
            {run.usage ? ` · ${run.usage.tool_calls} tool calls · ${run.usage.model_calls} model calls` : null}
            {run.error ? <span className="muted"> · {run.error}</span> : null}
          </p>
          {TERMINAL.has(run.state) && run.state !== "failed" && run.state !== "cancelled" ? <ReportView runId={run.run_id} /> : null}
        </div>
      ) : null}
    </Panel>
  );
}

export function ClassificationPanel({ chain, address }: { chain: string; address: string }) {
  const [result, setResult] = useState<Classification | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const history = useApi(() => api.classifications(chain, address), [chain, address, result?.classification_id ?? ""]);
  const classify = () => {
    setBusy(true);
    setError(null);
    api.classify(chain, address).then((r) => { setResult(r); setBusy(false); }, (e: unknown) => {
      setError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Classification failed."));
      setBusy(false);
    });
  };
  return (
    <>
      <Panel title={`Wallet classification · ${chainLabel(chain)} ${shortId(address)}`}
        aside={<button type="button" className="btn btn-primary" onClick={classify} disabled={busy}>{busy ? "Classifying…" : "Classify wallet"}</button>}>
        <p className="note">Rules always run on stored evidence. A trained model adds predictions only with validated thresholds; scores are model outputs, not certainty. No LLM is involved.</p>
        {error ? <ErrorState error={error} /> : null}
        {result ? <ClassificationView c={result} /> : <p className="muted">Not classified in this session yet.</p>}
        {history.status === "ready" && history.data.items.length > 1 ? (
          <details>
            <summary>Classification history ({history.data.total})</summary>
            <ul className="list">
              {history.data.items.map((h) => (
                <li key={h.classification_id}>
                  <span className="mono">{fmtTime(h.classification_timestamp)}</span> {h.account_type.label} ·{" "}
                  {h.behavioural_roles.map((r) => `${r.label} (${humanize(r.status)})`).join(", ") || "no roles"} · {h.model ? `${h.model.name}@${h.model.version}` : "rules only"}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </Panel>
    </>
  );
}

function WalletWorkspace({ chain, address }: { chain: string; address: string }) {
  return (
    <>
      <ClassificationPanel chain={chain} address={address} />
      <Investigation chain={chain} address={address} />
    </>
  );
}

function ModelsPanel() {
  const models = useApi(api.mlModels);
  const runs = useApi(api.trainingRuns);
  const config = useApi(api.researchConfig);
  const [selected, setSelected] = useState<MLModel | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const train = (chain: string, source: "stored_labels" | "synthetic") => {
    setMsg(null);
    setError(null);
    api.train(chain, source).then((r) => { setMsg(`${humanize(r.status)}: ${r.message}`); models.reload(); runs.reload(); },
      (e: unknown) => setError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Training failed.")));
  };
  return (
    <div className="stack">
      <Panel title="AI provider">
        {config.status === "loading" ? <Loading /> : null}
        {config.status === "error" ? <ErrorState error={config.error} onRetry={config.reload} /> : null}
        {config.status === "ready" ? (
          <>
            <p><Pill tone={config.data.provider.available ? "ok" : "idle"}>{config.data.provider.available ? "Available" : "Not available"}</Pill> {config.data.provider.name} {config.data.provider.model ?? ""} · <span className="muted">{config.data.provider.reason}</span></p>
            <p className="note">{config.data.how_to_configure}</p>
          </>
        ) : null}
      </Panel>
      <Panel title="Model registry" aside={
        <div className="row">
          <button type="button" className="btn" onClick={() => train("ethereum", "stored_labels")}>Train Ethereum on reviewed labels</button>
          <button type="button" className="btn" onClick={() => train("ethereum", "synthetic")}>Train synthetic demo (Ethereum)</button>
        </div>}>
        {msg ? <p role="status">{msg}</p> : null}
        {error ? <ErrorState error={error} /> : null}
        {models.status === "loading" ? <Loading /> : null}
        {models.status === "error" ? <ErrorState error={models.error} onRetry={models.reload} /> : null}
        {models.status === "ready" && models.data.items.length === 0 ? (
          <EmptyState title="No model trained">Only deterministic rules classify wallets. A real model needs enough reviewed, provenance-backed labels.</EmptyState>
        ) : null}
        {models.status === "ready" && models.data.items.length ? (
          <div className="table-wrap">
            <table aria-label="Model registry">
              <thead><tr><th>Model</th><th>Status</th><th>Data</th><th>Labels</th><th>Test macro F1</th><th>Created</th><th /></tr></thead>
              <tbody>
                {models.data.items.map((m) => (
                  <tr key={`${m.name}@${m.version}`}>
                    <td className="mono">{m.name}@{m.version}</td>
                    <td><Pill tone={m.status === "trained" ? "ok" : "warn"}>{humanize(m.status)}</Pill></td>
                    <td>{m.data_kind}{m.data_kind === "synthetic" ? <div className="muted">not real-world performance</div> : null}</td>
                    <td>{m.labels.modelled.length} modelled · {Object.keys(m.labels.skipped).length} skipped</td>
                    <td>{m.labels.summary?.test?.macro_f1 != null ? m.labels.summary.test.macro_f1.toFixed(3) : "—"}</td>
                    <td className="mono">{fmtTime(m.created_at)}</td>
                    <td><button type="button" className="btn btn-small" onClick={() => api.mlModel(m.name, m.version).then(setSelected, () => undefined)}>Evaluation</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {selected ? (
          <div className="stack">
            <h3>{selected.name}@{selected.version} — test split (out-of-time)</h3>
            {selected.note ? <p className="notice" role="note">{selected.note}</p> : null}
            <div className="table-wrap">
              <table aria-label="Model evaluation">
                <thead><tr><th>Label</th><th className="num">Precision</th><th className="num">Recall</th><th className="num">F1</th><th className="num">Support</th><th className="num">Brier</th><th>High-confidence threshold</th></tr></thead>
                <tbody>
                  {(selected.evaluation ?? []).filter((e) => e.split === "test").map((e) => (
                    <tr key={e.label}>
                      <td>{humanize(e.label)}</td>
                      <td className="num">{e.metrics.precision?.toFixed(3) ?? "—"}</td>
                      <td className="num">{e.metrics.recall?.toFixed(3) ?? "—"}</td>
                      <td className="num">{e.metrics.f1?.toFixed(3) ?? "—"}</td>
                      <td className="num">{e.metrics.support}/{e.metrics.n}</td>
                      <td className="num">{e.metrics.brier?.toFixed(3) ?? "—"}</td>
                      <td>{selected.thresholds[e.label]?.high != null ? `${selected.thresholds[e.label]?.high?.toFixed(2) ?? ""} (test precision ${e.metrics.at_high_threshold.precision?.toFixed(2) ?? "—"})` : "none validated"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
      </Panel>
      <Panel title="Training runs">
        {runs.status === "ready" && runs.data.items.length ? (
          <ul className="list">{runs.data.items.map((r) => <li key={r.training_run_id}><span className="mono">{fmtTime(r.started_at)}</span> · {r.model_name} · <Pill tone={r.status === "succeeded" ? "ok" : "warn"}>{humanize(r.status)}</Pill> · {r.data_kind} · {r.message}</li>)}</ul>
        ) : <p className="muted">No training runs.</p>}
      </Panel>
    </div>
  );
}

export default function Research() {
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState<"wallet" | "models">("wallet");
  const chain = params.get("chain") ?? "";
  const address = params.get("address") ?? "";
  return (
    <>
      <PageHeader title="AI Research" lede="Trained-model wallet classification and bounded, evidence-first investigations. Observed data, ML predictions, corroborated evidence, AI interpretations and hypotheses are always labelled separately." />
      <div className="row tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "wallet"} className={`btn ${tab === "wallet" ? "btn-primary" : ""}`} onClick={() => setTab("wallet")}>Wallet research</button>
        <button type="button" role="tab" aria-selected={tab === "models"} className={`btn ${tab === "models" ? "btn-primary" : ""}`} onClick={() => setTab("models")}>Models and AI provider</button>
      </div>
      {tab === "models" ? <ModelsPanel /> : (
        <>
          <Panel>
            <WalletPicker key={`${chain}:${address}`} initialChain={chain || "ethereum"} initialAddress={address} submitLabel="Open wallet" onPick={(c, a) => setParams({ chain: c, address: a })} />
          </Panel>
          {chain && address ? <WalletWorkspace key={`${chain}:${address}`} chain={chain} address={address} /> : (
            <EmptyState title="Choose a wallet">Enter a wallet whose activity BIP has retrieved.</EmptyState>
          )}
        </>
      )}
    </>
  );
}
