import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, api } from "../../api/client";
import type { ResearchReport, ResearchRun } from "../../api/types";
import { Address, shortId } from "../../components/Address";
import { DataOriginBanner } from "../../components/intel";
import { EmptyState, ErrorState, Loading, PageHeader, Panel, Pill } from "../../components/ui";
import { chainLabel } from "../../lib/address";
import { fmtTime, humanize } from "../../lib/format";
import { useApi } from "../../lib/useApi";
import { ReportView } from "../Research";

const TERMINAL = new Set(["completed", "completed_with_limitations", "failed", "cancelled"]);
const NOT_RECORDED = "Not recorded in this report version";

function tone(state: string) {
  if (state.startsWith("completed")) return "ok" as const;
  if (state === "failed") return "bad" as const;
  if (state === "cancelled") return "idle" as const;
  return "info" as const;
}

/** A persisted research run, deep-linkable: survives reloads and restarts (state lives in the DB). */
export default function RunPage() {
  const { runId = "" } = useParams();
  const run = useApi(() => api.researchRun(runId), [runId]);
  const { reload } = run;
  const active = run.status === "ready" && !TERMINAL.has(run.data.state);

  useEffect(() => {
    if (!active) return;
    const t = window.setTimeout(reload, 1000);
    return () => window.clearTimeout(t);
  }, [active, run.data, reload]);

  if (run.status === "loading") return <Loading label="Loading research run…" />;
  if (run.status === "error" && run.error.code === "not_found")
    return (
      <>
        <PageHeader title="Research run" />
        <EmptyState title="Research run not found">
          No research run with this id exists in this BIP database. <Link to="/research">Research history</Link>
        </EmptyState>
      </>
    );
  if (run.status === "error") return <ErrorState error={run.error} onRetry={run.reload} />;
  const r = run.data;
  const done = TERMINAL.has(r.state);
  return (
    <>
      <PageHeader title="Research run" docTitle={`Research ${shortId(r.run_id)}`} lede={r.question} />
      <div className="wallet-id">
        <Pill tone="info">{chainLabel(r.chain)}</Pill>
        <Address value={r.address} chain={r.chain} link />
        <Pill tone={tone(r.state)}>{humanize(r.state)}</Pill>
      </div>
      <DataOriginBanner origin={r.data_origin === "offline_fixture" ? "offline_fixture" : "stored_observations"} />
      {r.parent_run_id ? (
        <p className="note">
          <Link to={`/research/runs/${encodeURIComponent(r.parent_run_id)}`}>Rerun of {shortId(r.parent_run_id)}</Link>. The original
          run and report are unchanged.
        </p>
      ) : null}
      <RunActions run={r} />
      <section role="status" aria-label="Run progress" className="muted">
        {r.progress && !done ? `Step ${r.progress.step} of ${r.progress.of}: ${r.progress.tool}` : null}
        {r.usage ? `${r.usage.tool_calls} tool calls · ${r.usage.model_calls} model calls` : null}
        {r.error ? ` · ${r.error}` : null}
      </section>
      {done && r.state !== "failed" && r.state !== "cancelled" ? <ReportWithProvenance run={r} /> : null}
      {done && (r.state === "failed" || r.state === "cancelled") ? (
        <Panel title="No report">
          <p>This run {r.state === "failed" ? "failed" : "was cancelled"} before a report was written. Nothing was inferred in its place.</p>
          {r.limitations.length ? <ul className="list">{r.limitations.map((l) => <li key={l}>{l}</li>)}</ul> : null}
        </Panel>
      ) : null}
      {!done ? <Loading label={`${humanize(r.state)}…`} /> : null}
    </>
  );
}

