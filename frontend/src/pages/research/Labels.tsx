import { RoleHint } from "../../auth/RoleHint";
import { useCan } from "../../auth/useAuth";
import { useState, type FormEvent } from "react";
import { ApiError, api } from "../../api/client";
import type { LabelStatus } from "../../api/types";
import { Address } from "../../components/Address";
import { DataTable } from "../../components/DataTable";
import { ErrorState, Loading, Panel, Pill } from "../../components/ui";
import { CHAINS, chainLabel, validateAddress } from "../../lib/address";
import { fmtTime, humanize } from "../../lib/format";
import { useApi } from "../../lib/useApi";

const STATUSES: LabelStatus[] = ["verified", "corroborated", "analyst_reviewed", "inferred", "weak", "unknown"];
const TRAINABLE = new Set(["verified", "corroborated", "analyst_reviewed"]);

/**
 * Label curation: every label carries status and provenance; weak/inferred labels train only after a
 * reviewer accepts them. Model predictions are never turned into labels here.
 */
export function LabelsPanel() {
  const [nonce, setNonce] = useState(0);
  const page = useApi(() => api.labels(50, 0), [nonce]);
  const tax = useApi(api.taxonomy);
  const reload = () => setNonce((n) => n + 1);
  return (
    <>
      <Panel title="Training readiness">
        {page.status === "ready" ? (
          <ul className="list">
            {CHAINS.map((c) => (
              <li key={c.id}>
                <strong>{c.label}:</strong> {page.data.counts.trainable_wallets[c.id] ?? 0} of {page.data.counts.min_labelled_wallets} distinct
                wallets with trainable labels
              </li>
            ))}
          </ul>
        ) : null}
        <p className="note">
          Trainable means verified, corroborated or analyst-reviewed, or weak/inferred and accepted by a reviewer.
          Training refuses to run below the threshold; nothing is fabricated to reach it.
        </p>
      </Panel>
      <AddLabel labels={tax.status === "ready" ? tax.data.labels : []} onAdded={reload} />
      <Panel title="Labels">
        {page.status === "loading" ? <Loading /> : null}
        {page.status === "error" ? <ErrorState error={page.error} onRetry={page.reload} /> : null}
        {page.status === "ready" ? <LabelTable rows={page.data.items} onReviewed={reload} /> : null}
      </Panel>
    </>
  );
}

