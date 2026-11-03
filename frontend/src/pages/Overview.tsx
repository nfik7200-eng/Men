import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { ChainStatus } from "../api/types";
import { DataOriginBanner } from "../components/intel";
import { EmptyState, ErrorState, Loading, PageHeader, Panel, Pill, Stat } from "../components/ui";
import { chainLabel } from "../lib/address";
import { fmtInt, fmtTime, humanize } from "../lib/format";
import { useApi } from "../lib/useApi";

export function chainTone(c: ChainStatus) {
  if (c.connected) return "ok" as const;
  return c.configured ? ("warn" as const) : ("idle" as const);
}

export function chainStateLabel(c: ChainStatus): string {
  if (c.connected) return "Connected";
  if (c.adapter_implemented && c.usable_providers.length > 0) return "Adapter ready, not connected";
  if (c.adapter_implemented) return "No usable provider";
  return c.configured ? "Configured, no adapter" : "Not configured";
}

export default function Overview() {
  const health = useApi(api.health);
  const summary = useApi(api.summary);
  const intel = useApi(api.intelStatus);

  return (
    <>
      <PageHeader
        title="Overview"
        lede="Live state of this BIP instance. Every number comes from the backend database."
      />
      <div className="grid grid-health">
        <Panel title="System health">
          {health.status === "loading" ? <Loading /> : null}
          {health.status === "error" ? <ErrorState error={health.error} onRetry={health.reload} /> : null}
          {health.status === "ready" ? (
            <dl className="kv">
              <dt>API</dt>
              <dd>
                <Pill tone={health.data.status === "ok" ? "ok" : "warn"}>{health.data.status}</Pill>
              </dd>
              <dt>Database</dt>
              <dd>
                <Pill tone={health.data.database === "ok" ? "ok" : "bad"}>{health.data.database}</Pill>
              </dd>
              <dt>Storage writes</dt>
              <dd>
                <Pill tone={health.data.storage_writes === "allowed" ? "ok" : "bad"}>
                  {health.data.storage_writes}
                </Pill>
              </dd>
              <dt>Version</dt>
              <dd className="mono">{health.data.version}</dd>
              <dt>Environment</dt>
              <dd>{health.data.environment}</dd>
              <dt>Checked</dt>
              <dd className="mono">{fmtTime(health.data.timestamp)}</dd>
            </dl>
          ) : null}
        </Panel>
        {summary.status === "ready"
          ? summary.data.chains.map((c) => (
              <Panel key={c.chain} title={chainLabel(c.chain)} aside={<Pill tone={chainTone(c)}>{chainStateLabel(c)}</Pill>}>
                <dl className="kv">
                  <dt>Transactions stored</dt>
                  <dd className="num">{fmtInt(c.transactions_stored)}</dd>
                  <dt>Latest known {c.position_kind}</dt>
                  <dd className="num">
                    {c.latest_observation
                      ? ((c.position_kind === "block" ? c.latest_observation.head_block : c.latest_observation.head_slot) ?? "—")
                      : "Not retrieved"}
                  </dd>
                  <dt>Last retrieval</dt>
                  <dd className="mono">{fmtTime(c.last_successful_retrieval)}</dd>
                </dl>
              </Panel>
            ))
          : null}
      </div>

      {summary.status === "loading" ? <Loading label="Loading summary…" /> : null}
      {summary.status === "error" ? <ErrorState error={summary.error} onRetry={summary.reload} /> : null}
      {summary.status === "ready" ? (
        <>
          <div className="stats">
            <Stat label="Wallets with stored data" value={fmtInt(summary.data.wallets_stored)} />
            <Stat label="Watchlisted addresses" value={fmtInt(summary.data.watchlisted_addresses)} />
            <Stat label="Transactions stored" value={fmtInt(summary.data.transactions_stored)} />
            <Stat label="Transfers stored" value={fmtInt(summary.data.transfers_stored)} />
            <Stat label="Open investigations" value={fmtInt(summary.data.open_investigations)} />
            <Stat
              label="Healthy RPC providers"
              value={`${summary.data.providers.healthy} / ${summary.data.providers.rpc_providers}`}
              hint={`${summary.data.providers.untested} untested · ${summary.data.providers.unavailable} unavailable`}
            />
            <Stat
              label="Ingestion runs"
              value={fmtInt(summary.data.ingestion.runs_total)}
              hint={Object.entries(summary.data.ingestion.runs_by_status).map(([k, v]) => `${v} ${k}`).join(" · ") || "none yet"}
            />
          </div>
          <Panel title="Intelligence and verification">
            {intel.status === "loading" ? <Loading /> : null}
            {intel.status === "error" ? <ErrorState error={intel.error} onRetry={intel.reload} /> : null}
            {intel.status === "ready" ? (
              <>
                <DataOriginBanner origin={intel.data.data_origin} />
                <div className="stats">
                  <Stat label="Corroborated transactions" value={fmtInt(intel.data.verification.corroborated)}
                    hint={`of ${fmtInt(intel.data.verification.stored_transactions)} stored`} />
                  <Stat label="Unverified transactions" value={fmtInt(intel.data.verification.unverified)} hint="single provider or not checked" />
                  <Stat label="Conflicted / invalidated" value={`${fmtInt(intel.data.verification.conflicted)} / ${fmtInt(intel.data.verification.invalidated)}`} />
                  <Stat label="Analysed wallets" value={fmtInt(intel.data.analysed_wallets)} hint={`${fmtInt(intel.data.analysis_runs)} runs`} />
                  <Stat label="Anomalies (latest runs)" value={fmtInt(intel.data.anomalies)} />
                  <Stat label="Findings" value={fmtInt(intel.data.findings)} />
                  <Stat label="Research leads" value={fmtInt(intel.data.opportunity_candidates)} />
                </div>
              </>
            ) : null}
          </Panel>
          {summary.data.transactions_stored === 0 ? (
            <p className="notice">
              No blockchain data has been ingested yet. Open Wallet Intelligence, look up an address
              and choose "Retrieve recent activity" to fetch real data from a provider.
            </p>
          ) : null}
          <div className="grid grid-2">
            <Panel title="Recent findings">
              {summary.data.recent_findings.length === 0 ? (
                <EmptyState title="No findings recorded">
                  Findings appear here when they are attached to an investigation.
                </EmptyState>
              ) : (
                <ul className="list">
                  {summary.data.recent_findings.map((f) => (
                    <li key={f.finding_id}>
                      <span className="mono">{f.finding_id}</span> · {humanize(f.finding_type)}
                      <Pill tone="info">{humanize(f.status)}</Pill>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
            <Panel title="Recent activity">
              {summary.data.recent_activity.length === 0 ? (
                <EmptyState title="No activity yet">
                  Create a <Link to="/watchlists">watchlist</Link> or an{" "}
                  <Link to="/investigations">investigation</Link> to get started.
                </EmptyState>
              ) : (
                <ul className="list activity">
                  {summary.data.recent_activity.map((a, i) => (
                    <li key={`${a.created_at}-${i}`}>
                      <span className="mono muted">{fmtTime(a.created_at)}</span>
                      <span>{a.summary}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>
        </>
      ) : null}
    </>
  );
}
