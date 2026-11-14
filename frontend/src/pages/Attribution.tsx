import { useState, type FormEvent } from "react";
import { ApiError, api } from "../api/client";
import type { EntityKind, EntityLabel, EntityLabelPage, LabelCategory } from "../api/types";
import { RoleHint } from "../auth/RoleHint";
import { useCan } from "../auth/useAuth";
import { Address } from "../components/Address";
import { DataTable } from "../components/DataTable";
import { LabelEvidenceList, humanLabel } from "../components/discovery";
import { EvidenceEditor, evidenceProblem, newEvidenceRow, toEvidenceInput, type EvidenceRow } from "../components/EvidenceEditor";
import { Modal } from "../components/Modal";
import { EmptyState, ErrorState, FieldError, Loading, PageHeader, Panel, Pill } from "../components/ui";
import { CHAINS, chainLabel, validateAddress } from "../lib/address";
import { fmtTime } from "../lib/format";
import { useApi } from "../lib/useApi";
import { DataOriginBanner } from "../components/intel";

const KINDS: EntityKind[] = ["wallet", "contract", "token", "pool", "program"];
const CATEGORIES: LabelCategory[] = ["exchange", "protocol", "bridge", "market_maker", "treasury", "team", "other"];
const HUMAN_KINDS = new Set(["url", "transaction", "signal"]);
const MIN_NOTE = 10;
const message = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);
const isPersonAdded = (e: { kind: string; added_by?: string | undefined }) =>
  HUMAN_KINDS.has(e.kind) && !(e.added_by ?? "").startsWith("ai:") && !(e.added_by ?? "").startsWith("signal:");

function SourcePill({ label }: { label: EntityLabel }) {
  return label.source === "ai" ? <Pill tone="warn">AI proposal</Pill> : <Pill tone="idle">{label.source}</Pill>;
}

function ReviewDialog({ label, onClose, onDone }: { label: EntityLabel; onClose: () => void; onDone: () => void }) {
  const [decision, setDecision] = useState<"approve" | "reject">("approve");
  const [note, setNote] = useState("");
  const [rows, setRows] = useState<EvidenceRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    if (note.trim().length < MIN_NOTE) return setError(`Write a review note of at least ${MIN_NOTE} characters.`);
    const problem = evidenceProblem(rows);
    if (problem) return setError(problem);
    const extra = toEvidenceInput(rows);
    if (decision === "approve" && !label.evidence.some(isPersonAdded) && !extra.some((x) => HUMAN_KINDS.has(x.kind)))
      return setError("Approval needs at least one url, transaction or signal evidence item added by a person. Add one below; a note is not enough.");
    setBusy(true);
    setError(null);
    try {
      await api.reviewEntityLabel(label.id, { decision, note: note.trim(), evidence: extra });
      onDone();
    } catch (err) {
      setBusy(false);
      setError(message(err, "The review could not be saved."));
    }
  };

  return (
    <Modal title={`Review label: ${label.name}`} onClose={onClose} busy={busy}>
      <p className="muted">
        {humanLabel(label.entity_kind)} on {chainLabel(label.chain)}, proposed as {humanLabel(label.category)} by {label.proposed_by}.
      </p>
      <p className="mono wrap-any">{label.ref}</p>
      <LabelEvidenceList items={label.evidence} chain={label.chain} />
      <form className="form" onSubmit={submit} noValidate aria-label="Review label">
        <fieldset className="probe-select">
          <legend>Decision</legend>
          <label className="check">
            <input type="radio" name="decision" checked={decision === "approve"} onChange={() => setDecision("approve")} />
            Approve
          </label>
          <label className="check">
            <input type="radio" name="decision" checked={decision === "reject"} onChange={() => setDecision("reject")} />
            Reject
          </label>
        </fieldset>
        <label className="field">
          <span>Review note (at least {MIN_NOTE} characters)</span>
          <textarea rows={3} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
        <p className="note">{note.trim().length} of at most 500 characters</p>
        <EvidenceEditor rows={rows} onChange={setRows} legend="Extra evidence (optional)" />
        {decision === "approve" ? (
          <p className="note">Approval counts only evidence a person added: a public url, a stored transaction or a stored signal. Evidence from an AI model or a signal does not count, and a note does not count.</p>
        ) : null}
        <FieldError id="review-error" message={error} />
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Saving…" : decision === "approve" ? "Approve label" : "Reject label"}</button>
        </div>
      </form>
    </Modal>
  );
}