function AddLabel({ labels, onAdded }: { labels: { id: string; chains: string[]; sensitive: boolean }[]; onAdded: () => void }) {
  const [chain, setChain] = useState("ethereum");
  const [address, setAddress] = useState("");
  const [label, setLabel] = useState("");
  const [status, setStatus] = useState<LabelStatus>("weak");
  const [source, setSource] = useState("");
  const [sourceRef, setSourceRef] = useState("");
  const [observed, setObserved] = useState(new Date().toISOString().slice(0, 10));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const can = useCan("POST", "/ml/labels");
  const options = labels.filter((l) => l.chains.includes(chain));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!can.allowed) return;
    const bad = validateAddress(chain, address) ?? (!label ? "Choose a label." : source.trim().length < 2 ? "Name the source of this label." : null);
    if (bad) return setError(bad);
    setBusy(true);
    setError(null);
    api.addLabels([{ chain, address: address.trim(), label, value: true, status, source: source.trim(), source_ref: sourceRef.trim() || null, observed_at: `${observed}T00:00:00Z`, note: null }])
      .then(() => { setBusy(false); setAddress(""); onAdded(); }, (err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Could not add the label.");
        setBusy(false);
      });
  };
  return (
    <Panel title="Add a label">
      <form className="form" onSubmit={submit}>
        <div className="row">
          <label className="field">Chain
            <select value={chain} onChange={(e) => { setChain(e.target.value); setLabel(""); }}>
              {CHAINS.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
          </label>
          <label className="field grow">Address
            <input value={address} onChange={(e) => setAddress(e.target.value)} spellCheck={false} autoCapitalize="off" />
          </label>
        </div>
        <div className="row">
          <label className="field">Label
            <select value={label} onChange={(e) => setLabel(e.target.value)}>
              <option value="">Choose…</option>
              {options.map((l) => <option key={l.id} value={l.id}>{humanize(l.id)}{l.sensitive ? " (sensitive)" : ""}</option>)}
            </select>
          </label>
          <label className="field">Status
            <select value={status} onChange={(e) => setStatus(e.target.value as LabelStatus)}>
              {STATUSES.map((s) => <option key={s} value={s}>{humanize(s)}</option>)}
            </select>
          </label>
          <label className="field">Observed on
            <input type="date" value={observed} onChange={(e) => setObserved(e.target.value)} />
          </label>
        </div>
        <div className="row">
          <label className="field grow">Source
            <input value={source} maxLength={120} onChange={(e) => setSource(e.target.value)} placeholder="Who or what asserts this label" />
          </label>
          <label className="field grow">Source reference (optional)
            <input value={sourceRef} maxLength={500} onChange={(e) => setSourceRef(e.target.value)} placeholder="URL, document or case id" />
          </label>
        </div>
        {error ? <p className="field-error" role="alert">{error}</p> : null}
        <p className="note">Only label what an independent source supports. Do not record identities of people.</p>
        <div className="row"><button type="submit" className="btn btn-primary" disabled={busy || !can.allowed} title={can.reason}>Add label</button><RoleHint can={can} /></div>
      </form>
    </Panel>
  );
}

function LabelTable({ rows, onReviewed }: { rows: import("../../api/types").LabelRow[]; onReviewed: () => void }) {
  const [reviewer, setReviewer] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const can = useCan("POST", "/ml/labels/1/reviews");
  const review = (id: number, decision: "accepted" | "rejected" | "needs_evidence") => {
    if (!reviewer.trim()) return setError(new ApiError(422, "invalid_input", "Enter a reviewer name first."));
    setError(null);
    api.reviewLabel(id, { decision, reviewer: reviewer.trim(), note: null }).then(onReviewed, (e: unknown) => {
      setError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Review failed."));
    });
  };
  return (
    <>
      <RoleHint can={can} />
      <label className="field">Reviewer
        <input value={reviewer} maxLength={80} onChange={(e) => setReviewer(e.target.value)} />
      </label>
      {error ? <ErrorState error={error} /> : null}
      <DataTable
        caption="Labels"
        rows={rows}
        rowKey={(r) => String(r.id)}
        empty="No labels yet. Real model training needs reviewed labels with provenance."
        columns={[
          { key: "label", header: "Label", priority: 1, render: (r) => humanize(r.label) },
          { key: "wallet", header: "Wallet", priority: 2, render: (r) => <span>{chainLabel(r.chain)} <Address value={r.address} chain={r.chain} link copy={false} /></span> },
          { key: "status", header: "Status", priority: 2, render: (r) => <Pill tone={TRAINABLE.has(r.status) ? "ok" : "warn"}>{humanize(r.status)}</Pill> },
          { key: "review", header: "Review", priority: 2, render: (r) => (r.latest_review ? humanize(r.latest_review) : "Not reviewed") },
          { key: "source", header: "Source", priority: 3, render: (r) => r.source },
          { key: "observed", header: "Observed", priority: 3, render: (r) => fmtTime(r.observed_at) },
          {
            key: "act", header: "Decision", priority: 2, render: (r) => (
              <span className="row">
                <button type="button" className="btn btn-small" disabled={!can.allowed} title={can.reason} onClick={() => review(r.id, "accepted")}>Accept</button>
                <button type="button" className="btn btn-small" disabled={!can.allowed} title={can.reason} onClick={() => review(r.id, "rejected")}>Reject</button>
              </span>
            ),
          },
        ]}
      />
    </>
  );
}
