import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { EvidenceClass, ExplorerGraph, ExplorerTimeline, GraphNode } from "../api/types";
import { DataOriginBanner, VerificationLine, WalletPicker } from "../components/intel";
import { EmptyState, ErrorState, Loading, Mono, PageHeader, Panel, Pill } from "../components/ui";
import { fmtTime, humanize, shortId } from "../lib/format";
import { useApi } from "../lib/useApi";
import { GraphCanvas } from "./Graph";

const CLASSES: { id: EvidenceClass; label: string }[] = [
  { id: "verified", label: "Verified" },
  { id: "single_source", label: "Single-source" },
  { id: "hypothesis", label: "Hypothesis" },
];

type Filters = { since: string | null; until: string | null; statuses: EvidenceClass[] };

/** Slider positions are bucket boundaries 0..n of the (unfiltered) timeline. */
function windowOf(tl: ExplorerTimeline, lo: number, hi: number): { since: string | null; until: string | null } {
  const n = tl.buckets.length;
  if (n === 0 || (lo === 0 && hi === n)) return { since: null, until: null };
  return { since: lo === 0 ? null : (tl.buckets[lo]?.start ?? null), until: hi === n ? null : (tl.buckets[hi - 1]?.end ?? null) };
}

function TimeSlider({ tl, lo, hi, onChange }: { tl: ExplorerTimeline; lo: number; hi: number; onChange: (lo: number, hi: number) => void }) {
  const n = tl.buckets.length;
  if (n === 0) return <p className="muted">No dated observations to slice by time.</p>;
  const max = Math.max(1, ...tl.buckets.map((b) => b.count));
  const w = windowOf(tl, lo, hi);
  return (
    <div className="time-slider" role="group" aria-label="Time window">
      <div className="spark" aria-hidden="true">
        {tl.buckets.map((b, i) => (
          <i key={b.start} className={i >= lo && i < hi ? "on" : ""} style={{ height: `${Math.max(6, (b.count / max) * 100)}%` }} />
        ))}
      </div>
      <label className="field">
        <span>From</span>
        <input type="range" min={0} max={n - 1} value={lo} aria-label="Window start" onChange={(e) => onChange(Math.min(Number(e.target.value), hi - 1), hi)} />
      </label>
      <label className="field">
        <span>To</span>
        <input type="range" min={1} max={n} value={hi} aria-label="Window end" onChange={(e) => onChange(lo, Math.max(Number(e.target.value), lo + 1))} />
      </label>
      <p className="note">
        Window: {fmtTime(w.since ?? tl.start)} to {fmtTime(w.until ?? tl.end)}
        {tl.undated ? ` · ${tl.undated} observations have no block time and are excluded whenever a window is set` : ""}
      </p>
    </div>
  );
}

