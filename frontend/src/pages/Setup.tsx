import { useState, type FormEvent } from "react";
import { ApiError, api } from "../api/client";
import type { SetupOverview, SetupSecret, SetupSetting, SetupValue } from "../api/types";
import { RoleHint } from "../auth/RoleHint";
import { useCan } from "../auth/useAuth";
import { ExternalLink } from "../components/ExternalLink";
import { ErrorState, Loading, PageHeader, Panel, Pill } from "../components/ui";
import { useApi } from "../lib/useApi";

const GROUP_ORDER = ["Data", "Alerts", "AI research", "Background jobs"];

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function sourceLabel(source: SetupSecret["source"] | SetupSetting["source"]): string {
  if (source === "environment") return "set in the server environment";
  if (source === "setup") return "saved here";
  if (source === "default") return "default";
  return "not set";
}

function showValue(v: SetupValue): string {
  if (v === null) return "not set";
  if (typeof v === "boolean") return v ? "on" : "off";
  if (Array.isArray(v)) return v.length ? `${v.length} entr${v.length === 1 ? "y" : "ies"}` : "none";
  return String(v);
}

/** One write-only credential: the value is typed, sent once and never shown again. */
function SecretRow({ s, onChanged, canEdit }: { s: SetupSecret; onChanged: () => void; canEdit: boolean }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const inputId = `secret-${s.name}`;
  const fromEnv = s.source === "environment";

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!value.trim()) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.setSetupSecret(s.name, value);
      setValue("");
      setMessage({ tone: "ok", text: s.applies === "immediately" ? "Saved. In use now." : "Saved. Applies after a restart." });
      onChanged();
    } catch (err) {
      setMessage({ tone: "bad", text: errorText(err, "Could not save.") });
    } finally {
      setBusy(false);
    }
  };
  const clear = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await api.clearSetupSecret(s.name);
      setMessage({ tone: "ok", text: "Removed." });
      onChanged();
    } catch (err) {
      setMessage({ tone: "bad", text: errorText(err, "Could not remove.") });
    } finally {
      setBusy(false);
    }
  };
  const check = async () => {
    if (!s.check_path) return;
    setBusy(true);
    setMessage(null);
    try {
      const r = await api.testSource(s.check_path);
      const text = r.ok
        ? r.message ?? `Connected (${r.latency_ms ?? "?"} ms).`
        : r.message ?? r.detail ?? `Check failed: ${r.outcome}.`;
      setMessage({ tone: r.ok ? "ok" : "bad", text });
    } catch (err) {
      setMessage({ tone: "bad", text: errorText(err, "The check could not run.") });
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="setup-row" data-testid={`secret-${s.name}`}>
      <div className="setup-row-head">
        <label htmlFor={inputId}>
          <strong>{s.label}</strong>
        </label>
        <Pill tone={s.set ? "ok" : "idle"}>{s.set ? `Set (${sourceLabel(s.source)})` : "Not set"}</Pill>
        {s.requires_subscription ? <Pill tone="warn">Needs a paid plan</Pill> : null}
        {s.chains.length ? <span className="muted">{s.chains.join(", ")}</span> : null}
        {s.access ? <span className="muted">{s.access.replace(/_/g, " ")}</span> : null}
      </div>
      <p className="muted setup-help">
        {s.help} {s.applies === "after_restart" ? "Applies after a restart." : "Applies immediately."}{" "}
        {s.docs_url ? <ExternalLink url={s.docs_url} text="Get a key" /> : null}
        {s.terms_url ? (
          <>
            {" · "}
            <ExternalLink url={s.terms_url} text="Terms" />
          </>
        ) : null}
      </p>
      {fromEnv ? (
        <p className="note">Set in the server environment ({s.name}); change it there.</p>
      ) : (
        <form className="setup-form" onSubmit={save}>
          <input
            id={inputId}
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder={s.set ? "Saved — type a new value to replace it" : "Paste the value"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            disabled={!canEdit || busy}
          />
          <button type="submit" className="btn" disabled={!canEdit || busy || !value.trim()}>
            Save
          </button>
          {s.source === "setup" ? (
            <button type="button" className="btn btn-ghost" disabled={!canEdit || busy} onClick={clear}>
              Remove
            </button>
          ) : null}
        </form>
      )}
      {s.checkable && s.set ? (
        <button type="button" className="btn btn-ghost" disabled={busy} onClick={check}>
          Check connection
        </button>
      ) : null}
      {message ? (
        <p role="status" className={message.tone === "ok" ? "setup-ok" : "setup-bad"}>
          {message.text}
        </p>
      ) : null}
    </li>
  );
}

function SettingEditor({ s, draft, setDraft, disabled }: { s: SetupSetting; draft: SetupValue; setDraft: (v: SetupValue) => void; disabled: boolean }) {
  const id = `setting-${s.key}`;
  if (s.kind === "bool")
    return (
      <label className="row-inline" htmlFor={id}>
        <input id={id} type="checkbox" checked={draft === true} disabled={disabled} onChange={(e) => setDraft(e.target.checked)} />
        {draft === true ? "On" : "Off"}
      </label>
    );
  if (s.kind === "choice")
    return (
      <select id={id} value={typeof draft === "string" ? draft : ""} disabled={disabled} onChange={(e) => setDraft(e.target.value || null)}>
        <option value="">(not set)</option>
        {s.choices.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    );
  if (s.kind === "int")
    return (
      <input id={id} type="number" value={typeof draft === "number" ? draft : ""} disabled={disabled}
        onChange={(e) => setDraft(e.target.value === "" ? null : Number(e.target.value))} />
    );
  if (s.kind === "list")
    return (
      <textarea id={id} rows={3} spellCheck={false} disabled={disabled} value={Array.isArray(draft) ? draft.join("\n") : ""}
        onChange={(e) => setDraft(e.target.value.split("\n"))} />
    );
  return <input id={id} type="text" spellCheck={false} value={typeof draft === "string" ? draft : ""} disabled={disabled} onChange={(e) => setDraft(e.target.value)} />;
}

function SettingRow({ s, onChanged, canEdit }: { s: SetupSetting; onChanged: () => void; canEdit: boolean }) {
  const initial = s.stored ?? s.value;
  const [draft, setDraft] = useState<SetupValue>(initial);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const fromEnv = s.source === "environment";

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    const value = Array.isArray(draft) ? draft.map((v) => v.trim()).filter(Boolean) : draft;
    try {
      await api.setSetupSetting(s.key, value);
      setMessage({ tone: "ok", text: "Saved. Applies after a restart." });
      onChanged();
    } catch (err) {
      setMessage({ tone: "bad", text: errorText(err, "Could not save.") });
    } finally {
      setBusy(false);
    }
  };
  const reset = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await api.clearSetupSetting(s.key);
      setDraft(s.value);
      setMessage({ tone: "ok", text: "Reset. Applies after a restart." });
      onChanged();
    } catch (err) {
      setMessage({ tone: "bad", text: errorText(err, "Could not reset.") });
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="setup-row" data-testid={`setting-${s.key}`}>
      <div className="setup-row-head">
        <label htmlFor={`setting-${s.key}`}>
          <strong>{s.label}</strong>
        </label>
        {s.contacts_providers ? <Pill tone="warn">Uses provider quota</Pill> : null}
        <span className="muted">
          Now: {showValue(s.value)} ({sourceLabel(s.source)})
          {s.stored !== null && s.source === "setup" ? ` · saved: ${showValue(s.stored)}` : ""}
        </span>
      </div>
      <p className="muted setup-help">{s.help}</p>
      {fromEnv ? (
        <p className="note">Set in the server environment; change it there.</p>
      ) : (
        <form className="setup-form" onSubmit={save}>
          <SettingEditor s={s} draft={draft} setDraft={setDraft} disabled={!canEdit || busy} />
          <button type="submit" className="btn" disabled={!canEdit || busy}>
            Save
          </button>
          {s.source === "setup" ? (
            <button type="button" className="btn btn-ghost" disabled={!canEdit || busy} onClick={reset}>
              Reset to default
            </button>
          ) : null}
        </form>
      )}
      {message ? (
        <p role="status" className={message.tone === "ok" ? "setup-ok" : "setup-bad"}>
          {message.text}
        </p>
      ) : null}
    </li>
  );
}

