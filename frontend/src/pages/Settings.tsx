import { api } from "../api/client";
import { ErrorState, Loading, PageHeader, Panel, Pill } from "../components/ui";
import { fmtBytes } from "../lib/format";
import { useApi } from "../lib/useApi";

export default function Settings() {
  const settings = useApi(api.settings);
  return (
    <>
      <PageHeader
        title="Settings"
        lede="Read-only, non-sensitive configuration. Change settings with BIP_* environment variables on the server."
      />
      {settings.status === "loading" ? <Loading /> : null}
      {settings.status === "error" ? <ErrorState error={settings.error} onRetry={settings.reload} /> : null}
      {settings.status === "ready" ? (
        <div className="grid grid-2">
          <Panel title="Application">
            <dl className="kv">
              <dt>Version</dt>
              <dd className="mono">{settings.data.version}</dd>
              <dt>Environment</dt>
              <dd>{settings.data.environment}</dd>
              <dt>Log level</dt>
              <dd>{settings.data.log_level}</dd>
              <dt>Enabled chains</dt>
              <dd>{settings.data.enabled_chains.join(", ")}</dd>
              <dt>Authentication</dt>
              <dd>
                <Pill tone="warn">Not implemented</Pill>
              </dd>
            </dl>
            <p className="note">
              There is no login yet. Keep the backend bound to 127.0.0.1 (the default) and do not
              expose it to a network.
            </p>
          </Panel>
          <Panel title="Data and storage">
            <dl className="kv">
              <dt>Database</dt>
              <dd>
                {settings.data.database_engine}, schema v{settings.data.database_schema_version}
              </dd>
              {settings.data.storage ? (
                <>
                  <dt>BIP data used</dt>
                  <dd className="num">
                    {fmtBytes(settings.data.storage.bip_used_bytes)} of {fmtBytes(settings.data.storage.max_data_bytes)}
                  </dd>
                  <dt>Writes</dt>
                  <dd>
                    <Pill tone={settings.data.storage.writes_allowed ? "ok" : "bad"}>
                      {settings.data.storage.writes_allowed ? "Allowed" : "Paused"}
                    </Pill>
                  </dd>
                </>
              ) : (
                <>
                  <dt>Storage</dt>
                  <dd>Usage could not be measured</dd>
                </>
              )}
            </dl>
            {settings.data.storage?.reasons.length ? (
              <ul className="list">
                {settings.data.storage.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            ) : null}
          </Panel>
          <Panel title="Data acquisition">
            <dl className="kv">
              <dt>Live provider requests</dt>
              <dd>
                <Pill tone="idle">{settings.data.live_requests_enabled ? "Enabled" : "Disabled"}</Pill>
              </dd>
              <dt>Cross-verification sample rate</dt>
              <dd className="num">{(settings.data.cross_verification_sample_rate * 100).toFixed(1)}%</dd>
            </dl>
            <p className="note">These flags have no effect until the provider router exists.</p>
          </Panel>
          <Panel title="API">
            <dl className="kv">
              <dt>Max request body</dt>
              <dd className="num">{fmtBytes(settings.data.max_request_bytes)}</dd>
              <dt>Allowed origins</dt>
              <dd className="mono">{settings.data.cors_origins.join(", ")}</dd>
            </dl>
          </Panel>
        </div>
      ) : null}
    </>
  );
}
