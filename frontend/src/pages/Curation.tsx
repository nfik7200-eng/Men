import { useState } from "react";
import { api } from "../api/client";
import type { CurationDecision, CurationQueueItem } from "../api/types";
import { EmptyState, ErrorState, Loading, PageHeader, Panel, Pill, Stat } from "../components/ui";
import { useApi } from "../lib/useApi";
import { humanize } from "../lib/format";

export default function Curation() {
  const queue = useApi(api.curationQueue);
  const stats = useApi(api.curationStats);
  const history = useApi(api.curationFeedback);
  const [reviewer, setReviewer] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const keyOf = (i: CurationQueueItem) => `${i.classification_id}:${i.label}`;

  async function decide(item: CurationQueueItem, decision: CurationDecision) {
    setMessage(null);
    if (!reviewer.trim()) {
      setFormError("Enter your reviewer name before recording a verdict.");
      return;
    }
    setFormError(null);
    try {
      const note = notes[keyOf(item)]?.trim();
      const fb = await api.submitCuration({
        target_type: "classification_label",
        target_key: item.classification_id,
        label: item.label,
        decision,
        reviewer: reviewer.trim(),
        ...(note ? { note } : {}),
      });
      setMessage(
        fb.training_label_id !== null
          ? `Recorded "${decision}" for ${humanize(item.label)}; stored as an analyst label for future training.`
          : `Recorded "${decision}" for ${humanize(item.label)}; no training label was written.`,
      );
      queue.reload();
      stats.reload();
      history.reload();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Could not record the verdict.");
    }
  }

  return (
    <>
      <PageHeader
        title="Model Curation"
        lede="Review the least certain model predictions. Verdicts are stored with a snapshot of what you saw. Predictions are hypotheses until an analyst judges them; the ordering only prioritises effort."
      />
      <Panel title="Analyst">
        <label>
          Reviewer
          <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} maxLength={80} />
        </label>
        {formError ? <p className="field-error" role="alert">{formError}</p> : null}
        {message ? <p role="status">{message}</p> : null}
      </Panel>

      <Panel title="Curation statistics">
        {stats.status === "loading" ? <Loading /> : null}
        {stats.status === "error" ? <ErrorState error={stats.error} onRetry={stats.reload} /> : null}
        {stats.status === "ready" ? (
          <>
            <div className="stat-row">
              <Stat label="Verdicts" value={stats.data.total} />
              <Stat label="Accepted" value={stats.data.by_decision.accepted ?? 0} />
              <Stat label="Rejected" value={stats.data.by_decision.rejected ?? 0} />
              <Stat label="Needs evidence" value={stats.data.by_decision.needs_evidence ?? 0} />
            </div>
            <p className="hint">{stats.data.note}</p>
          </>
        ) : null}
      </Panel>

      <Panel title="Review queue">
        {queue.status === "loading" ? <Loading /> : null}
        {queue.status === "error" ? <ErrorState error={queue.error} onRetry={queue.reload} /> : null}
        {queue.status === "ready" && queue.data.items.length === 0 ? (
          <EmptyState title="Nothing to review">
            Classify wallets to produce model predictions that can be curated.
          </EmptyState>
        ) : null}
        {queue.status === "ready" ? (
          <ul className="list">
            {queue.data.items.map((i) => (
              <li key={keyOf(i)} aria-label={`${i.label} ${i.address}`}>
                <div>
                  <strong>{humanize(i.label)}</strong> <Pill tone="info">hypothesis</Pill>{" "}
                  {i.contradicted ? <Pill tone="warn">contradicted</Pill> : null}
                </div>
                <div className="hint">
                  {i.chain} · {i.address} · score {i.confidence.toFixed(2)} · {i.reason}
                </div>
                <div className="hint">{i.explanation}</div>
                <input
                  aria-label={`Note for ${i.label}`}
                  placeholder="Optional note"
                  maxLength={500}
                  value={notes[keyOf(i)] ?? ""}
                  onChange={(e) => setNotes({ ...notes, [keyOf(i)]: e.target.value })}
                />
                <button type="button" className="btn" onClick={() => void decide(i, "accepted")}>Accept</button>{" "}
                <button type="button" className="btn" onClick={() => void decide(i, "rejected")}>Reject</button>{" "}
                <button type="button" className="btn" onClick={() => void decide(i, "needs_evidence")}>
                  Needs evidence
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </Panel>

      <Panel title="Recent verdicts">
        {history.status === "ready" && history.data.items.length === 0 ? (
          <EmptyState title="No verdicts yet" />
        ) : null}
        {history.status === "ready" ? (
          <ul className="list">
            {history.data.items.map((f) => (
              <li key={f.id}>
                {humanize(f.label)}: <strong>{humanize(f.decision)}</strong> by {f.reviewer}
                {f.model_ref ? <span className="hint"> · {f.model_ref}</span> : null}
              </li>
            ))}
          </ul>
        ) : null}
      </Panel>
    </>
  );
}
