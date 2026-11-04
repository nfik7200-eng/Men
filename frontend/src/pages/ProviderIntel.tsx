import { Fragment, useState } from "react";
import { ApiError, api } from "../api/client";
import type {
  CapabilityStatus,
  Connectivity,
  ProbePreview,
  ProbeRun,
  ProviderProfile,
  ProviderProfileDetail,
  Reliability,
} from "../api/types";
import { DataOriginBanner } from "../components/intel";
import { EmptyState, ErrorState, Loading, Mono, Panel, Pill, type Tone } from "../components/ui";
import { chainLabel } from "../lib/address";
import { fmtTime, humanize } from "../lib/format";
import { useApi } from "../lib/useApi";

// Every status is shown as text; colour only reinforces it.
export const CONNECTIVITY: Record<Connectivity, { label: string; tone: Tone }> = {
  reachable: { label: "Reachable", tone: "ok" },
  network_blocked: { label: "Network blocked", tone: "warn" },
  authentication_required: { label: "Needs configuration", tone: "idle" },
  unauthorized: { label: "Access denied", tone: "bad" },
  rate_limited: { label: "Rate-limited", tone: "warn" },
  timeout: { label: "Timed out", tone: "warn" },
  tls_error: { label: "TLS error", tone: "bad" },
  provider_error: { label: "Provider error", tone: "bad" },
  invalid_response: { label: "Invalid response", tone: "bad" },
  wrong_network: { label: "Wrong network", tone: "bad" },
  not_tested: { label: "Not tested", tone: "idle" },
};

export const STATUS: Record<CapabilityStatus, { label: string; tone: Tone }> = {
  supported: { label: "Supported", tone: "ok" },
  unsupported: { label: "Unsupported", tone: "bad" },
  inconclusive: { label: "Inconclusive", tone: "warn" },
  not_tested: { label: "Not tested", tone: "idle" },
};

const RELIABILITY: Record<Reliability["state"], { label: string; tone: Tone }> = {
  unknown: { label: "Unknown (never reached)", tone: "idle" },
  insufficient_data: { label: "Insufficient data", tone: "idle" },
  observed_reliable: { label: "Observed reliable", tone: "ok" },
  observed_degraded: { label: "Observed degraded", tone: "warn" },
  observed_unreliable: { label: "Observed unreliable", tone: "bad" },
};

export function ConnectivityPill({ c, envRestricted }: { c: Connectivity; envRestricted?: boolean }) {
  const meta = CONNECTIVITY[c] ?? { label: c, tone: "idle" as Tone };
  return <Pill tone={meta.tone}>{envRestricted ? "Blocked by local environment" : meta.label}</Pill>;
}

function StatusPill({ s }: { s: CapabilityStatus }) {
  return <Pill tone={STATUS[s].tone}>{STATUS[s].label}</Pill>;
}

function Coverage({ p }: { p: ProviderProfile }) {
  if (!p.coverage) return <span className="muted">—</span>;
  const c = p.coverage;
  const parts = [
    c.unsupported ? `${c.unsupported} unsupported` : null,
    c.inconclusive ? `${c.inconclusive} inconclusive` : null,
    `${c.not_tested} not tested`,
  ].filter(Boolean);
  return (
    <span className="nowrap">
      {c.supported}/{c.total} supported
      <div className="muted">{parts.join(" · ")}</div>
    </span>
  );
}

function sampleText(sample: Record<string, unknown>): string {
  const n = (k: string) => (typeof sample[k] === "number" ? String(sample[k]) : null);
  const where = n("block") ? `block ${n("block") ?? ""}` : n("slot") ? `slot ${n("slot") ?? ""}` : "sample";
  const dist = n("distance_from_head") ?? n("distance") ?? n("distance_from_finalized");
  return dist ? `${where}, ${dist} below the observed head` : where;
}

