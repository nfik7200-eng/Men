import { useMemo, useRef, useState, type PointerEvent, type WheelEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Graph, GraphEdge, GraphNode } from "../api/types";
import { DataOriginBanner, Freshness, VerificationLine, VerificationPill, WalletPicker } from "../components/intel";
import { EmptyState, ErrorState, Loading, Mono, PageHeader, Panel, Pill } from "../components/ui";
import { fmtTime, humanize, shortId } from "../lib/format";
import { useApi } from "../lib/useApi";

// Three validated categorical slots (dark palette); shape is the secondary encoding.
const KIND_COLOR: Record<GraphNode["kind"], string> = {
  address: "#3987e5",
  contract: "#3987e5",
  token: "#d95926",
  program: "#199e70",
};
const KIND_SHAPE: Record<GraphNode["kind"], string> = {
  address: "circle", contract: "ringed circle", token: "diamond", program: "square",
};

type Pos = { x: number; y: number };

/** Deterministic radial layout: one ring per traversal depth, nodes sorted by kind then id. */
export function layout(nodes: GraphNode[]): Map<string, Pos> {
  const byDepth = new Map<number, GraphNode[]>();
  for (const n of nodes) byDepth.set(n.depth, [...(byDepth.get(n.depth) ?? []), n]);
  const pos = new Map<string, Pos>();
  for (const [depth, ring] of byDepth) {
    ring.sort((a, b) => (a.kind + a.id).localeCompare(b.kind + b.id));
    const r = depth * 170;
    ring.forEach((n, i) => {
      const angle = (2 * Math.PI * i) / ring.length - Math.PI / 2 + depth * 0.3;
      pos.set(n.id, depth === 0 && ring.length === 1 ? { x: 0, y: 0 } : { x: r * Math.cos(angle), y: r * Math.sin(angle) });
    });
  }
  return pos;
}

function NodeMark({ n, p, selected }: { n: GraphNode; p: Pos; selected: boolean }) {
  const s = n.center ? 11 : 8;
  const fill = KIND_COLOR[n.kind];
  const ring = selected ? "var(--text)" : "var(--surface)";
  if (n.kind === "token")
    return <rect x={p.x - s} y={p.y - s} width={s * 2} height={s * 2} transform={`rotate(45 ${p.x} ${p.y})`} fill={fill} stroke={ring} strokeWidth={2} />;
  if (n.kind === "program")
    return <rect x={p.x - s} y={p.y - s} width={s * 2} height={s * 2} rx={2} fill={fill} stroke={ring} strokeWidth={2} />;
  return (
    <>
      {n.kind === "contract" ? <circle cx={p.x} cy={p.y} r={s + 4} fill="none" stroke={fill} strokeWidth={2} /> : null}
      <circle cx={p.x} cy={p.y} r={s} fill={fill} stroke={ring} strokeWidth={2} />
    </>
  );
}

