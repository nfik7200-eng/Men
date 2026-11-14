// Small pieces shared by the discovery pages (signals, alerts, coverage, attribution).
import { Link } from "react-router-dom";
import type { EvidenceClass, GroundTruth, LabelEvidence, ReviewState, Severity, SignalStatus } from "../api/types";
import { addressUrl, txUrl } from "../lib/explorer";
import { ExternalLink } from "./ExternalLink";
import { Pill, type Tone } from "./ui";

const SEVERITY_TONE: Record<Severity, Tone> = { high: "bad", medium: "warn", low: "info" };

export function SeverityPill({ severity }: { severity: Severity }) {
  return <Pill tone={SEVERITY_TONE[severity] ?? "idle"}>{severity} severity</Pill>;
}

const STATUS_TONE: Record<SignalStatus, Tone> = { open: "info", promoted: "ok", dismissed: "idle", invalidated: "bad", expired: "idle" };

export function SignalStatusPill({ status }: { status: SignalStatus }) {
  return <Pill tone={STATUS_TONE[status] ?? "idle"}>{status}</Pill>;
}

/** A score is 0 to 100, a confidence 0 to 1; both are shown with their scale so neither reads as a probability. */
export const fmtScore = (n: number): string => `${n.toFixed(1)} / 100`;
export const fmtConfidence = (n: number): string => n.toFixed(2);

/** Any JSON value as text. Objects are stringified; nothing is ever rendered as HTML. */
export function valueText(value: unknown): string {
  if (value === null || value === undefined) return "not recorded";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return "unreadable value";
  }
}

/** A transaction or address on its explorer when the identifier is well formed, otherwise plain text. */
export function ExplorerRef({ kind, chain, id }: { kind: "tx" | "address"; chain: string; id: string }) {
  const href = kind === "tx" ? txUrl(chain, id) : addressUrl(chain, id);
  if (href === null) return <span className="mono wrap-any">{id}</span>;
  return (
    <a href={href} rel="noopener noreferrer" target="_blank" className="mono wrap-any">
      {id}
    </a>
  );
}

export const humanLabel = (s: string): string => s.replace(/_/g, " ");

/**
 * Evidence attached to a label or review. A url is a link only when it is http(s); a transaction
 * goes to its explorer when well formed; a stored signal goes to its page; a note is plain text.
 */
export function LabelEvidenceList({ items, chain }: { items: LabelEvidence[]; chain: string }) {
  if (items.length === 0) return <p className="muted">No evidence attached.</p>;
  return (
    <ul className="list evidence-list" aria-label="Evidence">
      {items.map((e, i) => (
        <li key={`${e.kind}:${e.ref}:${i}`}>
          <span className="pill pill-idle">{e.kind}</span>
          {e.kind === "url" ? (
            <ExternalLink url={e.ref} />
          ) : e.kind === "transaction" ? (
            <ExplorerRef kind="tx" chain={chain} id={e.ref} />
          ) : e.kind === "signal" && /^\d+$/.test(e.ref) ? (
            <Link to={`/signals/${e.ref}`}>Signal {e.ref}</Link>
          ) : (
            <span className="wrap-any">{e.ref}</span>
          )}
          {e.description ? <span className="muted wrap-any">{e.description}</span> : null}
          {e.added_by ? <span className="muted">added by {e.added_by}</span> : null}
          {e.verified_stored === false && (e.kind === "transaction" || e.kind === "signal") ? <span className="muted">not found in BIP</span> : null}
        </li>
      ))}
    </ul>
  );
}

const REVIEW_TONE: Record<ReviewState, Tone> = {
  new: "idle",
  pending_review: "warn",
  investigating: "info",
  monitoring: "info",
  confirmed: "ok",
  rejected: "idle",
  duplicate: "idle",
  insufficient_evidence: "warn",
  archived: "idle",
};
export const REVIEW_STATES: ReviewState[] = [
  "new", "pending_review", "investigating", "monitoring", "confirmed", "rejected", "duplicate", "insufficient_evidence", "archived",
];

/** Analyst review state of a candidate. "confirmed" means a person judged it a real finding of its kind, not that it is a verified fact. */
export function ReviewStatePill({ state }: { state: ReviewState }) {
  return <Pill tone={REVIEW_TONE[state] ?? "idle"}>{`review: ${humanLabel(state)}`}</Pill>;
}

const CLASS_TONE: Record<EvidenceClass, Tone> = { observed: "ok", reviewed: "ok", statistical: "warn", heuristic: "warn", hypothesis: "warn" };
const CLASS_TEXT: Record<EvidenceClass, string> = {
  observed: "Observed",
  reviewed: "Reviewed by a person",
  statistical: "Statistical signal (not verified)",
  heuristic: "Heuristic (not verified)",
  hypothesis: "Hypothesis (not verified)",
};

/** Evidence class of a remembered fact. Only observed and reviewed facts are not flagged as unverified. */
export function EvidenceClassBadge({ value }: { value: EvidenceClass }) {
  return <Pill tone={CLASS_TONE[value] ?? "idle"}>{CLASS_TEXT[value] ?? humanLabel(String(value))}</Pill>;
}

const TRUTH_TEXT: Record<GroundTruth["quality"], string> = {
  unlabelled: "Not a label yet",
  human_review: "Label from one reviewer",
  human_consensus: "Label: reviewers agree",
  dispute_resolved: "Label: dispute resolved",
  disputed: "Disputed: reviewers disagree",
};

/** Whether this candidate's review counts as a training label, and why not when it does not. */
export function GroundTruthBadge({ truth }: { truth: GroundTruth }) {
  const tone: Tone = truth.eligible ? "ok" : truth.quality === "disputed" ? "warn" : "idle";
  const outcome = truth.label === 1 ? " (confirmed)" : truth.label === 0 ? " (rejected)" : "";
  return (
    <span>
      <Pill tone={tone}>{`${TRUTH_TEXT[truth.quality] ?? humanLabel(truth.quality)}${truth.eligible ? outcome : ""}`}</Pill>
      {truth.excluded_because ? <span className="muted"> Not used for training: {truth.excluded_because}.</span> : null}
    </span>
  );
}
