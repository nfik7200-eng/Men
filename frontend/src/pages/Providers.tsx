import { useState } from "react";
import { ApiError, api } from "../api/client";
import type { HealthCheck, Lifecycle, Provider } from "../api/types";
import { ErrorState, Loading, PageHeader, Pill, type Tone } from "../components/ui";
import { chainLabel } from "../lib/address";
import { fmtTime, humanize } from "../lib/format";
import { useApi } from "../lib/useApi";

export const lifecycleTone: Record<Lifecycle, Tone> = {
  healthy: "ok",
  degraded: "warn",
  unavailable: "bad",
  untested: "info",
  adapter_available: "idle",
  configured: "idle",
  registered: "idle",
  disabled: "idle",
};

export const lifecycleLabel: Record<Lifecycle, string> = {
  healthy: "Healthy",
  degraded: "Degraded",
  unavailable: "Unavailable",
  untested: "Untested",
  adapter_available: "Adapter, not configured",
  configured: "Configured, no adapter",
  registered: "Registered only",
  disabled: "Disabled",
};

function ProviderRow({ p, onChange }: { p: Provider; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<HealthCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const checkable = p.adapter_available && p.enabled && ["untested", "healthy", "degraded", "unavailable"].includes(p.lifecycle);

  const runCheck = async () => {
    setBusy(true);
    setError(null);
    try {
      setCheck(await api.healthCheck(p.id));
      onChange();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Health check failed.");
    } finally {
      setBusy(false);
    }
  };
  const toggle = async () => {
    setError(null);
    try {
      await api.setProviderEnabled(p.id, !p.enabled);
      onChange();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change the provider.");
    }
  };

  return (
    <>
      <tr>
        <td>
          <strong>{p.name}</strong>
          <div className="mono muted">{p.id}</div>
        </td>
        <td>{p.chains.length ? p.chains.map(chainLabel).join(", ") : "—"}</td>
        <td>
          <Pill tone={lifecycleTone[p.lifecycle]}>{lifecycleLabel[p.lifecycle]}</Pill>
        </td>
        <td>{p.adapter_available ? "Yes" : "No"}</td>
        <td>
          {humanize(p.configuration_status)}
          {p.endpoint_host ? <div className="mono muted">{p.endpoint_host}</div> : null}
        </td>
        <td className="mono">
          {fmtTime(p.last_success_at)}
          {p.last_latency_ms !== null ? <div className="muted">{p.last_latency_ms} ms</div> : null}
        </td>
        <td>
          <div className="row">
            {checkable ? (
              <button type="button" className="btn btn-small" disabled={busy} onClick={() => void runCheck()}>
                {busy ? "Checking…" : "Health check"}
              </button>
            ) : null}
            <button type="button" className="btn btn-small" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
              {open ? "Hide" : "Details"}
            </button>
          </div>
        </td>
      </tr>
      {open || check || error ? (
        <tr className="detail-row">
          <td colSpan={7}>
            {error ? <p className="field-error" role="alert">{error}</p> : null}
            {check ? (
              <p role="status">
                <Pill tone={check.ok ? "ok" : "bad"}>{check.ok ? "Check passed" : "Check failed"}</Pill>{" "}
                {check.outcome} · {check.latency_ms ?? "—"} ms
                {check.head_block !== null ? ` · block ${check.head_block}` : ""}
                {check.head_slot !== null ? ` · slot ${check.head_slot}` : ""}
                {check.detail ? <span className="muted"> · {check.detail}</span> : null}
              </p>
            ) : null}
            {open ? (
              <div className="grid grid-2">
                <dl className="kv">
                  <dt>Operator</dt>
                  <dd>{p.operator}</dd>
                  <dt>Access</dt>
                  <dd>{p.access === "public" ? "Public (no key)" : "Requires credentials"}</dd>
                  <dt>Configuration</dt>
                  <dd>{p.configuration_note}</dd>
                  <dt>Methods</dt>
                  <dd className="mono">{p.methods.length ? p.methods.join(", ") : "None (no adapter)"}</dd>
                  <dt>Rate limit</dt>
                  <dd>{p.rate_limit}</dd>
                  <dt>BIP throttle</dt>
                  <dd>{p.client_rps !== null ? `${p.client_rps} requests/s (BIP policy, not a provider limit)` : "—"}</dd>
                  <dt>Last outcome</dt>
                  <dd>{p.last_outcome ?? "never contacted"}</dd>
                </dl>
                <div className="stack">
                  <ul className="list">
                    {p.limitations.map((l) => (
                      <li key={l}>{l}</li>
                    ))}
                  </ul>
                  {p.notes ? <p className="note">{p.notes}</p> : null}
                  <div className="row">
                    <a href={p.docs_url} target="_blank" rel="noopener noreferrer">Documentation</a>
                    <a href={p.terms_url} target="_blank" rel="noopener noreferrer">Terms</a>
                    <button type="button" className="btn btn-small" onClick={() => void toggle()}>
                      {p.enabled ? "Disable" : "Enable"}
                    </button>
                  </div>
                </div>
              </div>
            ) : null}
          </td>
        </tr>
      ) : null}
    </>
  );
}

export default function Providers() {
  const providers = useApi(api.providers);
  return (
    <>
      <PageHeader
        title="Providers"
        lede="Registered data sources and what BIP has actually observed from each. A provider is healthy only after a real, identity-verified request succeeded."
      />
      {providers.status === "loading" ? <Loading /> : null}
      {providers.status === "error" ? <ErrorState error={providers.error} onRetry={providers.reload} /> : null}
      {providers.status === "ready" ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Provider</th>
                <th>Chain</th>
                <th>Status</th>
                <th>Adapter</th>
                <th>Configuration</th>
                <th>Last success</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {providers.data.map((p) => (
                <ProviderRow key={p.id} p={p} onChange={providers.reload} />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <p className="note">
        Health checks send two read-only requests (network identity, then the current block or
        slot) to that provider only. Credentialed endpoints are configured on the server with
        BIP_PROVIDER_* variables; their URLs and keys are never shown here.
      </p>
    </>
  );
}
