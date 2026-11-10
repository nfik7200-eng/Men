import type { EvidenceInput, LabelEvidence } from "../api/types";
import { safeHttpUrl } from "../lib/url";

export type EvidenceRow = { id: number; kind: LabelEvidence["kind"]; ref: string; description: string };

const PLACEHOLDER: Record<LabelEvidence["kind"], string> = {
  url: "https://… (a public page that supports the label)",
  transaction: "A transaction hash or signature stored in BIP",
  signal: "A signal number stored in BIP",
  note: "Free text (does not count towards approval)",
};

let nextRowId = 1;
export const newEvidenceRow = (): EvidenceRow => ({ id: nextRowId++, kind: "url", ref: "", description: "" });

/** Rows with a reference, as the API wants them. Blank rows are ignored. */
export function toEvidenceInput(rows: EvidenceRow[]): EvidenceInput[] {
  return rows
    .filter((r) => r.ref.trim() !== "")
    .map((r) => ({ kind: r.kind, ref: r.ref.trim(), ...(r.description.trim() ? { description: r.description.trim() } : {}) }));
}

/** The first problem with the evidence rows, or null. The server checks again. */
export function evidenceProblem(rows: EvidenceRow[]): string | null {
  const items = toEvidenceInput(rows);
  if (items.length > 10) return "At most 10 evidence items.";
  for (const e of items) {
    if (e.ref.length > 300) return "An evidence reference is longer than 300 characters.";
    if (e.kind === "url" && safeHttpUrl(e.ref) === null) return "A url evidence item must be an http or https address.";
    if (e.kind === "signal" && !/^\d+$/.test(e.ref)) return "A signal evidence item is a signal number.";
  }
  return null;
}

/** Add, edit and remove evidence items (max 10). Controlled by the parent. */
export function EvidenceEditor({ rows, onChange, legend = "Evidence" }: { rows: EvidenceRow[]; onChange: (rows: EvidenceRow[]) => void; legend?: string }) {
  const update = (id: number, patch: Partial<EvidenceRow>) => onChange(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  return (
    <fieldset className="evidence-editor">
      <legend>{legend}</legend>
      {rows.length === 0 ? <p className="muted">No evidence items added.</p> : null}
      {rows.map((r, i) => (
        <div className="evidence-row" key={r.id}>
          <label className="field">
            <span>Kind {i + 1}</span>
            <select value={r.kind} onChange={(e) => update(r.id, { kind: e.target.value as EvidenceRow["kind"] })}>
              <option value="url">url</option>
              <option value="transaction">transaction</option>
              <option value="signal">signal</option>
              <option value="note">note</option>
            </select>
          </label>
          <label className="field grow">
            <span>Reference {i + 1}</span>
            <input value={r.ref} maxLength={300} placeholder={PLACEHOLDER[r.kind]} onChange={(e) => update(r.id, { ref: e.target.value })} spellCheck={false} autoComplete="off" />
          </label>
          <label className="field grow">
            <span>What it shows {i + 1} (optional)</span>
            <input value={r.description} maxLength={300} onChange={(e) => update(r.id, { description: e.target.value })} />
          </label>
          <button type="button" className="btn" aria-label={`Remove evidence item ${i + 1}`} onClick={() => onChange(rows.filter((x) => x.id !== r.id))}>
            Remove
          </button>
        </div>
      ))}
      <button type="button" className="btn" disabled={rows.length >= 10} onClick={() => onChange([...rows, newEvidenceRow()])}>
        Add evidence
      </button>
    </fieldset>
  );
}