function RestartBanner({ data, onRestarted }: { data: SetupOverview; onRestarted: () => void }) {
  const [state, setState] = useState<"idle" | "restarting" | "failed">("idle");
  const [error, setError] = useState<string | null>(null);
  const pending = data.restart.pending;
  if (pending.length === 0 && state === "idle") return null;

  const restart = async () => {
    setState("restarting");
    setError(null);
    try {
      await api.restartApi();
    } catch (err) {
      setState("failed");
      setError(errorText(err, "The restart could not be requested."));
      return;
    }
    // Wait for the API to stop and come back (up to a minute), then reload the page data.
    for (let i = 0; i < 60; i += 1) {
      await new Promise((r) => setTimeout(r, 1000));
      try {
        await api.setup();
        if (i > 0) break;
      } catch {
        /* still restarting */
      }
    }
    setState("idle");
    onRestarted();
  };

  return (
    <Panel title="Restart needed">
      <p>
        {pending.length} saved change{pending.length === 1 ? "" : "s"} appl{pending.length === 1 ? "ies" : "y"} after a restart:{" "}
        <span className="mono">{pending.join(", ")}</span>
      </p>
      {data.restart.available ? (
        <button type="button" className="btn" disabled={state === "restarting"} onClick={restart}>
          {state === "restarting" ? "Restarting…" : "Restart the API now"}
        </button>
      ) : (
        <p className="note">Restart the API the way it was started (for example `make api` or `systemctl restart bip-api`).</p>
      )}
      {error ? (
        <p role="alert" className="setup-bad">
          {error}
        </p>
      ) : null}
    </Panel>
  );
}

