import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { ContractInfo, ExplorerTxs, SourceRecord } from "../api/types";
import { RoleHint } from "../auth/RoleHint";
import { useCan } from "../auth/useAuth";
import { DataTable } from "../components/DataTable";
import { ExternalLink } from "../components/ExternalLink";
import { ErrorState, Loading, PageHeader, Panel, Pill } from "../components/ui";
import { fmtTime } from "../lib/format";
import { useApi } from "../lib/useApi";

const errText = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);
const NOTE = "Provider-reported: what the provider answered, not independently verified by BIP.";

function Integrations() {
  const status = useApi(api.sources);
  const can = useCan("POST", "/sources/coingecko/test");
  const [results, setResults] = useState<Record<string, string>>({});
  const test = async (id: string) => {
    try {
      const r = await api.testSource(`/sources/${id}/test`);
      setResults((m) => ({ ...m, [id]: r.message ?? (r.ok ? "Connected." : "Check failed.") }));
    } catch (err) {
      setResults((m) => ({ ...m, [id]: errText(err, "The check could not run.") }));
    }
    status.reload();
  };
  return (
    <Panel title="Integrations" aside={<RoleHint can={can} />}>
      <p className="muted">
        Keys are entered on the <Link to="/setup">Setup</Link> page. Accounts and plans are yours to create; BIP never signs up for
        anything. Each integration stops for the day at its request cap.
      </p>
      {status.status === "loading" ? <Loading /> : null}
      {status.status === "error" ? <ErrorState error={status.error} onRetry={status.reload} /> : null}
      {status.status === "ready" ? (
        <DataTable
          caption="Keyed integrations"
          rows={status.data.items}
          rowKey={(s) => s.provider_id}
          empty="No integrations."
          columns={[
            { key: "name", header: "Integration", priority: 1,
              render: (s) => (
                <>
                  <strong>{s.name}</strong>{" "}
                  {s.requires_subscription ? <Pill tone="warn">Needs a paid plan</Pill> : null}
                  <div className="muted"><ExternalLink url={s.docs_url} text="Docs" /> · <ExternalLink url={s.terms_url} text="Terms" /></div>
                </>
              ) },
            { key: "state", header: "Set up", priority: 1,
              render: (s) => <Pill tone={s.configured ? "ok" : "idle"}>{s.configured ? (s.key_set ? "key set" : "keyless") : "needs a key"}</Pill> },
            { key: "usage", header: "Today", priority: 2, render: (s) => `${s.requests_today} / ${s.daily_cap}` },
            { key: "last", header: "Last call", priority: 3, render: (s) => (s.last_outcome ? `${s.last_outcome}${s.last_check_at ? ` · ${fmtTime(s.last_check_at)}` : ""}` : "never") },
            { key: "test", header: "Check", priority: 1,
              render: (s) => (
                <>
                  <button type="button" className="btn btn-ghost" disabled={!can.allowed || !s.configured} onClick={() => void test(s.provider_id)}>
                    Test
                  </button>
                  {results[s.provider_id] ? <div role="status">{results[s.provider_id]}</div> : null}
                </>
              ) },
          ]}
        />
      ) : null}
    </Panel>
  );
}

function PricesPanel() {
  const prices = useApi(api.prices);
  const can = useCan("POST", "/sources/prices/refresh");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const refresh = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.refreshPrices();
      setMsg(`${r.stored} price(s) stored${r.missing ? `, ${r.missing} not available` : ""}.`);
      prices.reload();
    } catch (err) {
      setMsg(errText(err, "The refresh could not run."));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel
      title="Prices"
      aside={
        <button type="button" className="btn" disabled={!can.allowed || busy} title={can.reason} onClick={refresh}>
          {busy ? "Refreshing…" : "Refresh prices"}
        </button>
      }
    >
      {prices.status === "loading" ? <Loading /> : null}
      {prices.status === "error" ? <ErrorState error={prices.error} onRetry={prices.reload} /> : null}
      {prices.status === "ready" ? (
        <>
          <DataTable
            caption="Latest prices"
            rows={prices.data.items}
            rowKey={(p) => `${p.chain}:${p.asset}:${p.currency}`}
            empty="No prices stored yet."
            columns={[
              { key: "asset", header: "Asset", priority: 1, render: (p) => <span className="mono wrap-any">{p.chain} · {p.asset}</span> },
              { key: "price", header: "Price", priority: 1, render: (p) => `${p.price} ${p.currency.toUpperCase()}` },
              { key: "as_of", header: "Provider time", priority: 2, render: (p) => (p.provider_updated_at ? fmtTime(p.provider_updated_at) : "not stated") },
              { key: "fetched", header: "Fetched", priority: 3, render: (p) => fmtTime(p.fetched_at) },
            ]}
          />
          <p className="muted">{prices.data.attribution ?? ""}. {NOTE}</p>
        </>
      ) : null}
      {msg ? <p role="status">{msg}</p> : null}
    </Panel>
  );
}

