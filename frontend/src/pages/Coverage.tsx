import { useState } from "react";
import { ApiError, api } from "../api/client";
import type { CollectorManifest, CollectorStatus, CoverageGapRange, Detector, DetectorCatalogue, DiscoveryEnvelope } from "../api/types";
import { RoleHint } from "../auth/RoleHint";
import { useCan } from "../auth/useAuth";
import { DataTable } from "../components/DataTable";
import { ExternalLink } from "../components/ExternalLink";
import { humanLabel, valueText } from "../components/discovery";
import { DataOriginBanner } from "../components/intel";
import { Modal } from "../components/Modal";
import { EmptyState, ErrorState, FieldError, Loading, PageHeader, Panel, Pill, type Tone } from "../components/ui";
import { chainLabel } from "../lib/address";
import { fmtInt, fmtTime } from "../lib/format";
import { useApi } from "../lib/useApi";
import { ExpansionPanel, RunHistoryPanel } from "./coverage/ExpansionPanels";

type Chain = "ethereum" | "solana";

const STATUS_TONE: Record<string, Tone> = { succeeded: "ok", partial: "warn", blocked: "warn", failed: "bad" };
const SHOWN_ELSEWHERE = new Set([
  "status", "message", "started_at", "finished_at", "rpc_calls", "providers", "finalized_head", "lag_slots",
  "first_block", "last_block", "first_slot", "last_slot", "not_covered", "gap_recorded", "data_origin", "discovery_version",
]);

const count = (v: unknown): string => (typeof v === "number" ? fmtInt(v) : valueText(v));

/** One collector run: outcome, message, positions, request spend and every counter it recorded. */
export function Manifest({ m, label }: { m: CollectorManifest; label: string }) {
  const counters = Object.entries(m).filter(([k, v]) => !SHOWN_ELSEWHERE.has(k) && (typeof v === "number" || typeof v === "boolean" || v === null));
  const first = m.first_block ?? m.first_slot ?? null;
  const last = m.last_block ?? m.last_slot ?? null;
  const unit = m.first_slot !== undefined || m.last_slot !== undefined ? "slots" : "blocks";
  return (
    <section className="manifest" aria-label={label}>
      <div className="row-inline">
        <Pill tone={STATUS_TONE[m.status] ?? "idle"}>{m.status}</Pill>
        <span className="wrap-any">{m.message || "No message recorded."}</span>
      </div>
      <dl className="kv">
        <dt>Started</dt>
        <dd>{fmtTime(m.started_at ?? null)}</dd>
        <dt>Finished</dt>
        <dd>{fmtTime(m.finished_at ?? null)}</dd>
        <dt>Range read</dt>
        <dd>{first !== null && last !== null ? `${unit} ${fmtInt(first)} to ${fmtInt(last)}` : "nothing read"}</dd>
        <dt>Finalized head</dt>
        <dd>{typeof m.finalized_head === "number" ? fmtInt(m.finalized_head) : "not retrieved"}</dd>
        <dt>RPC calls</dt>
        <dd>{typeof m.rpc_calls === "number" ? fmtInt(m.rpc_calls) : "not recorded"}</dd>
        <dt>Providers used</dt>
        <dd>{m.providers && m.providers.length ? m.providers.join(", ") : "none"}</dd>
        {m.gap_recorded ? (
          <>
            <dt>Gap recorded</dt>
            <dd>{gapText(m.gap_recorded)}</dd>
          </>
        ) : null}
      </dl>
      {counters.length ? (
        <details>
          <summary>Counters ({counters.length})</summary>
          <dl className="kv">
            {counters.map(([k, v]) => (
              <Pair key={k} label={humanLabel(k)} value={count(v)} />
            ))}
          </dl>
        </details>
      ) : null}
    </section>
  );
}

