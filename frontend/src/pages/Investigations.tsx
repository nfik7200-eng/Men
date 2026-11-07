import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { InvestigationStatus } from "../api/types";
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

  const submit = async (e: FormEvent) => {
    e.preventDefault();
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
      <button type="submit" className="btn btn-primary" disabled={busy}>
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
                  disabled={inv.data.status === s}
                  onClick={() => void setStatus(s)}
                >
                  {humanize(s)}
                </button>
              ))}
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
              <button type="button" className="btn btn-small" onClick={() => { api.unlinkResearchRun(id, l.run_id).then(links.reload, links.reload); }}>
                Unlink
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