function EtherscanPanel() {
  const can = useCan("POST", "/sources/etherscan/contract/x");
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState(false);
  const [contract, setContract] = useState<ContractInfo | null>(null);
  const [txs, setTxs] = useState<ExplorerTxs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async (what: "contract" | "normal" | "internal", e?: FormEvent) => {
    e?.preventDefault();
    if (!address.trim()) return;
    setBusy(true);
    setError(null);
    try {
      if (what === "contract") {
        setContract(await api.etherscanContract(address.trim()));
        setTxs(null);
      } else {
        setTxs(await api.etherscanTransactions(address.trim(), what));
        setContract(null);
      }
    } catch (err) {
      setError(errText(err, "The lookup could not run."));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel title="Etherscan lookup" aside={<RoleHint can={can} />}>
      <form className="setup-form" onSubmit={(e) => void run("contract", e)}>
        <input aria-label="Ethereum address" placeholder="0x…" spellCheck={false} value={address} onChange={(e) => setAddress(e.target.value)} />
        <button type="submit" className="btn" disabled={!can.allowed || busy}>Contract info</button>
        <button type="button" className="btn btn-ghost" disabled={!can.allowed || busy} onClick={() => void run("normal")}>Transactions</button>
        <button type="button" className="btn btn-ghost" disabled={!can.allowed || busy} onClick={() => void run("internal")}>Internal transfers</button>
      </form>
      {error ? <p role="alert" className="setup-bad">{error}</p> : null}
      {contract ? (
        <dl className="kv">
          <dt>Verified source</dt><dd><Pill tone={contract.verified ? "ok" : "idle"}>{contract.verified ? "verified" : "not verified"}</Pill></dd>
          <dt>Name</dt><dd>{contract.name ?? "—"}</dd>
          <dt>Creator</dt><dd className="mono wrap-any">{contract.creator ?? "—"}</dd>
          <dt>Creation tx</dt><dd className="mono wrap-any">{contract.creation_tx ?? "—"}</dd>
          <dt>Proxy</dt><dd>{contract.proxy ? `yes → ${contract.implementation ?? "unknown"}` : "no"}</dd>
        </dl>
      ) : null}
      {txs ? (
        <DataTable
          caption={txs.kind === "internal" ? "Internal transfers" : "Transactions"}
          rows={txs.items}
          rowKey={(t) => `${t.tx_id ?? ""}:${t.from ?? ""}:${t.to ?? ""}:${t.value_wei ?? ""}`}
          empty="None found."
          columns={[
            { key: "tx", header: "Transaction", priority: 1, render: (t) => <span className="mono wrap-any">{t.tx_id ?? "—"}</span> },
            { key: "value", header: "Value (wei)", priority: 1, render: (t) => t.value_wei ?? "—" },
            { key: "dir", header: "From → to", priority: 2, render: (t) => <span className="mono wrap-any">{t.from ?? "?"} → {t.to ?? "?"}</span> },
            { key: "block", header: "Block", priority: 3, render: (t) => (t.block_number === null ? "—" : String(t.block_number)) },
          ]}
        />
      ) : null}
      {contract || txs ? <p className="muted">Powered by Etherscan.io APIs. {NOTE}</p> : null}
    </Panel>
  );
}

function DunePanel() {
  const records = useApi(() => api.sourceRecords("dune", "query_result"), []);
  const can = useCan("POST", "/sources/dune/refresh");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const refresh = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.refreshDune();
      setMsg(r.queries.map((q) => `${q.query_id}: ${q.ok ? `${q.rows} row(s)` : q.error ?? "failed"}`).join("; "));
      records.reload();
    } catch (err) {
      setMsg(errText(err, "The refresh could not run."));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel
      title="Dune query results"
      aside={<button type="button" className="btn" disabled={!can.allowed || busy} title={can.reason} onClick={refresh}>{busy ? "Fetching…" : "Fetch results"}</button>}
    >
      <p className="muted">Dune has no free API tier; add your key and saved query ids on the Setup page.</p>
      {records.status === "loading" ? <Loading /> : null}
      {records.status === "error" ? <ErrorState error={records.error} onRetry={records.reload} /> : null}
      {records.status === "ready" && records.data.items.length === 0 ? <p>No query results stored yet.</p> : null}
      {records.status === "ready" ? records.data.items.map((r) => <DuneResult key={r.subject} record={r} />) : null}
      {msg ? <p role="status">{msg}</p> : null}
    </Panel>
  );
}

function DuneResult({ record }: { record: SourceRecord }) {
  const payload = record.payload as { columns: string[] | null; rows: Record<string, unknown>[]; execution_ended_at: string | null };
  const columns = payload.columns ?? Object.keys(payload.rows[0] ?? {});
  const rows = payload.rows.slice(0, 50).map((row, i) => ({ ...row, __i: i }));
  return (
    <section className="stack" aria-label={`Dune query ${record.subject}`}>
      <h3>Query {record.subject}</h3>
      <p className="muted">Executed {payload.execution_ended_at ?? "at an unknown time"}; fetched {fmtTime(record.fetched_at)}. Showing up to 50 rows.</p>
      <DataTable
        caption={`Dune query ${record.subject}`}
        rows={rows}
        rowKey={(r) => String(r.__i)}
        empty="No rows."
        columns={columns.slice(0, 8).map((c, i) => ({ key: c, header: c, priority: i < 3 ? 1 : 2, render: (r: Record<string, unknown>) => String(r[c] ?? "") }))}
      />
    </section>
  );
}

export default function Sources() {
  return (
    <>
      <PageHeader
        title="Data sources"
        lede="Prices, explorer lookups and query results from the keyed providers you set up. Everything here is what a provider reported, not independently verified."
      />
      <div className="stack">
        <Integrations />
        <PricesPanel />
        <EtherscanPanel />
        <DunePanel />
      </div>
    </>
  );
}
