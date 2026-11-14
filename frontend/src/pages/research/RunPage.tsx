import { RoleHint } from "../../auth/RoleHint";
import { useCan } from "../../auth/useAuth";
import { useEffect, useId, useState } from "react";
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
  const uid = useId();
  const [caseId, setCaseId] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const can = useCan("POST", "/research/runs/x/cancel");
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
          <button type="button" className="btn btn-danger" disabled={!can.allowed} title={can.reason} onClick={() => { api.researchCancel(run.run_id).catch(fail); }}>
            Cancel
          </button>
        ) : null}
        <button type="button" className="btn" disabled={busy || !done || !can.allowed} title={can.reason} onClick={() => {
          setBusy(true);
          api.researchRerun(run.run_id).then((n) => navigate(`/research/runs/${encodeURIComponent(n.run_id)}`), fail);
        }}>
          Rerun
        </button>
        <div className="field">
          <label htmlFor={`${uid}-case`}>Case</label>
          <select id={`${uid}-case`} value={caseId} onChange={(e) => setCaseId(e.target.value)} disabled={cases.status !== "ready"}>
            <option value="">Choose a case</option>
            {cases.status === "ready" ? cases.data.map((c) => <option key={c.id} value={c.id}>{c.title}</option>) : null}
          </select>
        </div>
        <button type="button" className="btn" disabled={busy || !caseId || !can.allowed} title={can.reason} onClick={addToCase}>
          Add to case
        </button>
        <RoleHint can={can} />
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

/** Reports before 1.1.0 did not record feature/taxonomy/model-dataset provenance at all. */
export function isLegacyReport(version: string | undefined): boolean {
  if (!version) return true;
  const [maj = 0, min = 0] = version.split(".").map((x) => Number(x));
  return maj < 1 || (maj === 1 && min < 1);
}

function Rec({ label, value, missing }: { label: string; value: string | null | undefined; missing: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd className={value ? undefined : "muted"}>{value ?? missing}</dd>
    </>
  );
}

export function ProvenancePanel({ run, report }: { run: ResearchRun; report: ResearchReport["report"] }) {
  const c = report.classification;
  const model = c.model;
  const legacy = isLegacyReport(report.report_version);
  // a null field means "not recorded" only in old reports; in current ones it is genuinely absent
  const missing = legacy ? NOT_RECORDED : "Not available";
  const ran = Boolean(c.classification_id);
  return (
    <section className="panel" aria-label="Provenance">
      <div className="panel-head"><h2>Provenance</h2></div>
      <dl className="kv">
        <Rec missing={missing} label="Run id" value={run.run_id} />
        <Rec missing={missing} label="Started" value={fmtTime(run.created_at)} />
        <Rec missing="Not finished" label="Finished" value={run.finished_at ? fmtTime(run.finished_at) : null} />
        <Rec missing={missing} label="Data origin" value={humanize(run.data_origin)} />
        <Rec missing={NOT_RECORDED} label="Report version" value={report.report_version} />
        <Rec missing={missing} label="Budget used" value={run.usage ? `${run.usage.tool_calls} of ${run.budget.max_tool_calls ?? "?"} tool calls, ${run.usage.model_calls} of ${run.budget.max_model_calls ?? "?"} model calls` : null} />
        {!legacy && !ran ? (
          <>
            <dt>Classification</dt>
            <dd>Classification did not run in this research run</dd>
          </>
        ) : (
          <>
            <Rec missing={missing} label="Classification" value={c.classification_id} />
            <Rec missing={missing} label="Rules" value={c.rules ? `${c.rules.name} ${c.rules.version}` : null} />
            <dt>Model</dt>
            <dd>
              {model
                ? `${model.name} ${model.version} (${humanize(model.status)}, ${model.data_kind} data)`
                : ran
                  ? "No model prediction used (rules only)"
                  : NOT_RECORDED}
            </dd>
            <Rec missing={missing} label="Model training dataset" value={model ? (model.dataset_version ? shortId(model.dataset_version) : null) : "—"} />
            <Rec missing={missing} label="Feature version" value={c.feature_version} />
            <Rec missing={missing} label="Feature digest" value={c.feature_digest ? shortId(c.feature_digest) : null} />
            <Rec missing={missing} label="Taxonomy version" value={c.taxonomy_version} />
            <Rec missing={missing} label="Features as of" value={c.as_of ? fmtTime(c.as_of) : null} />
          </>
        )}
        <dt>AI provider</dt>
        <dd>{report.llm && report.llm.provider ? `${report.llm.provider} ${report.llm.model ?? ""}` : report.summary_source === "deterministic" ? "None: deterministic summary" : missing}</dd>
        <dt>Evidence references</dt>
        <dd>{report.evidence_references.length}</dd>
      </dl>
    </section>
  );
}