function NodeTrailPanel({ chain, address, node, depth, filters }: { chain: string; address: string; node: GraphNode; depth: number; filters: Filters }) {
  const t = useApi(() => api.explorerNode(chain, address, node.id, depth, filters), [chain, address, node.id, depth, filters.since, filters.until, filters.statuses.join(",")]);
  if (t.status === "loading") return <Loading label="Loading evidence trail…" />;
  if (t.status === "error") return <ErrorState error={t.error} onRetry={t.reload} />;
  const d = t.data;
  return (
    <section aria-label="Evidence trail">
      <h4>Evidence trail</h4>
      <dl className="kv">
        <dt>Transactions</dt><dd>{d.transaction_count}</dd>
        <dt>Block range</dt><dd className="mono">{d.block_range ? `${d.block_range.min} to ${d.block_range.max}` : "—"}</dd>
        <dt>Providers</dt><dd>{Object.entries(d.providers).map(([p, c]) => `${p} (${c})`).join(", ") || "—"}</dd>
      </dl>
      {d.incomplete_edges.length ? <p className="note"><Pill tone="warn">partial</Pill> Some incident edges hold more observations than were retained; the trail lists retained ones only.</p> : null}
      <ul className="metric-list">
        {d.transactions.map((x) => (
          <li key={x.tx_id}>
            <Mono title={x.tx_id}>{x.tx_id}</Mono>{" "}
            <span className="muted">block {x.position ?? "?"} · {fmtTime(x.block_time)}</span>{" "}
            <Pill tone={x.evidence_class === "verified" ? "ok" : x.evidence_class === "hypothesis" ? "warn" : "info"}>{humanize(x.evidence_class)}</Pill>
            {x.provider_response ? (
              <div className="muted">
                response: {x.provider_response.provider}
                {x.provider_response.method ? ` ${x.provider_response.method}` : ""}
                {x.provider_response.request_id ? ` · req ${x.provider_response.request_id}` : ""}
                {x.provider_response.raw_ref ? ` · raw ${x.provider_response.raw_ref}${x.provider_response.raw_row !== null ? `#${x.provider_response.raw_row}` : ""}` : " · raw payload not referenced"}
                {x.provider_response.content_hash ? ` · sha256 ${shortId(x.provider_response.content_hash, 8)}` : ""}
              </div>
            ) : null}
          </li>
        ))}
      </ul>
      <p className="note">{d.statement}</p>
    </section>
  );
}

function ExplorerView({ chain, address, depth }: { chain: string; address: string; depth: number }) {
  const [statuses, setStatuses] = useState<EvidenceClass[]>(CLASSES.map((c) => c.id));
  const [range, setRange] = useState<[number, number] | null>(null);
  const [tl, setTl] = useState<ExplorerTimeline | null>(null);
  const n = tl?.buckets.length ?? 0;
  const [lo, hi] = range ?? [0, n];
  const filters: Filters = { ...(tl ? windowOf(tl, lo, hi) : { since: null, until: null }), statuses };
  // An empty status set would mean "all" to the API, so it is sent as every class and shown as none checked.
  const sent = { ...filters, statuses: statuses.length ? statuses : CLASSES.map((c) => c.id) };
  const g = useApi(() => api.explorerGraph(chain, address, depth, sent), [chain, address, depth, sent.since, sent.until, sent.statuses.join(",")]);
  // The timeline is unfiltered, so the first response fixes the slider range for this wallet and depth.
  if (g.status === "ready" && tl === null) setTl(g.data.timeline);
  const toggle = (id: EvidenceClass) => setStatuses((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  return (
    <>
      <Panel title="Filters">
        <fieldset className="status-filter">
          <legend>Evidence status</legend>
          {CLASSES.map((c) => (
            <label key={c.id} className="check">
              <input type="checkbox" checked={statuses.includes(c.id)} onChange={() => toggle(c.id)} /> {c.label}
            </label>
          ))}
        </fieldset>
        {tl ? <TimeSlider tl={tl} lo={lo} hi={hi} onChange={(a, b) => setRange([a, b])} /> : null}
      </Panel>
      {g.status === "loading" ? <Loading label="Building temporal graph…" /> : null}
      {g.status === "error" ? <ErrorState error={g.error} onRetry={g.reload} /> : null}
      {g.status === "ready" ? <ExplorerResult g={g.data} chain={chain} address={address} depth={depth} filters={sent} /> : null}
    </>
  );
}

function ExplorerResult({ g, chain, address, depth, filters }: { g: ExplorerGraph; chain: string; address: string; depth: number; filters: Filters }) {
  const f = g.filtered_out;
  return (
    <>
      <DataOriginBanner origin={g.data_origin} />
      <Panel title={`${g.nodes.length} nodes · ${g.edges.length} edges`}>
        <p className="note">{g.statement}</p>
        <p><VerificationLine v={g.verification} /></p>
        <p className="note">
          {f.total} observations filtered out ({f.by_status} by status, {f.outside_time_window} outside the window, {f.undated} undated);
          {" "}{f.nodes_hidden} nodes and {f.edges_hidden} edges hidden.
        </p>
        <ul className="legend-list">
          {CLASSES.map((c) => <li key={c.id}><strong>{c.label}:</strong> {g.statuses_legend[c.id]}</li>)}
        </ul>
        {g.edges.length === 0 ? (
          <EmptyState title="No observations match these filters">Widen the time window or include more evidence classes. Nothing is drawn without evidence.</EmptyState>
        ) : (
          <GraphCanvas
            key={`${chain}:${address}:${depth}:${filters.since}:${filters.until}:${filters.statuses.join(",")}`}
            graph={g}
            nodeExtra={(node) => <NodeTrailPanel chain={chain} address={address} node={node} depth={depth} filters={filters} />}
          />
        )}
      </Panel>
    </>
  );
}

export default function GraphExplorerPage() {
  const [params, setParams] = useSearchParams();
  const chain = params.get("chain") ?? "";
  const address = params.get("address") ?? "";
  const depth = Math.min(3, Math.max(1, Number(params.get("depth") ?? "1") || 1));
  return (
    <>
      <PageHeader
        title="Temporal Explorer"
        lede="Slice a wallet's relationship graph by time and evidence status, then inspect the exact transactions, block range and provider responses behind any node."
      />
      <Panel>
        <WalletPicker key={`${chain}:${address}`} initialChain={chain || "ethereum"} initialAddress={address} submitLabel="Explore"
          onPick={(c, a) => setParams({ chain: c, address: a, depth: String(depth) })} />
        {chain && address ? (
          <label className="field depth-field">
            <span>Depth</span>
            <select value={depth} onChange={(e) => setParams({ chain, address, depth: e.target.value })}>
              {[1, 2, 3].map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
        ) : null}
      </Panel>
      {chain && address ? <ExplorerView key={`${chain}:${address}:${depth}`} chain={chain} address={address} depth={depth} /> : (
        <EmptyState title="Choose a wallet">Enter a wallet whose activity BIP has retrieved.</EmptyState>
      )}
    </>
  );
}
