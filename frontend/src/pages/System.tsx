import { useState } from "react";
import { ApiError, api } from "../api/client";
import type { BackupCreated, SystemHealth, SystemJob } from "../api/types";
import { RoleHint } from "../auth/RoleHint";
import { useCan } from "../auth/useAuth";
import { DataTable } from "../components/DataTable";
import { valueText } from "../components/discovery";
import { Modal } from "../components/Modal";
import { EmptyState, ErrorState, FieldError, Loading, PageHeader, Panel, Pill, Stat, type Tone } from "../components/ui";
import { chainLabel } from "../lib/address";
import { fmtBytes, fmtInt, fmtTime, humanize } from "../lib/format";
import { useApi } from "../lib/useApi";
import { DataOriginBanner } from "../components/intel";

const STATUS_TONE: Record<SystemHealth["status"], Tone> = { ok: "ok", degraded: "warn", critical: "bad" };
const RUN_TONE: Record<string, Tone> = { succeeded: "ok", success: "ok", ok: "ok", partial: "warn", failed: "bad", error: "bad", running: "info" };

function fmtUptime(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)} seconds`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} minutes`;
  if (seconds < 172800) return `${(seconds / 3600).toFixed(1)} hours`;
  return `${(seconds / 86400).toFixed(1)} days`;
}

function fmtInterval(seconds: number): string {
  if (seconds % 86400 === 0) return `every ${seconds / 86400} day${seconds === 86400 ? "" : "s"}`;
  if (seconds % 3600 === 0) return `every ${seconds / 3600} hour${seconds === 3600 ? "" : "s"}`;
  if (seconds % 60 === 0) return `every ${seconds / 60} minute${seconds === 60 ? "" : "s"}`;
  return `every ${seconds} seconds`;
}

function Jobs({ jobs, enabled, running }: { jobs: SystemJob[]; enabled: boolean; running: boolean }) {
  return (
    <Panel title="Scheduled jobs" aside={<Pill tone={enabled && running ? "ok" : "idle"}>{!enabled ? "scheduler disabled" : running ? "scheduler running" : "scheduler stopped"}</Pill>}>
      {jobs.length === 0 ? (
        <EmptyState title="No jobs">{enabled ? "The scheduler has no jobs registered." : "The scheduler is not enabled on this server, so nothing runs in the background."}</EmptyState>
      ) : (
        <DataTable
          caption="Scheduled jobs"
          rows={jobs}
          rowKey={(j) => j.job}
          columns={[
            { key: "job", header: "Job", priority: 1, render: (j) => humanize(j.job) },
            { key: "what", header: "What it does", priority: 3, render: (j) => j.description },
            { key: "every", header: "Schedule", priority: 2, render: (j) => fmtInterval(j.interval_seconds) },
            { key: "net", header: "Contacts providers", priority: 2, render: (j) => (j.contacts_providers ? <Pill tone="warn">yes</Pill> : "no") },
            { key: "state", header: "State", priority: 2, render: (j) => (j.running ? <Pill tone="info">running</Pill> : <Pill tone="idle">idle</Pill>) },
            { key: "fail", header: "Consecutive failures", priority: 2, numeric: true, render: (j) => <span className={j.consecutive_failures ? "bad-text" : undefined}>{j.consecutive_failures}</span> },
            { key: "next", header: "Next due", priority: 3, render: (j) => fmtTime(j.next_due) },
            {
              key: "last", header: "Last result", priority: 2, render: (j) =>
                j.last_run ? (
                  <span>
                    <Pill tone={RUN_TONE[j.last_run.status] ?? "idle"}>{j.last_run.status}</Pill> {fmtTime(j.last_run.finished_at ?? j.last_run.started_at)}
                    {j.last_run.detail ? <span className="muted wrap-any"> {j.last_run.detail}</span> : null}
                  </span>
                ) : (
                  <span className="muted">never run</span>
                ),
            },
          ]}
        />
      )}
    </Panel>
  );
}

