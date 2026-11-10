import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "../../api/client";
import type { Signal, SignalDetail as SignalDetailData } from "../../api/types";
import { RoleHint } from "../../auth/RoleHint";
import { useCan } from "../../auth/useAuth";
import { DataTable } from "../../components/DataTable";
import {
  ExplorerRef,
  LabelEvidenceList,
  SeverityPill,
  SignalStatusPill,
  fmtConfidence,
  fmtScore,
  humanLabel,
  valueText,
} from "../../components/discovery";
import { Grade } from "../../components/Grade";
import { DataOriginBanner } from "../../components/intel";
import { EmptyState, ErrorState, FieldError, Loading, PageHeader, Panel, Pill } from "../../components/ui";
import { chainLabel } from "../../lib/address";
import { addressUrl } from "../../lib/explorer";
import { fmtInt, fmtTime } from "../../lib/format";
import { useApi } from "../../lib/useApi";

const message = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);

function KeyValues({ values, chain }: { values: Record<string, unknown>; chain: string }) {
  const entries = Object.entries(values);
  if (entries.length === 0) return <p className="muted">Nothing recorded.</p>;
  return (
    <dl className="kv">
      {entries.map(([k, v]) => (
        <Pair key={k} label={humanLabel(k)}>
          {typeof v === "string" && addressUrl(chain, v) !== null ? <ExplorerRef kind="address" chain={chain} id={v} /> : <span className="wrap-any">{valueText(v)}</span>}
        </Pair>
      ))}
    </dl>
  );
}

function Pair({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </>
  );
}

function PromotedTo({ value }: { value: string }) {
  const [kind, id] = value.split(":");
  if (kind === "investigation" && id && /^\d+$/.test(id)) return <Link to={`/investigations/${id}`}>Investigation {id}</Link>;
  if (kind === "watchlist" && id && /^\d+$/.test(id)) return <Link to="/watchlists">Watchlist {id}</Link>;
  return <span>{value}</span>;
}

