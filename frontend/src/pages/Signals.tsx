import { useState, type ReactNode } from "react";
import { RoleHint } from "../auth/RoleHint";
import { useCan } from "../auth/useAuth";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { DiscoveryFeed, Freshness, ReviewState, Severity, Signal, SignalPage, SignalQuery, SignalStatus } from "../api/types";
import { Address } from "../components/Address";
import { DataTable } from "../components/DataTable";
import { REVIEW_STATES, ReviewStatePill, SeverityPill, SignalStatusPill, fmtConfidence, fmtScore, humanLabel } from "../components/discovery";
import { DataOriginBanner } from "../components/intel";
import { EmptyState, ErrorState, Loading, PageHeader, Panel } from "../components/ui";
import { CHAINS, chainLabel } from "../lib/address";
import { fmtInt, fmtTime } from "../lib/format";
import { useApi } from "../lib/useApi";

const PAGE_SIZE = 25;
const STATUSES: SignalStatus[] = ["open", "dismissed", "promoted", "invalidated", "expired"];
const SEVERITIES: Severity[] = ["high", "medium", "low"];

type Filters = {
  status: SignalStatus;
  review: "" | ReviewState;
  severity: "" | Severity;
  chain: string;
  family: string;
  detector: string;
  minScore: string;
  sort: "score" | "recent";
};
const DEFAULTS: Filters = { status: "open", review: "", severity: "", chain: "", family: "", detector: "", minScore: "", sort: "score" };

const isDefault = (f: Filters) => JSON.stringify(f) === JSON.stringify(DEFAULTS);

function toQuery(f: Filters, offset: number): SignalQuery {
  const q: SignalQuery = { status: f.status, sort: f.sort, limit: PAGE_SIZE, offset };
  if (f.review) q.review_state = f.review;
  if (f.severity) q.severity = f.severity;
  if (f.chain) q.chain = f.chain;
  if (f.family) q.family = f.family;
  if (f.detector) q.detector = f.detector;
  const min = Number(f.minScore);
  if (f.minScore.trim() !== "" && Number.isFinite(min)) q.min_score = Math.min(100, Math.max(0, min));
  return q;
}

type Loaded = { page: SignalPage; freshness: Freshness | null; scope: string | null };

/** The default view is the ranked feed itself (it carries the freshness block); filters use /signals. */
async function load(f: Filters, offset: number): Promise<Loaded> {
  if (isDefault(f)) {
    const feed: DiscoveryFeed = await api.discoveryFeed(PAGE_SIZE, offset);
    return { page: feed, freshness: feed.freshness, scope: feed.scope };
  }
  const [page, feed] = await Promise.all([api.signals(toQuery(f, offset)), api.discoveryFeed(1, 0).catch(() => null)]);
  return { page, freshness: feed?.freshness ?? null, scope: feed?.scope ?? null };
}

