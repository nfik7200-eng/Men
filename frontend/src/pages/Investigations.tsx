import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type {
  Case, CaseDetail, CaseRunResult, CaseStatus, CaseTemplate, CaseType, EvidenceKind,
  HypothesisStatus, InvestigationStatus, LeadStatus,
} from "../api/types";
import { RoleHint } from "../auth/RoleHint";
import { useCan } from "../auth/useAuth";
import { EmptyState, ErrorState, FieldError, Loading, Mono, PageHeader, Panel, Pill } from "../components/ui";
import { CHAINS, chainLabel, validateAddress } from "../lib/address";
import { fmtTime, humanize } from "../lib/format";
import { useApi } from "../lib/useApi";

const STATUSES: InvestigationStatus[] = ["open", "in_progress", "closed"];
const statusTone = (s: InvestigationStatus) => (s === "open" ? "info" : s === "in_progress" ? "warn" : "idle");

function CreateInvestigation({ onCreated }: { onCreated: () => void }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [chain, setChain] = useState("");
  const [address, setAddress] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const can = useCan("POST", "/investigations");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!can.allowed) return;
    if (!title.trim()) return setError("Give the investigation a title.");
    if (chain && validateAddress(chain, address)) return setError(validateAddress(chain, address));
    if (!chain && address.trim()) return setError("Choose the chain for the subject address.");
    setBusy(true);
    setError(null);
    try {
      await api.createInvestigation({
        title: title.trim(),
        description: description.trim() || null,
        subject_chain: chain || null,
        subject_address: chain ? address.trim() : null,
      });
      setTitle("");
      setDescription("");
      setChain("");
      setAddress("");
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the investigation.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form" onSubmit={submit} noValidate aria-label="New investigation">
      <label className="field">
        <span>Title</span>
        <input id="inv-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
      </label>
      <label className="field">
        <span>Description (optional)</span>
        <textarea id="inv-description" value={description} onChange={(e) => setDescription(e.target.value)} rows={2} maxLength={2000} />
      </label>
      <div className="row">
        <label className="field">
          <span>Subject chain (optional)</span>
          <select id="inv-chain" value={chain} onChange={(e) => setChain(e.target.value)}>
            <option value="">None</option>
            {CHAINS.map((c) => (
              <option key={c.id} value={c.id}>{c.label}</option>
            ))}
          </select>
        </label>
        <label className="field grow">
          <span>Subject address</span>
          <input id="inv-address" value={address} onChange={(e) => setAddress(e.target.value)} disabled={!chain} spellCheck={false} />
        </label>
      </div>
      <FieldError id="inv-error" message={error} />
      <RoleHint can={can} />
      <button type="submit" className="btn btn-primary" disabled={busy || !can.allowed} title={can.reason}>
        {busy ? "Creating…" : "Create investigation"}
      </button>
    </form>
  );
}

