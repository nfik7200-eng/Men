import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { Alert, AlertRule, Severity } from "../api/types";
import { RoleHint } from "../auth/RoleHint";
import { useCan } from "../auth/useAuth";
import { DataTable } from "../components/DataTable";
import { SeverityPill, humanLabel } from "../components/discovery";
import { DataOriginBanner } from "../components/intel";
import { EmptyState, ErrorState, FieldError, Loading, PageHeader, Panel, Pill } from "../components/ui";
import { CHAINS, chainLabel } from "../lib/address";
import { fmtInt, fmtTime } from "../lib/format";
import { notifyAlertsChanged } from "../lib/alertsSync";
import { useApi } from "../lib/useApi";

const PAGE_SIZE = 25;
const message = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);

function AlertList() {
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const alerts = useApi(() => api.alerts({ unread: unreadOnly, limit: PAGE_SIZE, offset }), [unreadOnly, offset]);
  const canRead = useCan("POST", "/alerts/1/read");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (action: () => Promise<string>) => {
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      setDone(await action());
      alerts.reload();
      notifyAlertsChanged();
    } catch (err) {
      setError(message(err, "Could not update the alerts."));
    } finally {
      setBusy(false);
    }
  };
  const markOne = (a: Alert) => void run(async () => {
    await api.markAlertRead(a.id);
    return "Alert marked read.";
  });
  const markAll = () => void run(async () => {
    const r = await api.markAllAlertsRead();
    return r.marked_read === 0 ? "There were no unread alerts." : `Marked ${fmtInt(r.marked_read)} alert${r.marked_read === 1 ? "" : "s"} read.`;
  });

  return (
    <Panel
      title="Alerts"
      aside={
        alerts.status === "ready" ? (
          <span className="muted" role="status">
            {unreadOnly ? `${fmtInt(alerts.data.total)} unread` : `${fmtInt(alerts.data.unread)} unread of ${fmtInt(alerts.data.total)}`}
          </span>
        ) : null
      }
    >
      <div className="row">
        <label className="check">
          <input type="checkbox" checked={unreadOnly} onChange={(e) => { setUnreadOnly(e.target.checked); setOffset(0); }} />
          Unread only
        </label>
        <button type="button" className="btn" onClick={markAll} disabled={busy || !canRead.allowed || (alerts.status === "ready" && alerts.data.unread === 0)} title={canRead.reason}>
          Mark all read
        </button>
        <RoleHint can={canRead} />
      </div>
      <FieldError id="alerts-error" message={error} />
      {done ? <p role="status" className="ok-text">{done}</p> : null}
      {alerts.status === "loading" ? <Loading /> : null}
      {alerts.status === "error" ? <ErrorState error={alerts.error} onRetry={alerts.reload} /> : null}
      {alerts.status === "ready" ? (
        <>
          <DataOriginBanner origin={alerts.data.data_origin} />
          {alerts.data.total === 0 ? (
            <EmptyState title={unreadOnly ? "No unread alerts" : "No alerts yet"}>
              {unreadOnly
                ? "Every alert has been read."
                : "An alert is raised when a new signal matches an enabled rule below. With no signals, or no matching rule, there is nothing to alert on."}
            </EmptyState>
          ) : (
            <>
              <DataTable
                caption="Alerts"
                rows={alerts.data.items}
                rowKey={(a) => String(a.id)}
                columns={[
                  { key: "title", header: "Alert", priority: 1, render: (a) => <Link to={`/signals/${a.signal_id}`}>{a.title}</Link> },
                  { key: "sev", header: "Severity", priority: 2, render: (a) => <SeverityPill severity={a.severity} /> },
                  { key: "state", header: "State", priority: 2, render: (a) => (a.read_at ? <Pill tone="idle">read</Pill> : <Pill tone="warn">unread</Pill>) },
                  { key: "at", header: "Raised", priority: 2, render: (a) => fmtTime(a.created_at) },
                  { key: "read", header: "Read at", priority: 3, render: (a) => fmtTime(a.read_at) },
                  { key: "delivery", header: "Delivery", priority: 3, render: (a) => humanLabel(a.delivery) },
                  {
                    key: "act", header: "", priority: 2, render: (a) =>
                      a.read_at ? null : (
                        <button type="button" className="btn btn-small" aria-label={`Mark alert ${a.id} as read`} onClick={() => markOne(a)} disabled={busy || !canRead.allowed} title={canRead.reason}>
                          Mark read
                        </button>
                      ),
                  },
                ]}
              />
              <div className="row pager">
                <button type="button" className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
                <span className="muted">{offset + 1} to {offset + alerts.data.items.length} of {fmtInt(alerts.data.total)}</span>
                <button type="button" className="btn" disabled={offset + alerts.data.items.length >= alerts.data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
              </div>
            </>
          )}
        </>
      ) : null}
    </Panel>
  );
}

