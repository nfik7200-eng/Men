import { useState } from "react";
import { ApiError, api } from "../../api/client";
import type { DiscoveryRunRecord, ExpansionPolicy, ExpansionPolicyId, ExpansionResult } from "../../api/types";
import { RoleHint } from "../../auth/RoleHint";
import { useCan } from "../../auth/useAuth";
import { DataTable } from "../../components/DataTable";
import { humanLabel } from "../../components/discovery";
import { ErrorState, Loading, Panel, Pill, type Tone } from "../../components/ui";
import { CHAINS, chainLabel } from "../../lib/address";
import { fmtInt, fmtTime } from "../../lib/format";
import { useApi } from "../../lib/useApi";

const TONE: Record<string, Tone> = { succeeded: "ok", partial: "warn", blocked: "warn", failed: "bad" };
const LOCAL_ONLY = "Local database only; never contacts a provider.";

function detectorTotals(run: DiscoveryRunRecord): string {
  const d = run.summary?.detectors;
  if (!d || Object.keys(d).length === 0) return "no detector output";
  const sum = (k: "created" | "merged") => Object.values(d).reduce((n, x) => n + (x[k] ?? 0), 0);
  return `${fmtInt(sum("created"))} created, ${fmtInt(sum("merged"))} merged`;
}

function RunsTable({ runs, caption }: { runs: DiscoveryRunRecord[]; caption: string }) {
  return (
    <DataTable
      caption={caption}
      rows={runs}
      rowKey={(r) => String(r.id)}
      empty="No run recorded."
      columns={[
        { key: "id", header: "Run", priority: 1, render: (r) => `#${r.id}` },
        { key: "policy", header: "Policy", priority: 2, render: (r) => (r.summary?.policy?.id ? humanLabel(r.summary.policy.id) : <span className="muted">detector pass</span>) },
        { key: "chain", header: "Chain", priority: 2, render: (r) => (r.chain ? chainLabel(r.chain) : "all") },
        { key: "status", header: "Status", priority: 2, render: (r) => <Pill tone={TONE[r.status] ?? "idle"}>{r.status}</Pill> },
        { key: "out", header: "Output", priority: 2, render: detectorTotals },
        {
          key: "trunc",
          header: "Truncated",
          priority: 3,
          render: (r) => (r.summary?.truncated?.length ? r.summary.truncated.map(humanLabel).join(", ") : <span className="muted">no</span>),
        },
        { key: "start", header: "Started", priority: 3, render: (r) => fmtTime(r.started_at) },
        { key: "end", header: "Finished", priority: 3, render: (r) => fmtTime(r.finished_at) },
      ]}
    />
  );
}

export function RunHistoryPanel() {
  const runs = useApi(() => api.discoveryRuns(10));
  return (
    <Panel title="Run history">
      <p className="muted">The most recent discovery passes over stored data, newest first. A truncated run stopped at a cap, so it did not examine everything.</p>
      {runs.status === "loading" ? <Loading /> : null}
      {runs.status === "error" ? <ErrorState error={runs.error} onRetry={runs.reload} /> : null}
      {runs.status === "ready" ? <RunsTable runs={runs.data.items} caption="Discovery run history" /> : null}
    </Panel>
  );
}

function RunPolicy({ policy, chain, onDone }: { policy: ExpansionPolicy; chain: string; onDone: () => void }) {
  const can = useCan("POST", `/discovery/expand/${chain}`);
  const [state, setState] = useState<{ busy: boolean; result?: ExpansionResult; error?: string }>({ busy: false });
  const run = async () => {
    setState({ busy: true });
    try {
      const result = await api.runExpansion(chain, policy.id as ExpansionPolicyId);
      setState({ busy: false, result });
      onDone();
    } catch (err) {
      setState({ busy: false, error: err instanceof ApiError ? err.message : "The policy could not run." });
    }
  };
  const r = state.result;
  return (
    <div className="stack-tight">
      <div className="row">
        <button type="button" className="btn" disabled={!can.allowed || state.busy} title={can.reason} onClick={() => void run()}>
          {state.busy ? "Running…" : `Run ${humanLabel(policy.id)} on ${chainLabel(chain)}`}
        </button>
        <RoleHint can={can} />
      </div>
      <div role="status" aria-live="polite">
        {r ? (
          <p>
            Run {r.run_id ?? ""} {r.status ?? "finished"} on {chainLabel(chain)}: {fmtInt(r.seeds_used ?? 0)} seeds,{" "}
            {fmtInt(r.entities_examined ?? 0)} entities examined, {fmtInt(r.relationship_rows_read ?? 0)} relationship rows read
            {r.truncated?.length ? `; truncated at ${r.truncated.map(humanLabel).join(", ")}` : ""}.
          </p>
        ) : null}
        {state.error ? <p className="field-error">{state.error}</p> : null}
      </div>
    </div>
  );
}

export function ExpansionPanel() {
  const exp = useApi(api.expansion);
  const chains = CHAINS.filter((c) => c.id === "ethereum" || c.id === "solana");
  return (
    <Panel title="Expansion policies and budgets">
      {exp.status === "loading" ? <Loading /> : null}
      {exp.status === "error" ? <ErrorState error={exp.error} onRetry={exp.reload} /> : null}
      {exp.status === "ready" ? (
        <div className="stack disc">
          <p className="note">
            {LOCAL_ONLY} {exp.data.source ? `Server note: ${exp.data.source}.` : ""} Expansion walks stored relationships from seed
            entities within the budgets below and produces candidates for review, not findings.{" "}
            {exp.data.scheduled ? "A scheduled run is enabled." : "No scheduled run is enabled; policies run only when started here."}
          </p>
          <dl className="kv" aria-label="Expansion budgets">
            <dt>Maximum depth</dt>
            <dd>{fmtInt(exp.data.budgets.max_depth)}</dd>
            <dt>Maximum entities</dt>
            <dd>{fmtInt(exp.data.budgets.max_entities)}</dd>
            <dt>Maximum relationship rows</dt>
            <dd>{fmtInt(exp.data.budgets.max_relationship_rows)}</dd>
            <dt>Maximum seeds</dt>
            <dd>{fmtInt(exp.data.budgets.max_seeds)}</dd>
            <dt>Cooldown</dt>
            <dd>{fmtInt(exp.data.budgets.cooldown_days)} days</dd>
          </dl>
          {exp.data.policies.map((p) => (
            <div key={p.id} role="group" className="detector-card" aria-label={`Policy ${humanLabel(p.id)}`}>
              <h3>{humanLabel(p.id)}</h3>
              <dl className="kv">
                <dt>Objective</dt>
                <dd>{p.objective}</dd>
                <dt>Seeds</dt>
                <dd>{typeof p.seeds === "string" ? p.seeds : JSON.stringify(p.seeds)}</dd>
                <dt>Stopping rule</dt>
                <dd>{typeof p.stopping === "string" ? p.stopping : JSON.stringify(p.stopping)}</dd>
              </dl>
              {chains.map((c) => (
                <RunPolicy key={c.id} policy={p} chain={c.id} onDone={exp.reload} />
              ))}
            </div>
          ))}
          <h3>Recent expansion runs</h3>
          <RunsTable runs={exp.data.runs} caption="Expansion runs" />
        </div>
      ) : null}
    </Panel>
  );
}