function Backups({ backups, onCreated }: { backups: SystemHealth["backups"]; onCreated: () => void }) {
  const can = useCan("POST", "/system/backups");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<BackupCreated | null>(null);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      setCreated(await api.createBackup());
      onCreated();
    } catch (err) {
      setCreated(null);
      setError(err instanceof ApiError ? err.message : "The backup could not be created.");
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  return (
    <Panel
      title="Backups"
      aside={
        <span className="row-inline">
          <RoleHint can={can} />
          <button type="button" className="btn" disabled={!can.allowed || busy} title={can.reason} onClick={() => setConfirming(true)}>
            Create backup
          </button>
        </span>
      }
    >
      <dl className="kv">
        <dt>Scheduled backups</dt>
        <dd>{backups.enabled ? "enabled" : "disabled"}</dd>
        <dt>Backups stored</dt>
        <dd>{fmtInt(backups.count)}</dd>
        <dt>Newest backup</dt>
        <dd>{backups.latest ? `${fmtTime(backups.latest.created_at)}, ${fmtBytes(backups.latest.size_bytes)}` : "none yet"}</dd>
      </dl>
      {backups.count === 0 ? <p className="notice">There is no backup. If the database is lost, nothing can be restored.</p> : null}
      <FieldError id="backup-error" message={error} />
      {created ? (
        <p role="status" className="ok-text">
          Backup created ({fmtBytes(created.size_bytes)}, {fmtTime(created.created_at)}). Older backups removed by the keep policy: {valueText(created.pruned)}.
        </p>
      ) : null}
      {confirming ? (
        <Modal title="Create a backup now?" onClose={() => setConfirming(false)} busy={busy}>
          <p>This writes a copy of the database to the server's backup storage and then removes the oldest backups beyond the keep limit. It uses disk space and does not contact any provider.</p>
          <div className="modal-actions">
            <button type="button" className="btn" onClick={() => setConfirming(false)} disabled={busy}>Cancel</button>
            <button type="button" className="btn btn-primary" onClick={() => void create()} disabled={busy}>{busy ? "Creating…" : "Create backup"}</button>
          </div>
        </Modal>
      ) : null}
    </Panel>
  );
}