export function GraphCanvas({ graph }: { graph: Graph }) {
  const pos = useMemo(() => layout(graph.nodes), [graph.nodes]);
  // Parallel edges between the same pair are bent apart so each one stays visible and clickable.
  const bend = useMemo(() => {
    const groups = new Map<string, string[]>();
    for (const e of graph.edges) {
      const k = [e.source, e.target].sort().join("|");
      groups.set(k, [...(groups.get(k) ?? []), e.id]);
    }
    const out = new Map<string, number>();
    for (const ids of groups.values()) ids.forEach((id, i) => out.set(id, (i - (ids.length - 1) / 2) * 28));
    return out;
  }, [graph.edges]);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const [sel, setSel] = useState<{ type: "node"; id: string } | { type: "edge"; id: string } | null>({ type: "node", id: graph.center });
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const zoom = (f: number) => setView((v) => ({ ...v, k: Math.min(4, Math.max(0.25, v.k * f)) }));
  const onWheel = (e: WheelEvent) => zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15);
  const onDown = (e: PointerEvent) => {
    if ((e.target as Element).closest("[data-hit]")) return;
    drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
  };
  const onMove = (e: PointerEvent) => {
    const d = drag.current;
    if (d) setView((v) => ({ ...v, x: d.vx + (e.clientX - d.x) / v.k, y: d.vy + (e.clientY - d.y) / v.k }));
  };
  const selectedNode = sel?.type === "node" ? graph.nodes.find((n) => n.id === sel.id) : undefined;
  const selectedEdge = sel?.type === "edge" ? graph.edges.find((e) => e.id === sel.id) : undefined;
  const incident = selectedNode ? graph.edges.filter((e) => e.source === selectedNode.id || e.target === selectedNode.id) : [];

  return (
    <div className="graph-layout">
      <div className="graph-stage">
        <div className="graph-toolbar">
          <button type="button" className="btn btn-small" onClick={() => zoom(1.25)} aria-label="Zoom in">+</button>
          <button type="button" className="btn btn-small" onClick={() => zoom(0.8)} aria-label="Zoom out">−</button>
          <button type="button" className="btn btn-small" onClick={() => setView({ x: 0, y: 0, k: 1 })}>Reset view</button>
          <span className="legend">
            {(["address", "contract", "token", "program"] as const).map((k) => (
              <span key={k}><i style={{ background: KIND_COLOR[k] }} /> {k} ({KIND_SHAPE[k]})</span>
            ))}
          </span>
        </div>
        <svg
          className="graph-svg"
          viewBox="-450 -330 900 660"
          role="img"
          aria-label={`Relationship graph: ${graph.nodes.length} nodes, ${graph.edges.length} edges`}
          onWheel={onWheel}
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={() => { drag.current = null; }}
          onPointerLeave={() => { drag.current = null; }}
        >
          <g transform={`scale(${view.k}) translate(${view.x} ${view.y})`}>
            {graph.edges.map((e) => {
              const a = pos.get(e.source), b = pos.get(e.target);
              if (!a || !b) return null;
              const on = sel?.type === "edge" ? sel.id === e.id : selectedNode ? e.source === selectedNode.id || e.target === selectedNode.id : false;
              const off = bend.get(e.id) ?? 0;
              const [p, q] = e.source < e.target ? [a, b] : [b, a];
              const len = Math.hypot(q.x - p.x, q.y - p.y) || 1;
              const cx = (p.x + q.x) / 2 + (-(q.y - p.y) / len) * off;
              const cy = (p.y + q.y) / 2 + ((q.x - p.x) / len) * off;
              const d = `M ${a.x} ${a.y} Q ${cx} ${cy} ${b.x} ${b.y}`;
              return (
                <g key={e.id} data-hit="" onClick={() => setSel({ type: "edge", id: e.id })} className="edge-hit">
                  <path d={d} stroke="transparent" strokeWidth={12} fill="none" />
                  <path d={d} fill="none" className={`edge ${on ? "edge-on" : ""} edge-${e.kind}`} />
                  <title>{`${humanize(e.kind)} · ${e.count}×`}</title>
                </g>
              );
            })}
            {graph.nodes.map((n) => {
              const p = pos.get(n.id);
              if (!p) return null;
              return (
                <g key={n.id} data-hit="" onClick={() => setSel({ type: "node", id: n.id })} className="node-hit" role="button" aria-label={`${n.kind} ${n.id}`}>
                  <circle cx={p.x} cy={p.y} r={16} fill="transparent" />
                  <NodeMark n={n} p={p} selected={sel?.type === "node" && sel.id === n.id} />
                  {n.center || n.degree > 2 ? <text x={p.x + 14} y={p.y + 4} className="node-label">{shortId(n.id, 4)}</text> : null}
                  <title>{`${n.kind} ${n.id}`}</title>
                </g>
              );
            })}
          </g>
        </svg>
        <p className="note">Scroll or use + / − to zoom; drag the background to pan; click a node or edge for its evidence.</p>
      </div>
      <aside className="graph-details" aria-label="Selection details">
        {selectedNode ? (
          <>
            <h3>{selectedNode.center ? "Center wallet" : humanize(selectedNode.kind)}</h3>
            <p><Mono>{selectedNode.id}</Mono></p>
            <p className="muted">Depth {selectedNode.depth} · {incident.length} edges</p>
            {selectedNode.kind === "address" && !selectedNode.center ? (
              <Link className="btn btn-small" to={`/wallets/${graph.chain}/${encodeURIComponent(selectedNode.id)}`}>Open wallet</Link>
            ) : null}
            <ul className="metric-list">
              {incident.slice(0, 30).map((e) => (
                <li key={e.id}>
                  <button type="button" className="linklike" onClick={() => setSel({ type: "edge", id: e.id })}>
                    {humanize(e.kind)} {e.source === selectedNode.id ? "→" : "←"} {shortId(e.source === selectedNode.id ? e.target : e.source, 4)}
                  </button>{" "}
                  <span className="muted">{e.count}×</span>
                </li>
              ))}
            </ul>
          </>
        ) : null}
        {selectedEdge ? <EdgeDetails e={selectedEdge} /> : null}
        {!sel ? <p className="muted">Select a node or edge.</p> : null}
      </aside>
    </div>
  );
}