export default function Investigations() {
  const list = useApi(api.investigations);
  return (
    <>
      <PageHeader
        title="Investigations"
        lede="Research cases you open by hand. Autonomous AI investigation is not implemented."
      />
      <div className="grid grid-side">
        <Panel title="New investigation">
          <CreateInvestigation onCreated={list.reload} />
        </Panel>
        <Panel title="All investigations">
          {list.status === "loading" ? <Loading /> : null}
          {list.status === "error" ? <ErrorState error={list.error} onRetry={list.reload} /> : null}
          {list.status === "ready" && list.data.length === 0 ? (
            <EmptyState title="No investigations yet">Create one with the form.</EmptyState>
          ) : null}
          {list.status === "ready" && list.data.length > 0 ? (
            <ul className="cards">
              {list.data.map((i) => (
                <li key={i.id}>
                  <Link to={`/investigations/${i.id}`} className="card-link">
                    <div className="card-top">
                      <strong>{i.title}</strong>
                      <Pill tone={statusTone(i.status)}>{humanize(i.status)}</Pill>
                    </div>
                    <div className="muted">
                      {i.finding_count} finding{i.finding_count === 1 ? "" : "s"} · updated {fmtTime(i.updated_at)}
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          ) : null}
        </Panel>
      </div>
    </>
  );
}

export function InvestigationDetail() {
  const id = Number(useParams().id);
  const inv = useApi(() => api.investigation(id), [id]);
  const [error, setError] = useState<string | null>(null);
  const can = useCan("PATCH", "/investigations/1");

  const setStatus = async (status: InvestigationStatus) => {
    setError(null);
    try {
      await api.updateInvestigation(id, status);
      inv.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update the status.");
    }
  };

  return (
    <>
      <PageHeader title="Investigation" actions={<Link className="btn" to="/investigations">All investigations</Link>} />
      {inv.status === "loading" ? <Loading /> : null}
      {inv.status === "error" ? <ErrorState error={inv.error} onRetry={inv.reload} /> : null}
      {inv.status === "ready" ? (
        <div className="grid grid-side">
          <Panel title={inv.data.title} aside={<Pill tone={statusTone(inv.data.status)}>{humanize(inv.data.status)}</Pill>}>
            {inv.data.description ? <p>{inv.data.description}</p> : <p className="muted">No description.</p>}
            <dl className="kv">
              <dt>Subject</dt>
              <dd>
                {inv.data.subject_chain && inv.data.subject_address ? (
                  <Link to={`/wallets/${inv.data.subject_chain}/${inv.data.subject_address}`}>
                    {chainLabel(inv.data.subject_chain)} <Mono>{inv.data.subject_address}</Mono>
                  </Link>
                ) : (
                  "None"
                )}
              </dd>
              <dt>Opened</dt>
              <dd className="mono">{fmtTime(inv.data.created_at)}</dd>
              <dt>Updated</dt>
              <dd className="mono">{fmtTime(inv.data.updated_at)}</dd>
            </dl>
            <div className="row" role="group" aria-label="Set status">
              {STATUSES.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="btn"
                  aria-pressed={inv.data.status === s}
                  disabled={inv.data.status === s || !can.allowed}
                  title={can.reason}
                  onClick={() => void setStatus(s)}
                >
                  {humanize(s)}
                </button>
              ))}
              <RoleHint can={can} />
            </div>
            <FieldError id="status-error" message={error} />
          </Panel>
          <Panel title="Findings">
            {inv.data.findings.length === 0 ? (
              <EmptyState title="No findings attached">
                Attach a detector finding from a wallet's Evidence tab. Findings must cite evidence.
              </EmptyState>
            ) : (
              <ul className="list">
                {inv.data.findings.map((f) => (
                  <li key={f.finding_id}>
                    <Mono>{f.finding_id}</Mono> · {humanize(f.finding_type)} · {f.claim_count} claims,{" "}
                    {f.evidence_count} evidence <Pill tone="info">{humanize(f.status)}</Pill>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <CaseResearch id={id} />
        </div>
      ) : null}
    </>
  );
}

function CaseResearch({ id }: { id: number }) {
  const links = useApi(() => api.caseResearchRuns(id), [id]);
  const can = useCan("DELETE", "/investigations/1/research-runs/x");
  return (
    <section className="panel" aria-label="Research runs">
      <div className="panel-head"><h2>Research runs</h2></div>
      {links.status === "loading" ? <Loading /> : null}
      {links.status === "error" ? <ErrorState error={links.error} onRetry={links.reload} /> : null}
      {links.status === "ready" && links.data.items.length === 0 ? (
        <p className="muted">No research runs linked. Open a run and choose "Add to case".</p>
      ) : null}
      {links.status === "ready" && links.data.items.length > 0 ? (
        <ul className="list">
          {links.data.items.map((l) => (
            <li key={l.run_id}>
              <Link to={`/research/runs/${encodeURIComponent(l.run_id)}`}>{l.question}</Link>
              <Pill tone={l.state.startsWith("completed") ? "ok" : l.state === "failed" ? "bad" : "info"}>{humanize(l.state)}</Pill>
              {l.note ? <span>{l.note}</span> : null}
              <button type="button" className="btn btn-small" disabled={!can.allowed} title={can.reason} onClick={() => { api.unlinkResearchRun(id, l.run_id).then(links.reload, links.reload); }}>
                Unlink
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

// ------------------------------------------------------------------------------------------
// Case workbench: templated cases, evidence, hypotheses, leads and the automated local scan.
// ------------------------------------------------------------------------------------------

const CASE_STATUSES: CaseStatus[] = ["open", "in_review", "closed", "archived"];
const HYPOTHESIS_STATUSES: HypothesisStatus[] = ["open", "supported", "refuted", "inconclusive"];
const LEAD_STATUSES: LeadStatus[] = ["new", "investigating", "resolved", "dismissed"];
const KIND_LABEL: Record<EvidenceKind, string> = {
  verified_observation: "Verified observation",
  statistical_signal: "Statistical signal",
  hypothesis: "Hypothesis",
};
const KIND_TONE: Record<EvidenceKind, "ok" | "warn" | "idle"> = {
  verified_observation: "ok",
  statistical_signal: "warn",
  hypothesis: "idle",
};
const caseTone = (s: CaseStatus) => (s === "open" ? "info" : s === "in_review" ? "warn" : "idle");
const errMsg = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);

function CaseTypeSelect({ id, value, onChange, templates }: {
  id: string; value: CaseType | ""; onChange: (v: CaseType) => void; templates: CaseTemplate[];
}) {
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value as CaseType)}>
      <option value="" disabled>Choose a case type</option>
      {templates.map((t) => (
        <option key={t.case_type} value={t.case_type}>{t.name}</option>
      ))}
    </select>
  );
}

function NewCaseForm({ templates, onCreated }: { templates: CaseTemplate[]; onCreated: () => void }) {
  const [title, setTitle] = useState("");
  const [caseType, setCaseType] = useState<CaseType | "">("");
  const [chain, setChain] = useState("");
  const [summary, setSummary] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return setError("Give the case a title.");
    if (!caseType) return setError("Choose a case type.");
    setBusy(true);
    setError(null);
    try {
      await api.createCase({ title: title.trim(), case_type: caseType, chain: chain || null, summary: summary.trim() || null });
      setTitle(""); setCaseType(""); setChain(""); setSummary("");
      onCreated();
    } catch (err) {
      setError(errMsg(err, "Could not create the case."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form" onSubmit={submit} noValidate aria-label="New case">
      <label className="field">
        <span>Title</span>
        <input id="case-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
      </label>
      <div className="row">
        <label className="field grow">
          <span>Case type</span>
          <CaseTypeSelect id="case-type" value={caseType} onChange={setCaseType} templates={templates} />
        </label>
        <label className="field">
          <span>Chain (optional)</span>
          <select id="case-chain" value={chain} onChange={(e) => setChain(e.target.value)}>
            <option value="">None</option>
            {CHAINS.map((c) => (<option key={c.id} value={c.id}>{c.label}</option>))}
          </select>
        </label>
      </div>
      <label className="field">
        <span>Summary (optional)</span>
        <textarea id="case-summary" value={summary} onChange={(e) => setSummary(e.target.value)} rows={2} maxLength={2000} />
      </label>
      <FieldError id="case-error" message={error} />
      <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Creating…" : "Create case"}</button>
    </form>
  );
}

function RunScanForm({ templates, onDone }: { templates: CaseTemplate[]; onDone: (r: CaseRunResult) => void }) {
  const [caseType, setCaseType] = useState<CaseType | "">("");
  const [chain, setChain] = useState("ethereum");
  const [targetType, setTargetType] = useState<"address" | "tx">("address");
  const [target, setTarget] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!caseType) return setError("Choose a case type.");
    if (targetType === "address") {
      const problem = validateAddress(chain, target);
      if (problem) return setError(problem);
    } else if (!target.trim()) return setError("Enter a transaction id.");
    setBusy(true);
    setError(null);
    try {
      onDone(await api.runInvestigation({ case_type: caseType, chain, target_type: targetType, target: target.trim() }));
    } catch (err) {
      setError(errMsg(err, "The automated scan failed."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form" onSubmit={submit} noValidate aria-label="Automated scan">
      <p className="muted">
        Opens a case from the template and scans data already stored locally. No provider is queried.
      </p>
      <div className="row">
        <label className="field grow">
          <span>Case type</span>
          <CaseTypeSelect id="run-type" value={caseType} onChange={setCaseType} templates={templates} />
        </label>
        <label className="field">
          <span>Chain</span>
          <select id="run-chain" value={chain} onChange={(e) => setChain(e.target.value)}>
            {CHAINS.map((c) => (<option key={c.id} value={c.id}>{c.label}</option>))}
          </select>
        </label>
        <label className="field">
          <span>Target kind</span>
          <select id="run-target-type" value={targetType} onChange={(e) => setTargetType(e.target.value as "address" | "tx")}>
            <option value="address">Address</option>
            <option value="tx">Transaction</option>
          </select>
        </label>
      </div>
      <label className="field">
        <span>Target</span>
        <input id="run-target" value={target} onChange={(e) => setTarget(e.target.value)} spellCheck={false} />
      </label>
      <FieldError id="run-error" message={error} />
      <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Scanning…" : "Run automated scan"}</button>
    </form>
  );
}

function RunSummary({ result }: { result: CaseRunResult }) {
  return (
    <section aria-label="Scan result" className="stack">
      <p>
        Opened <Link to={`/cases/${result.case.id}`}>{result.case.title}</Link>:{" "}
        {result.evidence.length} evidence, {result.hypotheses.length} suggested hypotheses,{" "}
        {result.leads.length} leads.
      </p>
      {result.coverage_gaps.length > 0 ? (
        <div role="note" className="state state-empty">
          <strong>Coverage gaps</strong>
          <ul>{result.coverage_gaps.map((g) => (<li key={g}>{g}</li>))}</ul>
        </div>
      ) : null}
      {result.required_evidence_unconfirmed.length > 0 ? (
        <div>
          <strong>Required evidence not confirmed by the scan</strong>
          <ul>{result.required_evidence_unconfirmed.map((r) => (<li key={r}>{r}</li>))}</ul>
        </div>
      ) : null}
      <p className="muted">{result.note}</p>
    </section>
  );
}

export function CaseWorkbench() {
  const list = useApi(api.cases);
  const templates = useApi(api.caseTemplates);
  const [result, setResult] = useState<CaseRunResult | null>(null);
  const tpls = templates.status === "ready" ? templates.data : [];
  return (
    <>
      <PageHeader
        title="Case Workbench"
        lede="Templated cases with evidence, hypotheses and leads. Every item is labelled as a verified observation, statistical signal or hypothesis."
      />
      <div className="grid grid-side">
        <div className="stack">
          <Panel title="New case">
            {templates.status === "error" ? <ErrorState error={templates.error} onRetry={templates.reload} /> : null}
            {templates.status === "loading" ? <Loading /> : null}
            {templates.status === "ready" ? <NewCaseForm templates={tpls} onCreated={list.reload} /> : null}
          </Panel>
          <Panel title="Automated scan">
            {templates.status === "ready" ? (
              <RunScanForm templates={tpls} onDone={(r) => { setResult(r); list.reload(); }} />
            ) : (
              <p className="muted">Templates are required to run a scan.</p>
            )}
            {result ? <RunSummary result={result} /> : null}
          </Panel>
        </div>
        <Panel title="All cases">
          {list.status === "loading" ? <Loading /> : null}
          {list.status === "error" ? <ErrorState error={list.error} onRetry={list.reload} /> : null}
          {list.status === "ready" && list.data.length === 0 ? (
            <EmptyState title="No cases yet">Create one or run an automated scan.</EmptyState>
          ) : null}
          {list.status === "ready" && list.data.length > 0 ? (
            <ul className="cards">
              {list.data.map((c: Case) => (
                <li key={c.id}>
                  <Link to={`/cases/${c.id}`} className="card-link">
                    <div className="card-top">
                      <strong>{c.title}</strong>
                      <Pill tone={caseTone(c.status)}>{humanize(c.status)}</Pill>
                    </div>
                    <div className="muted">
                      {humanize(c.case_type)}{c.chain ? ` · ${chainLabel(c.chain)}` : ""} · updated {fmtTime(c.updated_at)}
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          ) : null}
        </Panel>
      </div>
    </>
  );
}

function EvidencePanel({ id, detail, reload }: { id: number; detail: CaseDetail; reload: () => void }) {
  const [kind, setKind] = useState<EvidenceKind>("verified_observation");
  const [description, setDescription] = useState("");
  const [subject, setSubject] = useState("");
  const [source, setSource] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (!description.trim() || !subject.trim() || !source.trim())
      return setError("Description, subject and source are required: evidence must carry provenance.");
    setError(null);
    try {
      await api.addCaseEvidence(id, {
        kind, chain: detail.case.chain, subject_type: "address", subject_ref: subject.trim(),
        description: description.trim(), source_kind: "analyst", source_ref: source.trim(),
        collected_at: new Date().toISOString(),
      });
      setDescription(""); setSubject(""); setSource("");
      reload();
    } catch (err) {
      setError(errMsg(err, "Could not add the evidence."));
    }
  };

  return (
    <Panel title="Evidence">
      {detail.evidence.length === 0 ? (
        <EmptyState title="No evidence recorded">Add evidence with its source below.</EmptyState>
      ) : (
        <ul className="list">
          {detail.evidence.map((ev) => (
            <li key={ev.id}>
              <Pill tone={KIND_TONE[ev.kind]}>{KIND_LABEL[ev.kind]}</Pill>
              <span>#{ev.id} {ev.description}</span>
              <span className="muted">
                {humanize(ev.source_kind)}{ev.source_ref ? ` · ${ev.source_ref}` : ""} · {fmtTime(ev.collected_at)}
              </span>
            </li>
          ))}
        </ul>
      )}
      <form className="form" onSubmit={add} noValidate aria-label="Add evidence">
        <label className="field">
          <span>Evidence kind</span>
          <select id="ev-kind" value={kind} onChange={(e) => setKind(e.target.value as EvidenceKind)}>
            {(Object.keys(KIND_LABEL) as EvidenceKind[]).map((k) => (<option key={k} value={k}>{KIND_LABEL[k]}</option>))}
          </select>
        </label>
        <label className="field">
          <span>Subject (address)</span>
          <input id="ev-subject" value={subject} onChange={(e) => setSubject(e.target.value)} spellCheck={false} />
        </label>
        <label className="field">
          <span>Description</span>
          <input id="ev-description" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={1000} />
        </label>
        <label className="field">
          <span>Source reference</span>
          <input id="ev-source" value={source} onChange={(e) => setSource(e.target.value)} maxLength={300} />
        </label>
        <FieldError id="ev-error" message={error} />
        <button type="submit" className="btn">Add evidence</button>
      </form>
    </Panel>
  );
}

function HypothesesPanel({ id, detail, reload }: { id: number; detail: CaseDetail; reload: () => void }) {
  const [statement, setStatement] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (!statement.trim()) return setError("Write the hypothesis statement.");
    setError(null);
    try {
      await api.addCaseHypothesis(id, statement.trim());
      setStatement("");
      reload();
    } catch (err) {
      setError(errMsg(err, "Could not add the hypothesis."));
    }
  };
  const setStatus = async (hid: number, status: HypothesisStatus) => {
    setError(null);
    try {
      // The backend refuses "supported" unless evidence is cited; cite all case evidence ids on request.
      const h = detail.hypotheses.find((x) => x.id === hid);
      const ids = h && h.evidence_ids.length > 0 ? h.evidence_ids : undefined;
      await api.updateCaseHypothesis(id, hid, { status, ...(ids ? { evidence_ids: ids } : {}) });
      reload();
    } catch (err) {
      setError(errMsg(err, "Could not update the hypothesis."));
    }
  };

  return (
    <Panel title="Hypotheses">
      {detail.hypotheses.length === 0 ? (
        <EmptyState title="No hypotheses">State what you think happened, then test it against evidence.</EmptyState>
      ) : (
        <ul className="list">
          {detail.hypotheses.map((h) => (
            <li key={h.id}>
              <span>{h.statement}</span>
              <Pill tone={h.status === "supported" ? "ok" : h.status === "refuted" ? "bad" : "idle"}>{humanize(h.status)}</Pill>
              <span className="muted">
                {h.evidence_ids.length === 0 ? "no evidence cited" : `cites evidence ${h.evidence_ids.map((n) => `#${n}`).join(", ")}`}
              </span>
              <select
                aria-label={`Status of hypothesis ${h.id}`}
                value={h.status}
                onChange={(e) => void setStatus(h.id, e.target.value as HypothesisStatus)}
              >
                {HYPOTHESIS_STATUSES.map((s) => (<option key={s} value={s}>{humanize(s)}</option>))}
              </select>
            </li>
          ))}
        </ul>
      )}
      <form className="form" onSubmit={add} noValidate aria-label="Add hypothesis">
        <label className="field">
          <span>Hypothesis</span>
          <input id="hyp-statement" value={statement} onChange={(e) => setStatement(e.target.value)} maxLength={1000} />
        </label>
        <FieldError id="hyp-error" message={error} />
        <button type="submit" className="btn">Add hypothesis</button>
      </form>
    </Panel>
  );
}

function LeadsPanel({ id, detail, reload }: { id: number; detail: CaseDetail; reload: () => void }) {
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return setError("Write the lead title.");
    setError(null);
    try {
      await api.addCaseLead(id, title.trim());
      setTitle("");
      reload();
    } catch (err) {
      setError(errMsg(err, "Could not add the lead."));
    }
  };
  const setStatus = async (lid: number, status: LeadStatus) => {
    setError(null);
    try {
      await api.updateCaseLead(id, lid, status);
      reload();
    } catch (err) {
      setError(errMsg(err, "Could not update the lead."));
    }
  };

  return (
    <Panel title="Leads">
      {detail.leads.length === 0 ? (
        <EmptyState title="No leads">Record the next things to check.</EmptyState>
      ) : (
        <ul className="list">
          {detail.leads.map((l) => (
            <li key={l.id}>
              <Pill tone="info">P{l.priority}</Pill>
              <span>{l.title}</span>
              {l.subject_ref ? <Mono>{l.subject_ref}</Mono> : null}
              <select
                aria-label={`Status of lead ${l.id}`}
                value={l.status}
                onChange={(e) => void setStatus(l.id, e.target.value as LeadStatus)}
              >
                {LEAD_STATUSES.map((s) => (<option key={s} value={s}>{humanize(s)}</option>))}
              </select>
            </li>
          ))}
        </ul>
      )}
      <form className="form" onSubmit={add} noValidate aria-label="Add lead">
        <label className="field">
          <span>Lead</span>
          <input id="lead-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
        </label>
        <FieldError id="lead-error" message={error} />
        <button type="submit" className="btn">Add lead</button>
      </form>
    </Panel>
  );
}

export function CaseDetailPage() {
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const detail = useApi(() => api.caseDetail(id), [id]);
  const [error, setError] = useState<string | null>(null);

  const setStatus = async (status: CaseStatus) => {
    setError(null);
    try {
      await api.updateCaseStatus(id, status);
      detail.reload();
    } catch (err) {
      setError(errMsg(err, "Could not update the case status."));
    }
  };

  return (
    <>
      <PageHeader
        title="Case"
        actions={<button type="button" className="btn" onClick={() => void navigate("/cases")}>All cases</button>}
      />
      {detail.status === "loading" ? <Loading /> : null}
      {detail.status === "error" ? <ErrorState error={detail.error} onRetry={detail.reload} /> : null}
      {detail.status === "ready" ? (
        <div className="stack">
          <Panel title={detail.data.case.title} aside={<Pill tone={caseTone(detail.data.case.status)}>{humanize(detail.data.case.status)}</Pill>}>
            {detail.data.case.summary ? <p>{detail.data.case.summary}</p> : <p className="muted">No summary.</p>}
            <dl className="kv">
              <dt>Type</dt><dd>{humanize(detail.data.case.case_type)}</dd>
              <dt>Chain</dt><dd>{detail.data.case.chain ? chainLabel(detail.data.case.chain) : "None"}</dd>
              <dt>Opened</dt><dd className="mono">{fmtTime(detail.data.case.created_at)}</dd>
            </dl>
            <div className="row" role="group" aria-label="Set case status">
              {CASE_STATUSES.map((s) => (
                <button key={s} type="button" className="btn" aria-pressed={detail.data.case.status === s}
                  disabled={detail.data.case.status === s} onClick={() => void setStatus(s)}>
                  {humanize(s)}
                </button>
              ))}
            </div>
            <FieldError id="case-status-error" message={error} />
          </Panel>
          <EvidencePanel id={id} detail={detail.data} reload={detail.reload} />
          <HypothesesPanel id={id} detail={detail.data} reload={detail.reload} />
          <LeadsPanel id={id} detail={detail.data} reload={detail.reload} />
        </div>
      ) : null}
    </>
  );
}
