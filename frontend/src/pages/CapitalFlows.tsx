import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Flows } from "../api/types";
import { DataOriginBanner, Freshness, ScopeNote, VerificationLine, WalletPicker } from "../components/intel";
import { EmptyState, ErrorState, Loading, Mono, PageHeader, Panel } from "../components/ui";
import { chainLabel } from "../lib/address";
import { fmtTime, shortId } from "../lib/format";
import { useApi } from "../lib/useApi";

// Categorical slots 1-2 of the validated dark palette (dataviz reference; ΔE checked by script).
const IN_COLOR = "#3987e5";
const OUT_COLOR = "#d95926";

function assetLabel(chain: string, asset: string) {
  return asset === "native" ? (chain === "solana" ? "SOL" : "ETH") : shortId(asset);
}

/** Daily in/out bars for one asset. Bars use scaled amounts when decimals are known, else counts. */
export function DailyFlowChart({ flows, asset }: { flows: Flows; asset: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const rows = flows.time_buckets.filter((b) => b.asset === asset);
  if (!rows.length) return <EmptyState title="No timestamped transfers for this asset" />;
  const useAmount = rows.every((r) => r.in !== null && r.out !== null);
  const val = (s: string | null, raw: string) => Number(useAmount ? s : raw) || 0;
  const data = rows.map((r) => ({ date: r.date, in: useAmount ? val(r.in, r.in_raw) : 0, out: useAmount ? val(r.out, r.out_raw) : 0, count: r.count, r }));
  const max = Math.max(1e-12, ...data.map((d) => Math.max(d.in, d.out, useAmount ? 0 : d.count)));
  const W = 640, H = 180, pad = 28, slot = (W - pad * 2) / data.length, bw = Math.max(2, Math.min(18, slot / 2 - 2));
  const y = (v: number) => H - pad - (v / max) * (H - pad * 2);
  const h = hover !== null ? data[hover] : null;
  return (
    <figure className="chart">
      <div className="legend" aria-hidden="true">
        {useAmount ? (
          <>
            <span><i style={{ background: IN_COLOR }} /> In</span>
            <span><i style={{ background: OUT_COLOR }} /> Out</span>
          </>
        ) : <span><i style={{ background: IN_COLOR }} /> Transfers per day (decimals unknown: counts only)</span>}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Daily flows for ${asset}`} onMouseLeave={() => setHover(null)}>
        <line x1={pad} x2={W - pad} y1={H - pad} y2={H - pad} className="axis" />
        {data.map((d, i) => {
          const x = pad + i * slot + slot / 2;
          const a = useAmount ? d.in : d.count;
          return (
            <g key={d.date} onMouseEnter={() => setHover(i)}>
              <rect x={pad + i * slot} y={pad} width={slot} height={H - pad * 2} fill="transparent" />
              <rect x={useAmount ? x - bw - 1 : x - bw / 2} y={y(a)} width={bw} height={Math.max(0, H - pad - y(a))} rx={2} fill={IN_COLOR} />
              {useAmount ? <rect x={x + 1} y={y(d.out)} width={bw} height={Math.max(0, H - pad - y(d.out))} rx={2} fill={OUT_COLOR} /> : null}
            </g>
          );
        })}
        <text x={pad} y={H - 8} className="tick">{data[0]?.date}</text>
        {data.length > 1 ? <text x={W - pad} y={H - 8} className="tick" textAnchor="end">{data[data.length - 1]?.date}</text> : null}
      </svg>
      {h ? (
        <div className="tooltip" role="status">
          <strong>{h.date}</strong> · {h.count} transfers
          {useAmount ? <> · in {h.r.in} · out {h.r.out}</> : <> · raw in {h.r.in_raw} · raw out {h.r.out_raw}</>}
        </div>
      ) : <div className="tooltip muted">Hover a day for values.</div>}
    </figure>
  );
}

export function FlowsView({ chain, address }: { chain: string; address: string }) {
  const flows = useApi(() => api.flows(chain, address), [chain, address]);
  const [asset, setAsset] = useState<string | null>(null);
  if (flows.status === "loading") return <Loading label="Computing flows from stored transfers…" />;
  if (flows.status === "error") return <ErrorState error={flows.error} onRetry={flows.reload} />;
  const f = flows.data;
  const chosen = asset ?? f.assets[0]?.asset ?? null;
  return (
    <>
      <DataOriginBanner origin={f.data_origin} />
      <Panel title={`${chainLabel(chain)} · ${shortId(address)}`} aside={<Freshness at={null} />}>
        <ScopeNote scope={f.scope} />
        <p><VerificationLine v={f.verification} /> <span className="muted">Canonical source: {f.canonical_source} table · {f.transfers_considered} transfers</span></p>
        <div className="page-actions">
          <Link className="btn" to={`/wallets/${chain}/${encodeURIComponent(address)}`}>Wallet intelligence</Link>
          <Link className="btn" to={`/graph?chain=${chain}&address=${encodeURIComponent(address)}`}>Relationship graph</Link>
        </div>
      </Panel>
      {f.assets.length === 0 ? (
        <EmptyState title="No transfers stored for this wallet">
          Retrieve recent activity on the wallet page first. Nothing is drawn without stored transfers.
        </EmptyState>
      ) : (
        <>
          <Panel title="Per asset: in, out and net">
            <div className="table-wrap">
              <table>
                <thead><tr><th>Asset</th><th>Standard</th><th className="num">In</th><th className="num">Out</th><th className="num">Net</th><th>Count in / out / self</th><th></th></tr></thead>
                <tbody>
                  {f.assets.map((a) => (
                    <tr key={a.asset}>
                      <td><Mono title={a.asset}>{assetLabel(chain, a.asset)}</Mono></td>
                      <td>{a.standard}</td>
                      <td className="num mono">{a.in ?? `${a.in_raw} raw`}</td>
                      <td className="num mono">{a.out ?? `${a.out_raw} raw`}</td>
                      <td className="num mono">{a.net ?? `${a.net_raw} raw`}</td>
                      <td>{a.in_count} / {a.out_count} / {a.self_count}</td>
                      <td><button type="button" className="btn btn-small" onClick={() => setAsset(a.asset)} aria-pressed={chosen === a.asset}>Chart</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="note">No USD values. Amounts are raw units unless decimals are known.</p>
          </Panel>
          {chosen ? (
            <Panel title={`Daily flows · ${assetLabel(chain, chosen)}`}>
              <DailyFlowChart flows={f} asset={chosen} />
            </Panel>
          ) : null}
          <div className="grid grid-2">
            <Panel title="Counterparties">
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Counterparty</th><th>Asset</th><th className="num">Transfers</th><th className="num">In</th><th className="num">Out</th></tr></thead>
                  <tbody>
                    {f.counterparties.slice(0, 20).map((c) => (
                      <tr key={`${c.counterparty}:${c.asset}`}>
                        <td><Mono title={c.counterparty}>{shortId(c.counterparty)}</Mono></td>
                        <td><Mono title={c.asset}>{assetLabel(chain, c.asset)}</Mono></td>
                        <td className="num">{c.count}</td>
                        <td className="num mono">{c.in ?? c.in_raw}</td>
                        <td className="num mono">{c.out ?? c.out_raw}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
            <Panel title="Largest transfers (per asset)">
              <ul className="metric-list">
                {Object.entries(f.largest_transfers).flatMap(([a, rows]) =>
                  rows.slice(0, 3).map((r) => (
                    <li key={`${a}:${r.tx_id}:${r.event_key ?? ""}`}>
                      <span className="num metric-value">{r.amount ?? `${r.amount_raw ?? "?"} raw`}</span>{" "}
                      {assetLabel(chain, a)} {r.direction} <Mono title={r.tx_id}>{shortId(r.tx_id)}</Mono>{" "}
                      <span className="muted">{fmtTime(r.block_time)} · {r.verification}</span>
                    </li>
                  )),
                )}
              </ul>
            </Panel>
          </div>
          <Panel title="Repeated paths">
            {f.repeated_paths.length === 0 ? <EmptyState title="No path repeated in the retrieved transfers" /> : (
              <ul className="metric-list">
                {f.repeated_paths.map((r) => (
                  <li key={`${r.from}:${r.to}:${r.asset}`}>
                    <span className="num metric-value">{r.count}×</span> <Mono title={r.from}>{shortId(r.from)}</Mono> → <Mono title={r.to}>{shortId(r.to)}</Mono> <span className="muted">{assetLabel(chain, r.asset)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title="Limitations"><ul>{f.limitations.map((l) => <li key={l}>{l}</li>)}</ul></Panel>
        </>
      )}
    </>
  );
}

export default function CapitalFlows() {
  const [params, setParams] = useSearchParams();
  const chain = params.get("chain") ?? "";
  const address = params.get("address") ?? "";
  return (
    <>
      <PageHeader
        title="Capital Flows"
        lede="Inflows, outflows, counterparties and repeated paths for one wallet, computed from BIP's stored transfers. Retrieved data only; no prices."
      />
      <Panel>
        <WalletPicker
          key={`${chain}:${address}`}
          initialChain={chain || "ethereum"}
          initialAddress={address}
          submitLabel="Show flows"
          onPick={(c, a) => setParams({ chain: c, address: a })}
        />
      </Panel>
      {chain && address ? <FlowsView chain={chain} address={address} /> : (
        <EmptyState title="Choose a wallet">Enter a wallet whose activity BIP has retrieved to see its flows.</EmptyState>
      )}
    </>
  );
}