function Report({ h, reload }: { h: SystemHealth; reload: () => void }) {
  const disk = h.disk;
  return (
    <div className="stack disc">
      <Panel title="Overall status" aside={<button type="button" className="btn" onClick={reload}>Refresh</button>}>
        <div className="row-inline">
          <Pill tone={STATUS_TONE[h.status] ?? "idle"}>{h.status}</Pill>
          <span className="muted">BIP {h.version} · {h.environment} · up {fmtUptime(h.uptime_seconds)} · reported {fmtTime(h.time)}</span>
        </div>
        {h.warnings.length === 0 ? (
          <p>No warnings.</p>
        ) : (
          <ul className="list" aria-label="Warnings">
            {h.warnings.map((w) => (
              <li key={`${w.code}:${w.message}`}>
                <Pill tone={h.status === "critical" ? "bad" : "warn"}>{w.code}</Pill>
                <span className="wrap-any">{w.message}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <div className="grid grid-2">
        <Panel title="Database">
          <dl className="kv">
            <dt>Status</dt>
            <dd>{h.database.status}</dd>
            <dt>Schema version</dt>
            <dd>{h.database.schema_version ?? "not recorded"}</dd>
            <dt>Database size</dt>
            <dd>{fmtBytes(h.database.size_bytes)}</dd>
            <dt>WAL size</dt>
            <dd>{fmtBytes(h.database.wal_bytes)}</dd>
            <dt>Journal mode</dt>
            <dd>{h.database.journal_mode}</dd>
          </dl>
        </Panel>
        <Panel title="Disk">
          <dl className="kv">
            <dt>Writes</dt>
            <dd>
              <Pill tone={disk.writes === "allowed" ? "ok" : disk.writes === "refused" ? "bad" : "warn"}>{disk.writes}</Pill>
            </dd>
            {disk.error ? (
              <>
                <dt>Problem</dt>
                <dd>{disk.error}</dd>
              </>
            ) : null}
            {disk.free_bytes !== undefined && disk.total_bytes !== undefined ? (
              <>
                <dt>Free</dt>
                <dd>{fmtBytes(disk.free_bytes)} of {fmtBytes(disk.total_bytes)}{disk.free_ratio !== undefined ? ` (${(disk.free_ratio * 100).toFixed(1)}%)` : ""}</dd>
              </>
            ) : null}
            {disk.bip_used_bytes !== undefined ? (
              <>
                <dt>Used by BIP</dt>
                <dd>{fmtBytes(disk.bip_used_bytes)}{disk.max_data_bytes ? ` of a ${fmtBytes(disk.max_data_bytes)} limit` : ""}</dd>
              </>
            ) : null}
            {disk.min_free_bytes !== undefined ? (
              <>
                <dt>Reserved free space</dt>
                <dd>{fmtBytes(disk.min_free_bytes)}</dd>
              </>
            ) : null}
          </dl>
          {disk.reasons && disk.reasons.length ? <p className="notice">{disk.reasons.join("; ")}</p> : null}
          {disk.writes === "unknown" ? <p className="muted">Disk space could not be measured, so it is not reported as fine.</p> : null}
        </Panel>
      </div>

      <Panel title="Providers">
        <div className="stats stats-small" aria-label="Provider summary">
          <Stat label="Registered" value={h.providers.total} />
          <Stat label="Healthy" value={h.providers.healthy} />
          <Stat label="Degraded" value={h.providers.degraded} />
          <Stat label="Unavailable" value={h.providers.unavailable} />
          <Stat label="Untested" value={h.providers.untested} />
          <Stat label="Disabled" value={h.providers.disabled} />
        </div>
        <p className="note">{h.providers.configured} configured, {h.providers.adapters_available} with an adapter, {h.providers.rpc_providers} RPC providers. Untested is not the same as healthy.</p>
      </Panel>

      <Panel title="Discovery and alerts">
        <dl className="kv">
          <dt>Open signals</dt>
          <dd>{fmtInt(h.discovery.open_signals)}</dd>
          <dt>Unread alerts</dt>
          <dd>{fmtInt(h.discovery.unread_alerts)}</dd>
          <dt>Last discovery pass</dt>
          <dd>{h.discovery.last_run ? `${h.discovery.last_run.status}, ${fmtTime(h.discovery.last_run.finished_at ?? h.discovery.last_run.started_at)}` : "none has run"}</dd>
        </dl>
      </Panel>

      <Panel title="Ingestion">
        <p>{h.ingestion.failed_runs_24h} failed run{h.ingestion.failed_runs_24h === 1 ? "" : "s"} in the last 24 hours.</p>
        {h.ingestion.errors_24h.length ? (
          <p className="muted">Errors in 24 hours: {h.ingestion.errors_24h.map((e) => `${e.code} (${e.count})`).join(", ")}.</p>
        ) : null}
        <DataTable
          caption="Recent ingestion runs"
          rows={h.ingestion.recent_runs}
          rowKey={(r) => String(r.id)}
          empty="No ingestion run has been recorded."
          columns={[
            { key: "id", header: "Run", priority: 1, render: (r) => `Run ${r.id}` },
            { key: "chain", header: "Chain", priority: 2, render: (r) => chainLabel(r.chain) },
            { key: "status", header: "Status", priority: 2, render: (r) => <Pill tone={RUN_TONE[r.status] ?? "idle"}>{r.status}</Pill> },
            { key: "at", header: "Started", priority: 2, render: (r) => fmtTime(r.started_at) },
            { key: "rpc", header: "RPC calls", priority: 3, numeric: true, render: (r) => fmtInt(r.rpc_calls) },
            { key: "err", header: "Errors", priority: 3, numeric: true, render: (r) => fmtInt(r.error_count) },
          ]}
        />
      </Panel>

      <Jobs jobs={h.workers.jobs} enabled={h.workers.enabled} running={h.workers.running} />
      <Backups backups={h.backups} onCreated={reload} />
    </div>
  );
}

export default function System() {
  const health = useApi(api.systemHealth);
  return (
    <>
      <PageHeader title="System" lede="Operational state of this BIP server: storage, background jobs, providers and backups. Paths and secrets are never shown." />
      {health.status === "ready" ? <DataOriginBanner origin={health.data.data_origin ?? "stored_observations"} /> : null}
      {health.status === "loading" ? <Loading /> : null}
      {health.status === "error" ? <ErrorState error={health.error} onRetry={health.reload} /> : null}
      {health.status === "ready" ? <Report h={health.data} reload={health.reload} /> : null}
    </>
  );
}