function ageText(seconds: number | null): string {
  if (seconds === null) return "never";
  if (seconds < 90) return "just now";
  if (seconds < 5400) return `${Math.round(seconds / 60)} minutes ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} hours ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

export function FreshnessPanel({ freshness, scope }: { freshness: Freshness | null; scope: string | null }) {
  if (freshness === null) return null;
  return (
    <Panel title="How current is this feed">
      <dl className="kv" aria-label="Discovery freshness">
        <dt>Last discovery pass</dt>
        <dd>
          {freshness.last_run_at ? `${fmtTime(freshness.last_run_at)} (${ageText(freshness.age_seconds)})` : "No pass has run yet"}
        </dd>
        <dt>Pass result</dt>
        <dd>{freshness.last_run_status ?? "not recorded"}</dd>
        {freshness.last_historical_run_at ? (
          <>
            <dt>Last historical pass</dt>
            <dd>
              {fmtTime(freshness.last_historical_run_at)}
              {freshness.last_historical_as_of ? `, evaluated as of ${fmtTime(freshness.last_historical_as_of)}` : ""} (does not count as current)
            </dd>
          </>
        ) : null}
        {freshness.last_expansion_run_at ? (
          <>
            <dt>Last expansion run</dt>
            <dd>{fmtTime(freshness.last_expansion_run_at)} (does not count as current)</dd>
          </>
        ) : null}
      </dl>
      {freshness.stale ? (
        <p className="notice" role="status">
          {freshness.last_run_at
            ? "The last discovery pass is more than 24 hours old, so newer stored activity has not been examined."
            : "Discovery has not run on this database, so an empty feed means nothing was examined, not that nothing happened."}
        </p>
      ) : null}
      {scope ? <p className="note">{scope}</p> : null}
    </Panel>
  );
}

function Filter({ label, value, onChange, children }: { label: string; value: string; onChange: (v: string) => void; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {children}
      </select>
    </label>
  );
}

export function SubjectCell({ signal }: { signal: Pick<Signal, "chain" | "subject"> }) {
  if (signal.subject.kind === "asset") return <span>{signal.chain === "ethereum" ? "Native asset (ETH)" : "Native asset"}</span>;
  return (
    <span>
      <span className="muted">{humanLabel(signal.subject.kind)} </span>
      <Address value={signal.subject.ref} chain={signal.chain} />
    </span>
  );
}

function RunDiscovery({ onDone }: { onDone: () => void }) {
  const can = useCan("POST", "/discovery/run");
  const [state, setState] = useState<{ busy: boolean; result?: string; error?: string }>({ busy: false });
  const run = async () => {
    setState({ busy: true });
    try {
      const r = await api.runDiscovery();
      const sum = (key: "created" | "merged") => Object.values(r.detectors).reduce((n, d) => n + d[key], 0);
      setState({
        busy: false,
        result: `Pass ${r.status}: ${sum("created")} new, ${sum("merged")} merged, ${r.expired} expired, ${r.alerts_created} alert(s).`,
      });
      onDone();
    } catch (err) {
      setState({ busy: false, error: err instanceof ApiError ? err.message : "The discovery pass could not run." });
    }
  };
  return (
    <Panel title="Run discovery">
      <p className="note">
        Runs every detector once over data BIP already stored. It reads the database only: no provider is contacted and
        nothing is collected.
      </p>
      <div className="row">
        <button type="button" className="btn" disabled={!can.allowed || state.busy} title={can.reason} onClick={() => void run()}>
          {state.busy ? "Running…" : "Run discovery now"}
        </button>
        <RoleHint can={can} />
      </div>
      <div role="status" aria-live="polite">
        {state.result ? <p>{state.result}</p> : null}
        {state.error ? <p className="field-error">{state.error}</p> : null}
      </div>
    </Panel>
  );
}

export default function Signals() {
  const [filters, setFilters] = useState<Filters>(DEFAULTS);
  const [offset, setOffset] = useState(0);
  const catalogue = useApi(api.detectors);
  const key = JSON.stringify([filters, offset]);
  const data = useApi(() => load(filters, offset), [key]);

  const change = (patch: Partial<Filters>) => {
    setFilters((f) => ({ ...f, ...patch }));
    setOffset(0);
  };
  const detectors = catalogue.status === "ready" ? catalogue.data.detectors : [];
  const families = [...new Set(detectors.map((d) => d.family))].sort();
  const filtered = !isDefault(filters);

  return (
    <>
      <PageHeader
        title="Signals"
        lede="Candidates ranked by score. A signal is a statistical or observational lead over data BIP already stored. It is not a finding and not attribution."
      />
      {data.status === "ready" ? <DataOriginBanner origin={data.data.page.data_origin} /> : null}
      <div className="stack disc">
        {data.status === "ready" ? <FreshnessPanel freshness={data.data.freshness} scope={data.data.scope} /> : null}
        <RunDiscovery onDone={data.reload} />
        <Panel title="Filters">
          <form className="row filters" onSubmit={(e) => e.preventDefault()} aria-label="Signal filters">
            <Filter label="Status" value={filters.status} onChange={(v) => change({ status: v as SignalStatus })}>
              {STATUSES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </Filter>
            <Filter label="Review state" value={filters.review} onChange={(v) => change({ review: v as Filters["review"] })}>
              <option value="">All review states</option>
              {REVIEW_STATES.map((s) => (
                <option key={s} value={s}>{humanLabel(s)}</option>
              ))}
            </Filter>
            <Filter label="Severity" value={filters.severity} onChange={(v) => change({ severity: v as Filters["severity"] })}>
              <option value="">All severities</option>
              {SEVERITIES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </Filter>
            <Filter label="Chain" value={filters.chain} onChange={(v) => change({ chain: v })}>
              <option value="">All chains</option>
              {CHAINS.map((c) => (
                <option key={c.id} value={c.id}>{c.label}</option>
              ))}
            </Filter>
            <Filter label="Family" value={filters.family} onChange={(v) => change({ family: v })}>
              <option value="">All families</option>
              {families.map((f) => (
                <option key={f} value={f}>{humanLabel(f)}</option>
              ))}
            </Filter>
            <Filter label="Detector" value={filters.detector} onChange={(v) => change({ detector: v })}>
              <option value="">All detectors</option>
              {detectors.map((d) => (
                <option key={d.id} value={d.id}>{d.title}</option>
              ))}
            </Filter>
            <label className="field">
              <span>Minimum score</span>
              <input type="number" inputMode="decimal" min={0} max={100} step={1} value={filters.minScore} onChange={(e) => change({ minScore: e.target.value })} placeholder="0 to 100" />
            </label>
            <Filter label="Order" value={filters.sort} onChange={(v) => change({ sort: v as Filters["sort"] })}>
              <option value="score">Highest score</option>
              <option value="recent">Most recently seen</option>
            </Filter>
            {filtered ? (
              <button type="button" className="btn" onClick={() => { setFilters(DEFAULTS); setOffset(0); }}>
                Clear filters
              </button>
            ) : null}
          </form>
          {catalogue.status === "error" ? (
            <p className="note">The detector list could not be loaded, so the family and detector filters are empty. Reload to retry.</p>
          ) : null}
        </Panel>
        <Panel title={filtered ? "Matching signals" : "Open signals, ranked"}>
          {data.status === "loading" ? <Loading /> : null}
          {data.status === "error" ? <ErrorState error={data.error} onRetry={data.reload} /> : null}
          {data.status === "ready" ? <Results loaded={data.data} filtered={filtered} offset={offset} onPage={setOffset} /> : null}
        </Panel>
      </div>
    </>
  );
}

function Results({ loaded, filtered, offset, onPage }: { loaded: Loaded; filtered: boolean; offset: number; onPage: (o: number) => void }) {
  const { page, freshness } = loaded;
  if (page.total === 0) {
    return filtered ? (
      <EmptyState title="No signal matches these filters">Clear a filter to widen the search. Other statuses and chains may have signals.</EmptyState>
    ) : (
      <EmptyState title="No open signals">
        {freshness?.last_run_at
          ? "The last discovery pass left no open candidate. That is what the stored data showed, within the coverage of each detector."
          : "Signals appear after a discovery pass over stored data. None has run yet, so nothing has been examined."}{" "}
        See <Link to="/coverage">Coverage</Link> for what each detector can and cannot see.
      </EmptyState>
    );
  }
  const first = offset + 1;
  const last = offset + page.items.length;
  return (
    <>
      <DataTable
        caption="Signals"
        rows={page.items}
        rowKey={(s) => String(s.id)}
        columns={[
          { key: "title", header: "Signal", priority: 1, render: (s) => <Link to={`/signals/${s.id}`}>{s.title}</Link> },
          { key: "sev", header: "Severity", priority: 2, render: (s) => <SeverityPill severity={s.severity} /> },
          { key: "score", header: "Score", priority: 2, numeric: true, render: (s) => fmtScore(s.score) },
          { key: "conf", header: "Confidence", priority: 3, numeric: true, render: (s) => fmtConfidence(s.confidence) },
          { key: "chain", header: "Chain", priority: 2, render: (s) => chainLabel(s.chain) },
          { key: "subject", header: "Subject", priority: 2, render: (s) => <SubjectCell signal={s} /> },
          { key: "det", header: "Detector", priority: 3, render: (s) => `${s.detector.id} v${s.detector.version}` },
          { key: "status", header: "Status", priority: 2, render: (s) => <SignalStatusPill status={s.status} /> },
          { key: "review", header: "Review", priority: 2, render: (s) => <ReviewStatePill state={s.review_state} /> },
          { key: "seen", header: "Last seen", priority: 3, render: (s) => fmtTime(s.last_seen_at) },
          { key: "n", header: "Occurrences", priority: 3, numeric: true, render: (s) => fmtInt(s.occurrences) },
        ]}
      />
      <div className="row pager">
        <button type="button" className="btn" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - PAGE_SIZE))}>
          Previous
        </button>
        <span className="muted" role="status">
          {first} to {last} of {fmtInt(page.total)}
        </span>
        <button type="button" className="btn" disabled={last >= page.total} onClick={() => onPage(offset + PAGE_SIZE)}>
          Next
        </button>
      </div>
    </>
  );
}