function ReliabilityView({ r }: { r: Reliability }) {
  const meta = RELIABILITY[r.state];
  return (
    <div className="stack">
      <p><Pill tone={meta.tone}>{meta.label}</Pill> <span className="muted">{r.method}</span></p>
      <dl className="kv">
        <dt>Sample</dt>
        <dd>{r.sample_size} attempts in {r.runs} run(s){r.environment_blocked ? ` · ${r.environment_blocked} blocked by the local environment (excluded)` : ""}</dd>
        <dt>Success rate</dt>
        <dd>{r.success_rate === null || r.state === "insufficient_data" ? "insufficient data" : `${(r.success_rate * 100).toFixed(1)}% (${r.successes}/${r.sample_size})`}</dd>
        <dt>Latency</dt>
        <dd>
          median {r.latency.median_ms === null ? "insufficient data" : `${r.latency.median_ms} ms`} · p95{" "}
          {r.latency.p95_ms === null ? "insufficient data" : `${r.latency.p95_ms} ms`} ({r.latency.samples} samples)
        </dd>
        <dt>Rate limits / timeouts</dt>
        <dd>{r.rate_limit_events} / {r.timeouts}</dd>
        <dt>Invalid responses / errors</dt>
        <dd>{r.invalid_responses} / {r.provider_errors}</dd>
        <dt>Window</dt>
        <dd>{fmtTime(r.window.first_observation)} to {fmtTime(r.window.last_observation)}</dd>
      </dl>
      <ul className="list">{r.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
      <p className="note">
        States need at least {r.rules.min_attempts} attempts across {r.rules.min_runs} runs. This is
        not a ranking and says nothing about evidence corroboration.
      </p>
    </div>
  );
}

function ProfileDetail({ id }: { id: string }) {
  const d = useApi(() => api.providerProfile(id), [id]);
  if (d.status === "loading") return <Loading />;
  if (d.status === "error") return <ErrorState error={d.error} onRetry={d.reload} />;
  const p: ProviderProfileDetail = d.data;
  return (
    <div className="stack">
      {p.environment_note ? <p className="notice" role="note">{p.environment_note}</p> : null}
      {p.capabilities.length ? (
        <div className="table-wrap">
          <table aria-label={`Capability matrix for ${p.name}`}>
            <thead>
              <tr><th>Capability</th><th>Method</th><th>Status</th><th>Last tested</th><th>Evidence</th></tr>
            </thead>
            <tbody>
              {p.capabilities.map((c) => (
                <tr key={c.id}>
                  <td>{c.label}<div className="muted mono">{c.id}</div></td>
                  <td className="mono">{c.method ?? `from ${c.derived_from ?? "—"}`}</td>
                  <td>{c.observation ? <StatusPill s={c.observation.status} /> : <StatusPill s="not_tested" />}</td>
                  <td className="mono">{c.observation && c.observation.status !== "not_tested" ? fmtTime(c.observation.observed_at) : "—"}</td>
                  <td>
                    {c.observation ? c.observation.explanation : "Never probed."}
                    {c.observation?.latency_ms != null ? <div className="muted">{c.observation.latency_ms} ms</div> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <EmptyState title="No capability catalogue">This source has no RPC adapter.</EmptyState>}
      <div className="grid grid-2">
        <Panel title="Reliability observations">
          {p.reliability ? <ReliabilityView r={p.reliability} /> : <p className="muted">Not applicable.</p>}
        </Panel>
        <Panel title="Historical coverage and documented limits">
          {p.historical_coverage ? (
            <p>
              <Pill tone={p.historical_coverage.label === "historical_sample_observed" ? "info" : "idle"}>{humanize(p.historical_coverage.label)}</Pill>{" "}
              <Pill tone="idle">{humanize(p.historical_coverage.archive)}</Pill>
              <br />
              <span className="muted">{p.historical_coverage.note}</span>
              {p.historical_coverage.sample ? <span className="muted"> · {sampleText(p.historical_coverage.sample)}</span> : null}
            </p>
          ) : null}
          <dl className="kv">
            <dt>Rate limit (documented)</dt><dd>{p.documented.rate_limit}</dd>
            <dt>Archive (documented)</dt><dd>{p.documented.archive}</dd>
            <dt>eth_getLogs range</dt><dd>{p.documented.max_log_block_range ?? "unknown"}</dd>
            <dt>Basis</dt><dd>{humanize(p.documented.basis)}{p.documented.checked ? `, checked ${p.documented.checked}` : ""}</dd>
          </dl>
          <p className="note">{p.documented.note}</p>
        </Panel>
      </div>
      {p.consistency_checks.length ? (
        <Panel title="Cross-provider consistency">
          <ul className="list">
            {p.consistency_checks.slice(0, 10).map((c, i) => (
              <li key={i}>
                <Pill tone={c.outcome === "consistent" ? "ok" : c.outcome === "conflict" ? "bad" : "idle"}>{c.outcome}</Pill>{" "}
                {humanize(c.comparison)} · {c.provider_a} vs {c.provider_b}
                {c.reference ? <span className="mono"> @ {c.reference}</span> : null}
                {!c.independent ? <Pill tone="warn">not independent</Pill> : null}
                <div className="muted">{c.note}</div>
              </li>
            ))}
          </ul>
          <p className="note">Provider diagnostics only. They never change the verification status of wallet evidence.</p>
        </Panel>
      ) : null}
    </div>
  );
}

function RunResult({ run }: { run: ProbeRun }) {
  const obs = run.observations ?? [];
  const requested = obs.filter((o) => o.method && o.status !== "not_tested");
  const counts = (s: CapabilityStatus) => obs.filter((o) => o.status === s).length;
  return (
    <div className="stack" role="status" aria-label="Probe run result">
      <DataOriginBanner origin={run.data_origin === "offline_fixture" ? "offline_fixture" : "stored_observations"} />
      <p>
        <Pill tone={run.status === "completed" ? "ok" : run.status === "partial" ? "warn" : "bad"}>Run {run.status}</Pill>{" "}
        {run.requests_used} requests sent · {run.requests_skipped} planned requests not sent ·{" "}
        {run.duration_ms ?? "—"} ms · {run.summary?.rate_limit_events ?? 0} rate-limit events
      </p>
      <p className="muted">
        {counts("supported")} supported · {counts("unsupported")} unsupported · {counts("inconclusive")} inconclusive ·{" "}
        {counts("not_tested")} not tested
      </p>
      {run.failure_summary ? <p className="muted">Stopped: {run.failure_summary}</p> : null}
      {Object.entries(run.summary?.providers ?? {}).map(([pid, s]) => (
        <p key={pid}>
          <strong>{pid}</strong>{" "}
          <ConnectivityPill c={s.connectivity as Connectivity} envRestricted={s.environment_restricted} />{" "}
          {s.requests_used} requests{s.stop_reason ? ` · stopped: ${s.stop_reason}` : ""}
        </p>
      ))}
      {requested.length ? (
        <div className="table-wrap">
          <table aria-label="Probe observations">
            <thead><tr><th>Provider</th><th>Capability</th><th>Status</th><th>Connectivity</th><th>Explanation</th></tr></thead>
            <tbody>
              {obs.map((o) => (
                <tr key={`${o.provider_id}:${o.capability}`}>
                  <td className="mono">{o.provider_id}</td>
                  <td>{o.capability}</td>
                  <td><StatusPill s={o.status} /></td>
                  <td>{o.status === "not_tested" ? "—" : <ConnectivityPill c={o.connectivity} envRestricted={o.environment_restricted} />}</td>
                  <td>{o.explanation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

export function ProbeConsole({ profiles, onDone }: { profiles: ProviderProfile[]; onDone: () => void }) {
  const eligible = profiles.filter((p) => p.probe_eligible);
  const [selected, setSelected] = useState<string[]>([]);
  const [preview, setPreview] = useState<ProbePreview | null>(null);
  const [run, setRun] = useState<ProbeRun | null>(null);
  const [busy, setBusy] = useState<"preview" | "run" | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const fail = (e: unknown) => {
    setError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Request failed."));
    setBusy(null);
  };
  const toggle = (id: string) => {
    setPreview(null);
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  };
  const doPreview = () => {
    setBusy("preview");
    setError(null);
    api.probePreview(selected).then((p) => { setPreview(p); setBusy(null); }, fail);
  };
  const doRun = () => {
    setBusy("run");
    setError(null);
    api.probeRun(selected).then((r) => { setRun(r); setBusy(null); onDone(); }, fail);
  };
  return (
    <Panel title="Run bounded provider check">
      <p className="note">
        Nothing is sent until you start a run. A run sends at most the shown number of read-only
        requests, once each (no retries), spaced at least one second apart per provider, and stops
        a provider that is rate-limited, denies access, serves another network or cannot be
        reached. Budgets are enforced by the server.
      </p>
      {eligible.length === 0 ? (
        <EmptyState title="No provider can be probed">Enable a provider with an adapter and a configured endpoint.</EmptyState>
      ) : (
        <fieldset className="probe-select">
          <legend>Providers (none selected = all eligible)</legend>
          {eligible.map((p) => (
            <label key={p.id} className="check">
              <input type="checkbox" checked={selected.includes(p.id)} onChange={() => toggle(p.id)} />
              {p.name} <span className="muted">({chainLabel(p.chain)})</span>
            </label>
          ))}
        </fieldset>
      )}
      <div className="row">
        <button type="button" className="btn" onClick={doPreview} disabled={busy !== null || eligible.length === 0}>
          {busy === "preview" ? "Preparing…" : "Preview budget"}
        </button>
        <button type="button" className="btn btn-primary" onClick={doRun} disabled={busy !== null || !preview}>
          {busy === "run" ? "Running bounded check…" : "Run bounded provider check"}
        </button>
      </div>
      {!preview && eligible.length ? <p className="muted">Preview the budget before running.</p> : null}
      {error ? <ErrorState error={error} /> : null}
      {preview ? (
        <div className="stack" aria-label="Probe preview">
          <p>
            <strong>At most {preview.max_requests} requests</strong> · up to {preview.max_duration_s} s ·{" "}
            {preview.budget.max_calls_per_provider} per provider · {preview.budget.request_timeout_s} s timeout ·{" "}
            {preview.budget.retries} retries
          </p>
          {preview.network_note ? (
            <p className="notice" role="note">
              Previous attempt from this environment was blocked ({humanize(preview.known_network_restriction?.failure_kind ?? "")},{" "}
              {fmtTime(preview.known_network_restriction?.observed_at ?? null)}). {preview.network_note}
            </p>
          ) : null}
          <ul className="list">
            {preview.providers.map((p) => (
              <li key={p.provider_id}>
                <strong>{p.provider_id}</strong> ({chainLabel(p.chain)}{p.authentication_required ? ", credentials configured" : ", keyless"}):{" "}
                {p.planned.map((c) => c.capability).join(", ")}
                {p.over_budget.length ? <div className="muted">Over the per-provider cap (not tested): {p.over_budget.join(", ")}</div> : null}
              </li>
            ))}
          </ul>
          {preview.ineligible.length ? (
            <details>
              <summary>{preview.ineligible.length} provider(s) cannot be probed</summary>
              <ul className="list">{preview.ineligible.map((x) => <li key={x.provider_id}><Mono>{x.provider_id}</Mono>: {x.reason}</li>)}</ul>
            </details>
          ) : null}
        </div>
      ) : null}
      {run ? <RunResult run={run} /> : null}
    </Panel>
  );
}

export function ProviderIntelligence() {
  const profiles = useApi(api.providerProfiles);
  const runs = useApi(api.probeRuns);
  const [open, setOpen] = useState<string | null>(null);
  const reload = () => {
    profiles.reload();
    runs.reload();
  };
  if (profiles.status === "loading") return <Loading label="Loading provider intelligence…" />;
  if (profiles.status === "error") return <ErrorState error={profiles.error} onRetry={profiles.reload} />;
  const data = profiles.data;
  const rpc = data.items.filter((p) => p.adapter_available);
  return (
    <section aria-label="Provider intelligence" className="stack">
      <DataOriginBanner origin={data.data_origin === "offline_fixture" ? "offline_fixture" : "stored_observations"} />
      {data.last_environment_block ? (
        <p className="notice" role="note">
          The latest probe from this environment could not connect ({humanize(data.last_environment_block.failure_kind ?? "")} at{" "}
          {fmtTime(data.last_environment_block.observed_at)}). That is a local network restriction: provider availability remains unknown.
        </p>
      ) : null}
      <Panel title="Capability profiles">
        <div className="table-wrap">
          <table aria-label="Provider capability profiles">
            <thead>
              <tr>
                <th>Provider · infrastructure</th><th>Chain</th><th>Configuration</th>
                <th>Connectivity</th><th>Capabilities</th><th>Reliability</th><th>Last probe</th><th />
              </tr>
            </thead>
            <tbody>
              {rpc.map((p) => (
                <Fragment key={p.id}>
                  <tr>
                    <td><strong>{p.name}</strong><div className="mono muted">{p.id} · {p.infrastructure}</div></td>
                    <td>{chainLabel(p.chain)}<div className="muted">{p.network}</div></td>
                    <td>
                      {p.configured ? "Configured" : p.authentication_required ? "Needs credentials" : "Not configured"}
                      <div className="muted">{p.authentication_required ? "API key" : "Keyless"}{p.enabled ? "" : " · disabled"}</div>
                    </td>
                    <td><ConnectivityPill c={p.connectivity} envRestricted={p.environment_restricted} /></td>
                    <td><Coverage p={p} /></td>
                    <td>{p.reliability_state ? <Pill tone={RELIABILITY[p.reliability_state].tone}>{RELIABILITY[p.reliability_state].label}</Pill> : <span className="muted">No observations</span>}</td>
                    <td className="mono">{p.last_probe_at ? fmtTime(p.last_probe_at) : "never"}</td>
                    <td>
                      <button type="button" className="btn btn-small" aria-expanded={open === p.id} onClick={() => setOpen(open === p.id ? null : p.id)}>
                        {open === p.id ? "Hide" : "Capabilities"}
                      </button>
                    </td>
                  </tr>
                  {open === p.id ? (
                    <tr className="detail-row">
                      <td colSpan={8}><ProfileDetail id={p.id} /></td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <ProbeConsole profiles={data.items} onDone={reload} />
      <Panel title="Probe history">
        {runs.status === "loading" ? <Loading /> : null}
        {runs.status === "error" ? <ErrorState error={runs.error} onRetry={runs.reload} /> : null}
        {runs.status === "ready" && runs.data.items.length === 0 ? (
          <EmptyState title="No probe runs yet">Capabilities stay "not tested" until a bounded check runs.</EmptyState>
        ) : null}
        {runs.status === "ready" && runs.data.items.length ? (
          <ul className="list">
            {runs.data.items.map((r) => (
              <li key={r.run_id}>
                <span className="mono">{fmtTime(r.started_at)}</span> · <Pill tone={r.status === "completed" ? "ok" : r.status === "partial" ? "warn" : "bad"}>{r.status}</Pill>{" "}
                {r.providers.join(", ")} · {r.requests_used} requests{r.data_origin === "offline_fixture" ? " · offline fixture" : ""}
                {r.failure_summary ? <div className="muted">{r.failure_summary}</div> : null}
              </li>
            ))}
          </ul>
        ) : null}
      </Panel>
    </section>
  );
}