function RunActions({ run }: { run: ResearchRun }) {
  const navigate = useNavigate();
  const cases = useApi(() => api.investigations(), []);
  const [caseId, setCaseId] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const done = TERMINAL.has(run.state);
  const fail = (e: unknown) => {
    setError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Request failed."));
    setBusy(false);
  };
  const addToCase = () => {
    const id = Number(caseId);
    if (!id) return;
    setBusy(true);
    setError(null);
    api.linkResearchRun(id, run.run_id, null).then(() => {
      const title = cases.status === "ready" ? cases.data.find((c) => c.id === id)?.title : undefined;
      setMsg(`Added to ${title ?? `case ${id}`}.`);
      setBusy(false);
    }, fail);
  };
  return (
    <Panel title="Actions">
      <div className="row">
        {!done ? (
          <button type="button" className="btn btn-danger" onClick={() => { api.researchCancel(run.run_id).catch(fail); }}>
            Cancel
          </button>
        ) : null}
        <button type="button" className="btn" disabled={busy || !done} onClick={() => {
          setBusy(true);
          api.researchRerun(run.run_id).then((n) => navigate(`/research/runs/${encodeURIComponent(n.run_id)}`), fail);
        }}>
          Rerun
        </button>
        <label className="field">
          Case
          <select value={caseId} onChange={(e) => setCaseId(e.target.value)} disabled={cases.status !== "ready"}>
            <option value="">Choose a case</option>
            {cases.status === "ready" ? cases.data.map((c) => <option key={c.id} value={c.id}>{c.title}</option>) : null}
          </select>
        </label>
        <button type="button" className="btn" disabled={busy || !caseId} onClick={addToCase}>
          Add to case
        </button>
      </div>
      {msg ? <p role="status">{msg}</p> : null}
      {error ? <ErrorState error={error} /> : null}
    </Panel>
  );
}

function ReportWithProvenance({ run }: { run: ResearchRun }) {
  const rep = useApi(() => api.researchReport(run.run_id), [run.run_id]);
  return (
    <>
      {rep.status === "ready" ? <ProvenancePanel run={run} report={rep.data.report} /> : null}
      <ReportView runId={run.run_id} />
    </>
  );
}

function Rec({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <>
      <dt>{label}</dt>
      <dd className={value ? undefined : "muted"}>{value ?? NOT_RECORDED}</dd>
    </>
  );
}

export function ProvenancePanel({ run, report }: { run: ResearchRun; report: ResearchReport["report"] }) {
  const c = report.classification;
  const model = c.model;
  return (
    <section className="panel" aria-label="Provenance">
      <div className="panel-head"><h2>Provenance</h2></div>
      <dl className="kv">
        <Rec label="Run id" value={run.run_id} />
        <Rec label="Started" value={fmtTime(run.created_at)} />
        <Rec label="Finished" value={run.finished_at ? fmtTime(run.finished_at) : null} />
        <Rec label="Data origin" value={humanize(run.data_origin)} />
        <Rec label="Report version" value={report.report_version} />
        <Rec label="Budget used" value={run.usage ? `${run.usage.tool_calls} of ${run.budget.max_tool_calls ?? "?"} tool calls, ${run.usage.model_calls} of ${run.budget.max_model_calls ?? "?"} model calls` : null} />
        <Rec label="Classification" value={c.classification_id} />
        <Rec label="Rules" value={c.rules ? `${c.rules.name} ${c.rules.version}` : null} />
        <dt>Model</dt>
        <dd>{model ? `${model.name} ${model.version} (${humanize(model.status)}, ${model.data_kind} data)` : "No model prediction used (rules only)"}</dd>
        <Rec label="Model training dataset" value={model ? (model.dataset_version ? shortId(model.dataset_version) : null) : "—"} />
        <Rec label="Feature version" value={c.feature_version} />
        <Rec label="Feature digest" value={c.feature_digest ? shortId(c.feature_digest) : null} />
        <Rec label="Taxonomy version" value={c.taxonomy_version} />
        <Rec label="Features as of" value={c.as_of ? fmtTime(c.as_of) : null} />
        <dt>AI provider</dt>
        <dd>{report.llm && report.llm.provider ? `${report.llm.provider} ${report.llm.model ?? ""}` : report.summary_source === "deterministic" ? "None: deterministic summary" : NOT_RECORDED}</dd>
        <dt>Evidence references</dt>
        <dd>{report.evidence_references.length}</dd>
      </dl>
    </section>
  );
}