function ProposeForm({ onProposed }: { onProposed: () => void }) {
  const can = useCan("POST", "/entities/labels");
  const [chain, setChain] = useState("ethereum");
  const [ref, setRef] = useState("");
  const [kind, setKind] = useState<EntityKind>("contract");
  const [category, setCategory] = useState<LabelCategory>("exchange");
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [rows, setRows] = useState<EvidenceRow[]>([newEvidenceRow()]);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!can.allowed) return;
    setDone(null);
    const refProblem = validateAddress(chain, ref);
    if (refProblem) return setError(refProblem);
    if (!name.trim()) return setError("Name the organisation or protocol.");
    const problem = evidenceProblem(rows);
    if (problem) return setError(problem);
    setBusy(true);
    setError(null);
    try {
      const created = await api.proposeLabel({ chain, ref: ref.trim(), entity_kind: kind, category, name: name.trim(), evidence: toEvidenceInput(rows), note: note.trim() || null });
      setDone(`Proposed "${created.name}". It stays a proposal until a person reviews it.`);
      setRef("");
      setName("");
      setNote("");
      setRows([newEvidenceRow()]);
      onProposed();
    } catch (err) {
      setError(message(err, "Could not propose the label."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Propose a label">
      <form className="form" onSubmit={submit} noValidate aria-label="Propose label">
        <div className="row">
          <label className="field">
            <span>Chain</span>
            <select value={chain} onChange={(e) => { setChain(e.target.value); setError(null); }}>
              {CHAINS.map((c) => (
                <option key={c.id} value={c.id}>{c.label}</option>
              ))}
            </select>
          </label>
          <label className="field grow">
            <span>Address or program ID</span>
            <input value={ref} onChange={(e) => setRef(e.target.value)} spellCheck={false} autoComplete="off" />
          </label>
        </div>
        <div className="row">
          <label className="field">
            <span>Entity kind</span>
            <select value={kind} onChange={(e) => setKind(e.target.value as EntityKind)}>
              {KINDS.map((k) => (
                <option key={k} value={k}>{k}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Category</span>
            <select value={category} onChange={(e) => setCategory(e.target.value as LabelCategory)}>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{humanLabel(c)}</option>
              ))}
            </select>
          </label>
          <label className="field grow">
            <span>Organisation or protocol name</span>
            <input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
          </label>
        </div>
        <label className="field">
          <span>Note (optional)</span>
          <input value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
        </label>
        <EvidenceEditor rows={rows} onChange={setRows} />
        <FieldError id="propose-error" message={error} />
        {done ? <p role="status" className="ok-text">{done}</p> : null}
        <RoleHint can={can} />
        <button type="submit" className="btn btn-primary" disabled={busy || !can.allowed} title={can.reason}>Propose label</button>
      </form>
    </Panel>
  );
}

function EvidenceCell({ label }: { label: EntityLabel }) {
  return (
    <details>
      <summary>Evidence ({label.evidence.length})</summary>
      <LabelEvidenceList items={label.evidence} chain={label.chain} />
    </details>
  );
}

type QueueState = ReturnType<typeof useApi<EntityLabelPage>>;
function QueueView({ state, reviewer, onReview }: { state: QueueState; reviewer: ReturnType<typeof useCan>; onReview: (l: EntityLabel) => void }) {
  return (
    <Panel title="Review queue" aside={state.status === "ready" ? <span className="muted" role="status">{state.data.total} waiting</span> : null}>
      <RoleHint can={reviewer} />
      {state.status === "loading" ? <Loading /> : null}
      {state.status === "error" ? <ErrorState error={state.error} onRetry={state.reload} /> : null}
      {state.status === "ready" ? (
        state.data.items.length === 0 ? (
          <EmptyState title="No proposals waiting for review">Proposed labels appear here. Nothing is attributed until a person approves it with evidence.</EmptyState>
        ) : (
          <DataTable
            caption="Proposed labels"
            rows={state.data.items}
            rowKey={(l) => String(l.id)}
            columns={[
              { key: "name", header: "Proposed label", priority: 1, render: (l) => l.name },
              { key: "ref", header: "Subject", priority: 2, render: (l) => <Address value={l.ref} /> },
              { key: "chain", header: "Chain", priority: 2, render: (l) => chainLabel(l.chain) },
              { key: "cat", header: "Category", priority: 2, render: (l) => `${humanLabel(l.category)} (${l.entity_kind})` },
              { key: "src", header: "Source", priority: 2, render: (l) => <SourcePill label={l} /> },
              { key: "by", header: "Proposed by", priority: 2, render: (l) => l.proposed_by },
              { key: "at", header: "Proposed", priority: 3, render: (l) => fmtTime(l.created_at) },
              { key: "note", header: "Note", priority: 3, render: (l) => l.note ?? <span className="muted">none</span> },
              { key: "ev", header: "Evidence", priority: 2, render: (l) => <EvidenceCell label={l} /> },
              {
                key: "act", header: "", priority: 2, render: (l) => (
                  <button type="button" className="btn btn-small" aria-label={`Review ${l.name}`} onClick={() => onReview(l)} disabled={!reviewer.allowed} title={reviewer.reason}>
                    Review
                  </button>
                ),
              },
            ]}
          />
        )
      ) : null}
    </Panel>
  );
}

export default function Attribution() {
  const queue = useApi(() => api.entityLabels({ status: "proposed", limit: 50 }));
  const approved = useApi(() => api.entityLabels({ status: "approved", limit: 50 }));
  const reviewer = useCan("POST", "/entities/labels/1/review");
  const [reviewing, setReviewing] = useState<EntityLabel | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const scope = approved.status === "ready" ? approved.data.scope : queue.status === "ready" ? queue.data.scope : null;
  const reloadAll = () => {
    queue.reload();
    approved.reload();
  };

  return (
    <>
      <PageHeader title="Attribution" lede="Reviewed labels that attribute an address to an organisation or protocol." />
      {queue.status === "ready" ? <DataOriginBanner origin={queue.data.data_origin ?? "stored_observations"} /> : null}
      <div className="stack disc">
        <Panel title="How labels work">
          <ul className="bullets">
            <li>A label attributes an address, contract, token, pool or program to an <strong>organisation or protocol</strong>, never to a person.</li>
            <li>A proposal is a claim waiting for a person to check its evidence. It is not shown as attribution anywhere until approved.</li>
            <li>Approval needs a review note and at least one evidence item added by a person: a public url, a stored transaction or a stored signal. A note is not evidence.</li>
            <li>A label proposed by an AI model or by a signal stays a proposal. Only a person can approve it, and AI-supplied evidence does not count.</li>
            <li>No label means unattributed. It never means the address is not an exchange or anything else.</li>
          </ul>
          {scope ? <p className="note">{scope}</p> : null}
        </Panel>
        {flash ? <p role="status" className="ok-text">{flash}</p> : null}
        <QueueView state={queue} reviewer={reviewer} onReview={(l) => { setFlash(null); setReviewing(l); }} />
        <Panel title="Approved labels" aside={approved.status === "ready" ? <span className="muted" role="status">{approved.data.total} approved</span> : null}>
          {approved.status === "loading" ? <Loading /> : null}
          {approved.status === "error" ? <ErrorState error={approved.error} onRetry={approved.reload} /> : null}
          {approved.status === "ready" ? (
            approved.data.items.length === 0 ? (
              <EmptyState title="No approved labels">Every subject is currently unattributed.</EmptyState>
            ) : (
              <DataTable
                caption="Approved labels"
                rows={approved.data.items}
                rowKey={(l) => String(l.id)}
                columns={[
                  { key: "name", header: "Label", priority: 1, render: (l) => l.name },
                  { key: "ref", header: "Subject", priority: 2, render: (l) => <Address value={l.ref} /> },
                  { key: "chain", header: "Chain", priority: 2, render: (l) => chainLabel(l.chain) },
                  { key: "cat", header: "Category", priority: 2, render: (l) => `${humanLabel(l.category)} (${l.entity_kind})` },
                  { key: "src", header: "Proposed by", priority: 3, render: (l) => <span>{l.proposed_by} <SourcePill label={l} /></span> },
                  { key: "rev", header: "Reviewed by", priority: 2, render: (l) => l.reviewed_by ?? "not recorded" },
                  { key: "at", header: "Reviewed", priority: 3, render: (l) => fmtTime(l.reviewed_at) },
                  { key: "rn", header: "Review note", priority: 3, render: (l) => l.review_note ?? <span className="muted">none</span> },
                  { key: "ev", header: "Evidence", priority: 2, render: (l) => <EvidenceCell label={l} /> },
                ]}
              />
            )
          ) : null}
        </Panel>
        <ProposeForm onProposed={reloadAll} />
      </div>
      {reviewing ? (
        <ReviewDialog
          label={reviewing}
          onClose={() => setReviewing(null)}
          onDone={() => {
            setFlash(`Reviewed "${reviewing.name}".`);
            setReviewing(null);
            reloadAll();
          }}
        />
      ) : null}
    </>
  );
}