function EdgeDetails({ e }: { e: GraphEdge }) {
  return (
    <>
      <h3>{humanize(e.kind)}</h3>
      <p><Mono>{shortId(e.source)}</Mono> → <Mono>{shortId(e.target)}</Mono></p>
      <dl className="kv">
        <dt>Observations</dt><dd>{e.count}</dd>
        {e.asset ? (<><dt>Asset</dt><dd className="mono">{e.asset === "native" ? "native" : shortId(e.asset)}</dd></>) : null}
        {e.amount_raw ? (<><dt>Amount (raw)</dt><dd className="mono">{e.amount_raw}</dd></>) : null}
        <dt>Seen</dt><dd>{fmtTime(e.first_seen)} to {fmtTime(e.last_seen)}</dd>
        <dt>Verification</dt><dd><VerificationLine v={e.verification} /></dd>
      </dl>
      <h4>Evidence</h4>
      <ul className="metric-list">
        {e.evidence.map((x) => (
          <li key={`${x.tx_id}:${x.amount_raw ?? ""}`}>
            <Mono title={x.tx_id}>{shortId(x.tx_id)}</Mono> <span className="muted">pos {x.position ?? "?"} · {fmtTime(x.block_time)} · {x.provider}</span>{" "}
            <VerificationPill label={x.verification} />
          </li>
        ))}
      </ul>
      <p className="note">An edge is an observed interaction. It does not show common ownership or control.</p>
    </>
  );
}

export function GraphView({ chain, address, depth }: { chain: string; address: string; depth: number }) {
  const g = useApi(() => api.graph(chain, address, depth), [chain, address, depth]);
  if (g.status === "loading") return <Loading label="Building graph from stored observations…" />;
  if (g.status === "error") return <ErrorState error={g.error} onRetry={g.reload} />;
  const t = g.data.truncation;
  return (
    <>
      <DataOriginBanner origin={g.data.data_origin} />
      <Panel title={`${g.data.nodes.length} nodes · ${g.data.edges.length} edges`} aside={<Freshness at={null} />}>
        <p className="note">{g.data.statement}</p>
        <p><VerificationLine v={g.data.verification} /></p>
        {t.truncated || t.unexpanded_frontier ? (
          <p>
            <Pill tone="warn">bounded</Pill>{" "}
            {t.node_limit_reached ? `node limit ${g.data.limits.max_nodes} reached. ` : ""}
            {t.edge_limit_reached ? `edge limit ${g.data.limits.max_edges} reached. ` : ""}
            {t.timed_out ? "traversal timed out. " : ""}
            {t.unexpanded_frontier ? `${t.unexpanded_frontier} nodes at depth ${g.data.depth} not expanded.` : ""}
          </p>
        ) : null}
        {g.data.edges.length === 0 ? (
          <EmptyState title="No relationships stored">Retrieve activity for this wallet first. Nothing is drawn without evidence.</EmptyState>
        ) : <GraphCanvas key={`${chain}:${address}:${depth}`} graph={g.data} />}
      </Panel>
    </>
  );
}

export default function GraphPage() {
  const [params, setParams] = useSearchParams();
  const chain = params.get("chain") ?? "";
  const address = params.get("address") ?? "";
  const depth = Math.min(3, Math.max(1, Number(params.get("depth") ?? "1") || 1));
  return (
    <>
      <PageHeader
        title="Relationship Graph"
        lede="Evidence-backed interactions around one wallet: transfers, token activity, contract calls and program invocations. Bounded traversal; no ownership inference."
      />
      <Panel>
        <WalletPicker key={`${chain}:${address}`} initialChain={chain || "ethereum"} initialAddress={address} submitLabel="Show graph"
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
      {chain && address ? <GraphView chain={chain} address={address} depth={depth} /> : (
        <EmptyState title="Choose a wallet">Enter a wallet whose activity BIP has retrieved.</EmptyState>
      )}
    </>
  );
}