function Attribution({ signal }: { signal: Signal }) {
  const groups = Object.entries(signal.attribution.labels);
  return (
    <Panel title="Attribution">
      {groups.length === 0 ? (
        <p>
          <strong>Unattributed: no reviewed label.</strong> No person has approved a label for this subject or the addresses in it. This does not mean the address is not an exchange, a protocol or anything else.
        </p>
      ) : (
        <div className="stack disc">
          {groups.map(([ref, labels]) => (
            <div key={ref} className="stack-tight">
              <p className="mono wrap-any">{ref}</p>
              {labels.map((l) => (
                <div key={l.label_id} className="label-card">
                  <div className="row-inline">
                    <strong>{l.name}</strong>
                    <Pill tone="info">{humanLabel(l.category)}</Pill>
                  </div>
                  <p className="muted">
                    Approved by {l.reviewed_by ?? "an unrecorded reviewer"}
                    {l.reviewed_at ? ` on ${fmtTime(l.reviewed_at)}` : ""}. Attributes an organisation or protocol, never a person.
                  </p>
                  <LabelEvidenceList items={l.evidence} chain={signal.chain} />
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
      <p className="note">{signal.attribution.note}</p>
    </Panel>
  );
}

function Actions({ signal, onChanged }: { signal: Signal; onChanged: () => void }) {
  const base = `/signals/${signal.id}`;
  const canDismiss = useCan("POST", `${base}/dismiss`);
  const canPromote = useCan("POST", `${base}/promote`);
  const watchlists = useApi(api.watchlists);
  const investigations = useApi(api.investigations);
  const [note, setNote] = useState("");
  const [target, setTarget] = useState<"watchlist" | "investigation">("watchlist");
  const [dest, setDest] = useState("");
  const [promoteNote, setPromoteNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const open = signal.status === "open";
  const promotable = signal.status === "open" || signal.status === "promoted";
  const asset = signal.subject.kind === "asset";
  const options = target === "watchlist" ? (watchlists.status === "ready" ? watchlists.data.map((w) => ({ id: w.id, label: `${w.name} (${w.entry_count} entries)` })) : []) : investigations.status === "ready" ? investigations.data.map((c) => ({ id: c.id, label: `${c.title} (${c.status.replace("_", " ")})` })) : [];
  const listState = target === "watchlist" ? watchlists : investigations;
  const destination = options.some((o) => String(o.id) === dest) ? dest : (options[0] ? String(options[0].id) : "");

  const run = async (what: string, action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      await action();
      setDone(what);
      onChanged();
    } catch (err) {
      setError(message(err, "The action failed."));
    } finally {
      setBusy(false);
    }
  };
  const dismiss = (e: FormEvent) => {
    e.preventDefault();
    if (!canDismiss.allowed || !open) return;
    void run("Signal dismissed.", () => api.dismissSignal(signal.id, note.trim() || null));
  };
  const promote = (e: FormEvent) => {
    e.preventDefault();
    if (!canPromote.allowed || !promotable || destination === "") return;
    const id = Number(destination);
    void run(
      target === "watchlist" ? "Subject added to the watchlist and signal marked promoted." : "Added to the investigation as an unverified finding.",
      () => api.promoteSignal(signal.id, target === "watchlist" ? { target, watchlist_id: id, note: promoteNote.trim() || null } : { target, investigation_id: id }),
    );
  };

  return (
    <Panel title="Review actions">
      {!open && !promotable ? <p className="muted">This signal is {signal.status}; it can no longer be dismissed or promoted.</p> : null}
      <div className="grid grid-2">
        <form className="form" onSubmit={dismiss} noValidate aria-label="Dismiss signal">
          <h3>Dismiss</h3>
          <p className="muted">Marks the signal as not useful. It stays dismissed, and the same subject stays quiet during the cooldown.</p>
          <label className="field">
            <span>Note (optional)</span>
            <textarea rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} disabled={!open} />
          </label>
          <RoleHint can={canDismiss} />
          <button type="submit" className="btn btn-danger" disabled={busy || !open || !canDismiss.allowed} title={canDismiss.reason}>
            Dismiss signal
          </button>
        </form>
        <form className="form" onSubmit={promote} noValidate aria-label="Promote signal">
          <h3>Promote</h3>
          <p className="muted">A promoted signal is a lead to investigate, not a confirmed finding.</p>
          <label className="field">
            <span>Promote to</span>
            <select value={target} onChange={(e) => { setTarget(e.target.value as typeof target); setDest(""); }} disabled={!promotable}>
              <option value="watchlist">Watchlist (watch the subject)</option>
              <option value="investigation">Investigation (unverified finding with evidence)</option>
            </select>
          </label>
          {target === "watchlist" && asset ? (
            <p className="notice">A native-asset signal has no address to watch. Promote it to an investigation instead.</p>
          ) : listState.status === "loading" ? (
            <Loading />
          ) : listState.status === "error" ? (
            <ErrorState error={listState.error} onRetry={listState.reload} />
          ) : options.length === 0 ? (
            <p className="notice">
              There is no {target} to promote into yet. Create one on the <Link to={target === "watchlist" ? "/watchlists" : "/investigations"}>{target === "watchlist" ? "Watchlists" : "Investigations"}</Link> page first.
            </p>
          ) : (
            <label className="field">
              <span>{target === "watchlist" ? "Watchlist" : "Investigation"}</span>
              <select value={destination} onChange={(e) => setDest(e.target.value)} disabled={!promotable}>
                {options.map((o) => (
                  <option key={o.id} value={o.id}>{o.label}</option>
                ))}
              </select>
            </label>
          )}
          {target === "watchlist" ? (
            <label className="field">
              <span>Note (optional)</span>
              <input value={promoteNote} maxLength={500} onChange={(e) => setPromoteNote(e.target.value)} disabled={!promotable} />
            </label>
          ) : null}
          <RoleHint can={canPromote} />
          <button
            type="submit"
            className="btn btn-primary"
            disabled={busy || !promotable || !canPromote.allowed || destination === "" || (target === "watchlist" && asset)}
            title={canPromote.reason}
          >
            Promote signal
          </button>
        </form>
      </div>
      <FieldError id="signal-action-error" message={error} />
      {done ? <p role="status" className="ok-text">{done}</p> : null}
    </Panel>
  );
}

function Body({ signal, origin, reload }: { signal: Signal; origin: SignalDetailData["data_origin"]; reload: () => void }) {
  const ex = signal.explanation;
  const components = Array.isArray(ex.score_components) ? ex.score_components : [];
  const cov = signal.coverage;
  const known = new Set(["detector", "version", "coverage", "limitations", "refresh", "cost", "rows_scanned", "row_cap_reached", "baseline_window_days", "lookback_hours", "evidence_finality", "provisional_evidence"]);
  const extra = Object.fromEntries(Object.entries(cov).filter(([k]) => !known.has(k)));
  const caveat = ex.caveat ?? ex.not_claimed;
  return (
    <>
      <DataOriginBanner origin={origin} />
      <div className="stack disc">
        <Panel title="Summary">
          <div className="row-inline">
            <SeverityPill severity={signal.severity} />
            <SignalStatusPill status={signal.status} />
            <Grade kind="statistical_signal" />
          </div>
          <dl className="kv">
            <Pair label="Score">{fmtScore(signal.score)}</Pair>
            <Pair label="Confidence">{fmtConfidence(signal.confidence)} (0 to 1, capped by the detector)</Pair>
            <Pair label="Chain">{chainLabel(signal.chain)}</Pair>
            <Pair label="Subject">
              {signal.subject.kind === "asset" ? "Native asset" : `${humanLabel(signal.subject.kind)} `}
              {signal.subject.kind !== "asset" ? <ExplorerRef kind="address" chain={signal.chain} id={signal.subject.ref} /> : null}
            </Pair>
            <Pair label="Family">{humanLabel(signal.family)}</Pair>
            {signal.status_note ? <Pair label="Status note">{signal.status_note}</Pair> : null}
            {signal.promoted_to ? (
              <Pair label="Promoted to">
                <PromotedTo value={signal.promoted_to} />
              </Pair>
            ) : null}
          </dl>
        </Panel>

        <Panel title="Explanation">
          <dl className="kv">
            <Pair label="Statement">{ex.statement ?? "No statement recorded."}</Pair>
            <Pair label="Method">{ex.method ?? "Not recorded."}</Pair>
            <Pair label="Caveat">{caveat ?? "Not recorded. Treat this as a candidate, not a fact."}</Pair>
          </dl>
          <h3>Observed</h3>
          <KeyValues values={ex.observed ?? {}} chain={signal.chain} />
          {ex.baseline ? (
            <>
              <h3>Baseline it was compared with</h3>
              <KeyValues values={ex.baseline} chain={signal.chain} />
            </>
          ) : null}
        </Panel>

        <Panel title="Score components" aside={<span className="muted">score = 100 x sum of weight x value</span>}>
          {components.length === 0 ? (
            <EmptyState title="No score components recorded" />
          ) : (
            <DataTable
              caption="Score components"
              rows={components}
              rowKey={(c) => c.name}
              columns={[
                { key: "name", header: "Component", priority: 1, render: (c) => humanLabel(c.name) },
                { key: "value", header: "Value (0 to 1)", priority: 2, numeric: true, render: (c) => c.value.toFixed(2) },
                { key: "weight", header: "Weight", priority: 2, numeric: true, render: (c) => c.weight.toFixed(2) },
                { key: "contribution", header: "Points", priority: 2, numeric: true, render: (c) => c.contribution.toFixed(1) },
                { key: "note", header: "Basis", priority: 2, render: (c) => c.note || <span className="muted">none recorded</span> },
              ]}
            />
          )}
          {ex.scoring ? <p className="note">{ex.scoring}</p> : null}
        </Panel>

        <Panel title="Evidence">
          {signal.evidence.length === 0 ? (
            <EmptyState title="No supporting records stored" />
          ) : (
            <ul className="list" aria-label="Supporting records">
              {signal.evidence.map((e) => (
                <li key={`${e.chain}:${e.ref}`}>
                  <span className="pill pill-idle">{e.kind}</span>
                  {e.kind === "transaction" ? <ExplorerRef kind="tx" chain={e.chain} id={e.ref} /> : <span className="mono wrap-any">{e.ref}</span>}
                </li>
              ))}
            </ul>
          )}
          <p className="note">Up to 50 supporting references are kept per signal. Explorer links open the public chain explorer in a new tab.</p>
        </Panel>

        <Panel title="Coverage and limitations">
          <dl className="kv">
            <Pair label="What it covers">{cov.coverage ?? "Not recorded."}</Pair>
            <Pair label="Limitations">{cov.limitations ?? "Not recorded."}</Pair>
            {cov.refresh ? <Pair label="Refresh">{cov.refresh}</Pair> : null}
            {cov.cost ? <Pair label="Cost">{cov.cost}</Pair> : null}
            {cov.rows_scanned !== undefined ? <Pair label="Rows scanned">{fmtInt(cov.rows_scanned)}{cov.row_cap_reached ? " (row cap reached: older rows were not examined)" : ""}</Pair> : null}
            {cov.baseline_window_days !== undefined ? <Pair label="Baseline window">{cov.baseline_window_days} days</Pair> : null}
            {cov.lookback_hours !== undefined ? <Pair label="Lookback">{cov.lookback_hours} hours</Pair> : null}
            {cov.evidence_finality ? (
              <Pair label="Evidence finality">
                {cov.evidence_finality.finalized} of {cov.evidence_finality.total} supporting transactions finalized
              </Pair>
            ) : null}
          </dl>
          {cov.provisional_evidence ? <p className="notice">Some supporting evidence is not yet finalized and could still be reorganised. Score and confidence are lowered for it.</p> : null}
          {Object.keys(extra).length ? <KeyValues values={extra} chain={signal.chain} /> : null}
        </Panel>

        <Attribution signal={signal} />

        <Panel title="Provenance">
          <dl className="kv">
            <Pair label="Detector">{signal.detector.id} version {signal.detector.version}</Pair>
            <Pair label="Signal key"><span className="mono wrap-any">{signal.key}</span></Pair>
            <Pair label="Data origin">{origin === "offline_fixture" ? "Offline fixture (demo data)" : "Stored observations"}</Pair>
            <Pair label="Observed">{fmtTime(signal.observed_at)}</Pair>
            <Pair label="First seen">{fmtTime(signal.first_seen_at)}</Pair>
            <Pair label="Last seen">{fmtTime(signal.last_seen_at)}</Pair>
            <Pair label="Occurrences">{fmtInt(signal.occurrences)}</Pair>
            <Pair label="Review window ends">{fmtTime(signal.expires_at)}</Pair>
          </dl>
        </Panel>

        <Actions key={signal.id} signal={signal} onChanged={reload} />
      </div>
    </>
  );
}

export default function SignalDetailPage() {
  const { id = "" } = useParams();
  const numeric = /^\d+$/.test(id) ? Number(id) : null;
  const detail = useApi(() => (numeric === null ? Promise.reject(new ApiError(404, "not_found", "That is not a signal number.")) : api.signal(numeric)), [id]);
  const title = detail.status === "ready" ? detail.data.title : "Signal";
  return (
    <>
      <PageHeader title={title} lede="A candidate for review, not a finding." actions={<Link className="btn" to="/signals">All signals</Link>} docTitle={`Signal ${id}`} />
      {detail.status === "loading" ? <Loading /> : null}
      {detail.status === "error" ? <ErrorState error={detail.error} onRetry={detail.reload} /> : null}
      {detail.status === "ready" ? <Body signal={detail.data} origin={detail.data.data_origin} reload={detail.reload} /> : null}
    </>
  );
}