function Pair({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

function gapText(g: CoverageGapRange): string {
  const [a, b, unit] = g.from_slot !== undefined ? [g.from_slot, g.to_slot, "slots"] : [g.from_block, g.to_block, "blocks"];
  return `${unit} ${a === undefined ? "?" : fmtInt(a)} to ${b === undefined ? "?" : fmtInt(b)}: ${g.reason}`;
}

function RunDialog({ chain, limit, onCancel, onConfirm, busy }: { chain: Chain; limit: number | undefined; onCancel: () => void; onConfirm: () => void; busy: boolean }) {
  return (
    <Modal title={`Run the ${chainLabel(chain)} collector once?`} onClose={onCancel} busy={busy}>
      <p>
        <strong>This contacts a public RPC provider.</strong> One run is capped at 150 requests and 90 seconds
        {limit !== undefined && limit < 150 ? ` (this server allows ${limit} requests)` : ""} and reads finalized data only.
      </p>
      <p className="muted">It runs once and stops. Nothing is scheduled by this button, and ordinary wallet data is not changed.</p>
      <div className="modal-actions">
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="button" className="btn btn-primary" onClick={onConfirm} disabled={busy}>
          {busy ? "Running…" : "Contact provider and run once"}
        </button>
      </div>
    </Modal>
  );
}

function CollectorPanel({ chain }: { chain: Chain }) {
  const status = useApi<CollectorStatus>(chain === "ethereum" ? api.ethereumCollector : api.solanaCollector);
  const can = useCan("POST", `/discovery/collect/${chain}`);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<(DiscoveryEnvelope & CollectorManifest) | null>(null);

  const runOnce = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await (chain === "ethereum" ? api.collectEthereum() : api.collectSolana()));
      status.reload();
    } catch (err) {
      setResult(null);
      setError(err instanceof ApiError ? err.message : "The run could not be started.");
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  const s = status.status === "ready" ? status.data : null;
  const cursor = s ? (chain === "ethereum" ? s.last_block_scanned : s.last_slot_scanned) ?? null : null;
  const head = s?.last_run?.finalized_head ?? null;
  const lag = s?.last_run
    ? typeof s.last_run.lag_slots === "number" ? s.last_run.lag_slots : cursor !== null && head !== null ? Math.max(0, head - cursor) : null
    : null;
  const unit = chain === "ethereum" ? "block" : "slot";

  return (
    <Panel
      title={`${chainLabel(chain)} collector`}
      aside={
        <span className="row-inline">
          <RoleHint can={can} />
          <button type="button" className="btn" onClick={() => setConfirming(true)} disabled={!can.allowed || busy} title={can.reason} aria-label={`Run ${chainLabel(chain)} collector once`}>
            Run once
          </button>
        </span>
      }
    >
      {status.status === "loading" ? <Loading /> : null}
      {status.status === "error" ? <ErrorState error={status.error} onRetry={status.reload} /> : null}
      {s ? (
        <>
          <dl className="kv" aria-label={`${chainLabel(chain)} collector position`}>
            <dt>Cursor (last {unit} scanned)</dt>
            <dd>{cursor !== null ? fmtInt(cursor) : "Not started: the cursor is not set"}</dd>
            <dt>Finalized head at last run</dt>
            <dd>{head !== null ? fmtInt(head) : "not retrieved"}</dd>
            <dt>Lag behind the head</dt>
            <dd>{lag !== null ? `${fmtInt(lag)} ${unit}s` : "unknown (no run recorded)"}</dd>
            <dt>Collector version</dt>
            <dd>{s.version}</dd>
          </dl>
          <h3>Last run</h3>
          {s.last_run ? <Manifest m={s.last_run} label={`${chainLabel(chain)} last run`} /> : <EmptyState title="No run recorded">The collector has not run on this database. Nothing was collected, so detectors that need collector data have nothing to examine.</EmptyState>}
          {s.coverage_gaps !== undefined ? (
            <>
              <h3>Coverage gaps</h3>
              {s.coverage_gaps.length === 0 ? (
                <p className="muted">No coverage gaps recorded. This does not prove every {unit} was read; see what is not covered below.</p>
              ) : (
                <ul className="list" aria-label={`${chainLabel(chain)} coverage gaps`}>
                  {s.coverage_gaps.map((g, i) => (
                    <li key={`${gapText(g)}:${i}`}>
                      <span className="wrap-any">{gapText(g)}</span>
                      {g.at ? <span className="muted">recorded {fmtTime(g.at)}</span> : null}
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : null}
          <h3>Limits per run</h3>
          <dl className="kv">
            {Object.entries(s.limits).map(([k, v]) => (
              <Pair key={k} label={humanLabel(k)} value={count(v)} />
            ))}
          </dl>
          <h3>Not covered</h3>
          {s.not_covered.length === 0 ? <p className="muted">The collector reported nothing as not covered.</p> : (
            <ul className="bullets" aria-label={`${chainLabel(chain)} not covered`}>
              {s.not_covered.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
          {s.recognised_programs ? (
            <>
              <h3>Recognised programs</h3>
              <p className="muted">Only these programs are decoded. Activity of any other program is counted, not interpreted.</p>
              <DataTable
                caption="Recognised Solana programs"
                rows={s.recognised_programs}
                rowKey={(p) => p.program_id}
                empty="No programs are recognised."
                columns={[
                  { key: "name", header: "Program", priority: 1, render: (p) => p.name },
                  { key: "id", header: "Program ID", priority: 2, render: (p) => <span className="mono wrap-any">{p.program_id}</span> },
                  { key: "src", header: "Source", priority: 2, render: (p) => <ExternalLink url={p.source_url} /> },
                  { key: "ver", header: "Verified", priority: 2, render: (p) => p.verified_at },
                ]}
              />
            </>
          ) : null}
        </>
      ) : null}
      <FieldError id={`${chain}-run-error`} message={error} />
      {result ? (
        <div className="stack-tight" role="region" aria-label={`${chainLabel(chain)} manual run result`}>
          <h3>Result of the run you started</h3>
          <Manifest m={result} label={`${chainLabel(chain)} manual run manifest`} />
        </div>
      ) : null}
      {confirming ? <RunDialog chain={chain} limit={s?.limits.max_rpc_calls_per_run} busy={busy} onCancel={() => setConfirming(false)} onConfirm={() => void runOnce()} /> : null}
    </Panel>
  );
}

function DetectorCard({ d }: { d: Detector }) {
  return (
    <article className="detector-card" aria-label={d.title}>
      <div className="row-inline">
        <h3>{d.title}</h3>
        <Pill tone="info">{humanLabel(d.family)}</Pill>
        <Pill tone="idle">confidence cap {d.confidence_cap.toFixed(2)}</Pill>
      </div>
      <dl className="kv">
        <dt>Detector</dt>
        <dd><span className="mono">{d.id}</span> version {d.version}</dd>
        <dt>Chains</dt>
        <dd>{d.chains.map(chainLabel).join(", ") || "none"}</dd>
        <dt>Method</dt>
        <dd>{d.method}</dd>
        <dt>Data required</dt>
        <dd>{d.data_required}</dd>
        <dt>Coverage</dt>
        <dd>{d.coverage}</dd>
        <dt>Limitations</dt>
        <dd>{d.limitations}</dd>
        <dt>Refresh</dt>
        <dd>{d.refresh}</dd>
        <dt>Cost</dt>
        <dd>{d.cost}</dd>
      </dl>
    </article>
  );
}

function Detectors({ catalogue }: { catalogue: ReturnType<typeof useApi<DetectorCatalogue>> }) {
  return (
    <Panel title="Detector catalogue">
      <p className="muted">Every detector reads data BIP already stored. Coverage says what it can see; limitations say what it cannot. A confidence cap below 1 means no signal from it is ever a verified fact.</p>
      {catalogue.status === "loading" ? <Loading /> : null}
      {catalogue.status === "error" ? <ErrorState error={catalogue.error} onRetry={catalogue.reload} /> : null}
      {catalogue.status === "ready" ? (
        catalogue.data.detectors.length === 0 ? (
          <EmptyState title="No detectors registered" />
        ) : (
          <div className="stack disc">
            {catalogue.data.detectors.map((d) => (
              <DetectorCard key={d.id} d={d} />
            ))}
          </div>
        )
      ) : null}
    </Panel>
  );
}

export default function Coverage() {
  const catalogue = useApi(api.detectors);
  return (
    <>
      <PageHeader
        title="Coverage"
        lede="What BIP has read, how far behind it is, and what it cannot see. Missing data is never shown as zero."
      />
      {catalogue.status === "ready" ? <DataOriginBanner origin={catalogue.data.data_origin} /> : null}
      <div className="stack disc">
        <CollectorPanel chain="ethereum" />
        <CollectorPanel chain="solana" />
        <Detectors catalogue={catalogue} />
        <RunHistoryPanel />
        <ExpansionPanel />
      </div>
    </>
  );
}