function CreateRule({ families, onCreated }: { families: string[]; onCreated: () => void }) {
  const can = useCan("POST", "/alert-rules");
  const [name, setName] = useState("");
  const [severity, setSeverity] = useState<Severity>("high");
  const [minScore, setMinScore] = useState("0");
  const [picked, setPicked] = useState<string[]>([]);
  const [chains, setChains] = useState<string[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!can.allowed) return;
    const score = Number(minScore);
    if (!name.trim()) return setError("Give the rule a name.");
    if (minScore.trim() === "" || !Number.isFinite(score) || score < 0 || score > 100) return setError("The minimum score must be a number from 0 to 100.");
    setBusy(true);
    setError(null);
    try {
      await api.createAlertRule({ name: name.trim(), min_severity: severity, min_score: score, families: picked, chains, enabled });
      setName("");
      setMinScore("0");
      setPicked([]);
      setChains([]);
      onCreated();
    } catch (err) {
      setError(message(err, "Could not create the rule."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form" onSubmit={submit} noValidate aria-label="New alert rule">
      <h3>New rule</h3>
      <p className="muted">A rule matches new signals only. Leave the families and chains unticked to match all of them.</p>
      <div className="row">
        <label className="field grow">
          <span>Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} />
        </label>
        <label className="field">
          <span>Minimum severity</span>
          <select value={severity} onChange={(e) => setSeverity(e.target.value as Severity)}>
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
          </select>
        </label>
        <label className="field">
          <span>Minimum score</span>
          <input type="number" inputMode="decimal" min={0} max={100} value={minScore} onChange={(e) => setMinScore(e.target.value)} />
        </label>
      </div>
      <fieldset className="probe-select">
        <legend>Chains</legend>
        {CHAINS.map((c) => (
          <label key={c.id} className="check">
            <input type="checkbox" checked={chains.includes(c.id)} onChange={() => setChains(toggle(chains, c.id))} />
            {c.label}
          </label>
        ))}
      </fieldset>
      <fieldset className="probe-select">
        <legend>Signal families</legend>
        {families.length === 0 ? <span className="muted">The detector list is not available, so all families will match.</span> : null}
        {families.map((f) => (
          <label key={f} className="check">
            <input type="checkbox" checked={picked.includes(f)} onChange={() => setPicked(toggle(picked, f))} />
            {humanLabel(f)}
          </label>
        ))}
      </fieldset>
      <label className="check">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Enabled
      </label>
      <FieldError id="rule-error" message={error} />
      <RoleHint can={can} />
      <button type="submit" className="btn btn-primary" disabled={busy || !can.allowed} title={can.reason}>Create rule</button>
    </form>
  );
}

function Rules() {
  const rules = useApi(api.alertRules);
  const catalogue = useApi(api.detectors);
  const canEdit = useCan("PATCH", "/alert-rules/1");
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const families = catalogue.status === "ready" ? [...new Set(catalogue.data.detectors.map((d) => d.family))].sort() : [];

  const toggle = async (r: AlertRule) => {
    setBusyId(r.id);
    setError(null);
    try {
      await api.setAlertRuleEnabled(r.id, !r.enabled);
      rules.reload();
    } catch (err) {
      setError(message(err, "Could not change the rule."));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Panel title="Alert rules">
      {rules.status === "loading" ? <Loading /> : null}
      {rules.status === "error" ? <ErrorState error={rules.error} onRetry={rules.reload} /> : null}
      {rules.status === "ready" ? (
        rules.data.items.length === 0 ? (
          <EmptyState title="No alert rules">Without an enabled rule no alert is ever raised. Create one below.</EmptyState>
        ) : (
          <DataTable
            caption="Alert rules"
            rows={rules.data.items}
            rowKey={(r) => String(r.id)}
            columns={[
              { key: "name", header: "Rule", priority: 1, render: (r) => r.name },
              { key: "on", header: "State", priority: 2, render: (r) => (r.enabled ? <Pill tone="ok">enabled</Pill> : <Pill tone="idle">disabled</Pill>) },
              { key: "sev", header: "Minimum severity", priority: 2, render: (r) => r.min_severity },
              { key: "score", header: "Minimum score", priority: 2, numeric: true, render: (r) => r.min_score },
              { key: "fam", header: "Families", priority: 3, render: (r) => (r.families.length ? r.families.map(humanLabel).join(", ") : "all") },
              { key: "chains", header: "Chains", priority: 3, render: (r) => (r.chains.length ? r.chains.map(chainLabel).join(", ") : "all") },
              { key: "at", header: "Created", priority: 3, render: (r) => fmtTime(r.created_at) },
              {
                key: "act", header: "", priority: 2, render: (r) => (
                  <button type="button" className="btn btn-small" onClick={() => void toggle(r)} disabled={busyId === r.id || !canEdit.allowed} title={canEdit.reason} aria-label={`${r.enabled ? "Disable" : "Enable"} rule ${r.name}`}>
                    {r.enabled ? "Disable" : "Enable"}
                  </button>
                ),
              },
            ]}
          />
        )
      ) : null}
      <RoleHint can={canEdit} />
      <FieldError id="rules-error" message={error} />
      <CreateRule families={families} onCreated={rules.reload} />
    </Panel>
  );
}

function Channels() {
  const channels = useApi(api.alertChannels);
  return (
    <Panel title="Delivery channels">
      {channels.status === "loading" ? <Loading /> : null}
      {channels.status === "error" ? <ErrorState error={channels.error} onRetry={channels.reload} /> : null}
      {channels.status === "ready" ? (
        <>
          <p>
            Delivered to: {channels.data.available.length ? channels.data.available.map(humanLabel).join(", ") : "nothing"}.
          </p>
          <ul className="list">
            {Object.entries(channels.data.unavailable).map(([name, why]) => (
              <li key={name}>
                <strong>{name}</strong> <span className="muted">{why}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </Panel>
  );
}

export default function Alerts() {
  return (
    <>
      <PageHeader title="Alerts" lede="Notices raised when a new signal matches an enabled rule. Reading an alert changes nothing about the signal." />
      <div className="stack disc">
        <AlertList />
        <Rules />
        <Channels />
      </div>
    </>
  );
}