function TelegramTest() {
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const r = await api.testTelegram();
      setResult(r.status === "delivered" ? "Test message delivered." : `Not delivered: ${r.error ?? r.status}`);
    } catch (err) {
      setResult(errorText(err, "The test could not run."));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="setup-row">
      <button type="button" className="btn btn-ghost" disabled={busy} onClick={run}>
        Send a test message
      </button>
      {result ? <p role="status">{result}</p> : null}
    </div>
  );
}

export default function Setup() {
  const setup = useApi(api.setup);
  const can = useCan("POST", "/setup/secrets/x");
  const [filter, setFilter] = useState("");

  return (
    <>
      <PageHeader
        title="Setup"
        lede="Connect data providers, alerts and AI, and choose which background jobs run. Keys are stored on the server in an owner-only file and are never shown again."
      />
      <RoleHint can={can} />
      {setup.status === "loading" ? <Loading /> : null}
      {setup.status === "error" ? <ErrorState error={setup.error} onRetry={setup.reload} /> : null}
      {setup.status === "ready" ? (
        <>
          <RestartBanner data={setup.data} onRestarted={setup.reload} />
          {setup.data.startup.overrides_error ? (
            <Panel title="Saved settings were not applied at start">
              <p role="alert" className="setup-bad">
                {setup.data.startup.overrides_error}
              </p>
              <p className="note">Fix or reset the setting below; the API started with its other configuration.</p>
            </Panel>
          ) : null}

          <Panel
            title="Data providers"
            aside={
              <input
                type="search"
                aria-label="Filter providers"
                placeholder="Filter providers"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
            }
          >
            <p className="muted">
              Public endpoints work without keys. Add free-tier keys for more capacity and a second, independent
              provider per chain for cross-checking. Accounts are yours to create.
            </p>
            <ul className="setup-list">
              {setup.data.secrets
                .filter((s) => s.group === "Providers")
                .filter((s) => `${s.label} ${s.chains.join(" ")}`.toLowerCase().includes(filter.toLowerCase()))
                .map((s) => (
                  <SecretRow key={s.name} s={s} onChanged={setup.reload} canEdit={can.allowed} />
                ))}
            </ul>
          </Panel>

          {GROUP_ORDER.map((group) => {
            const secrets = setup.data.secrets.filter((s) => s.group === group);
            const settings = setup.data.settings.filter((s) => s.group === group);
            if (!secrets.length && !settings.length) return null;
            return (
              <Panel key={group} title={group}>
                {group === "Background jobs" ? (
                  <p className="muted">Jobs marked “Uses provider quota” contact providers on a schedule. Leave them off until your keys are in place.</p>
                ) : null}
                <ul className="setup-list">
                  {secrets.map((s) => (
                    <SecretRow key={s.name} s={s} onChanged={setup.reload} canEdit={can.allowed} />
                  ))}
                  {settings.map((s) => (
                    <SettingRow key={`${s.key}:${s.updated_at ?? ""}`} s={s} onChanged={setup.reload} canEdit={can.allowed} />
                  ))}
                </ul>
                {group === "Alerts" ? <TelegramTest /> : null}
              </Panel>
            );
          })}
        </>
      ) : null}
    </>
  );
}
