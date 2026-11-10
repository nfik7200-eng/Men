import { RoleHint } from "../../auth/RoleHint";
import { useCan } from "../../auth/useAuth";
import { useId, useState, type FormEvent } from "react";
import { Link, NavLink, useParams } from "react-router-dom";
import { ApiError, api } from "../../api/client";
import type { WalletStatus } from "../../api/types";
import { Address, shortId } from "../../components/Address";
import { DataOriginBanner } from "../../components/intel";
import { EmptyState, ErrorState, Loading, PageHeader, Panel, Pill } from "../../components/ui";
import { chainLabel } from "../../lib/address";
import { addressUrl } from "../../lib/explorer";
import { fmtCount, fmtTime, humanize } from "../../lib/format";
import { useApi } from "../../lib/useApi";
import { FlowsView } from "../CapitalFlows";
import { GraphView } from "../Graph";
import { ClassificationPanel, Investigation } from "../Research";
import WalletIntel from "../WalletIntel";
import { IngestPanel, WalletActivity } from "../Wallets";

export const TABS = [
  ["overview", "Overview"],
  ["activity", "Activity"],
  ["flows", "Flows"],
  ["graph", "Graph"],
  ["classification", "Classification"],
  ["research", "Research"],
  ["cases", "Cases"],
  ["evidence", "Evidence"],
] as const;
type Tab = (typeof TABS)[number][0];
const NEEDS_DATA: ReadonlySet<Tab> = new Set(["flows", "graph", "classification", "evidence"]);

/** The wallet workspace: one place for activity, intelligence, classification, research and cases. */
export default function WalletWorkspacePage() {
  const { chain = "", address = "", tab = "overview" } = useParams();
  const [nonce, setNonce] = useState(0);
  // reload() keeps the current status on screen while refetching, so tabs stay mounted
  // (no flash, input and panel state survive); nonce only refreshes the Evidence tab.
  const status = useApi(() => api.walletStatus(chain, address), [chain, address]);
  const refresh = () => {
    status.reload();
    setNonce((n) => n + 1);
  };
  const base = `/wallets/${encodeURIComponent(chain)}/${encodeURIComponent(address)}`;
  const explorer = addressUrl(chain, address);
  const known = TABS.some(([id]) => id === tab);

  return (
    <>
      <PageHeader
        title="Wallet"
        docTitle={`${shortId(address)} · ${chainLabel(chain)}`}
        actions={explorer ? <a className="btn" href={explorer} target="_blank" rel="noopener noreferrer">Explorer</a> : undefined}
      />
      <div className="wallet-id">
        <Pill tone="info">{chainLabel(chain)}</Pill>
        <Address value={status.status === "ready" ? status.data.address : address} />
      </div>
      {status.status === "ready" ? <CoverageLine s={status.data} /> : null}
      {status.status === "ready" ? (
        <DataOriginBanner origin={status.data.data_origin === "offline_fixture" ? "offline_fixture" : "stored_observations"} />
      ) : null}
      <nav className="workspace-tabs" aria-label="Wallet sections">
        {TABS.map(([id, label]) => (
          <NavLink key={id} to={id === "overview" ? base : `${base}/${id}`} end className="workspace-tab">
            {label}
          </NavLink>
        ))}
      </nav>
      {status.status === "loading" ? <Loading label="Loading wallet…" /> : null}
      {status.status === "error" ? <ErrorState error={status.error} onRetry={status.reload} /> : null}
      {status.status === "ready" && !known ? <EmptyState title="Unknown section">Pick a section above.</EmptyState> : null}
      {status.status === "ready" && known ? (
        <TabContent tab={tab as Tab} s={status.data} base={base} onChange={refresh} nonce={nonce} />
      ) : null}
    </>
  );
}

function CoverageLine({ s }: { s: WalletStatus }) {
  if (!s.stored) return <p className="coverage muted">Nothing stored yet. Opening a wallet never retrieves data by itself.</p>;
  return (
    <p className="coverage">
      Stored: {fmtCount(s.transaction_count)} transactions, {fmtCount(s.transfer_count)} transfers · observed{" "}
      {fmtTime(s.first_observed_at)} → {fmtTime(s.last_observed_at)} · last retrieval {fmtTime(s.last_ingested_at)}
    </p>
  );
}

function NotStored({ s, onChange, base, showRetrieve, retrieveAbove = false }: {
  s: WalletStatus;
  onChange: () => void;
  base: string;
  showRetrieve: boolean;
  retrieveAbove?: boolean;
}) {
  return (
    <>
      <EmptyState title="Nothing has been retrieved for this address yet">
        The address is valid, but BIP holds no observations for it. Retrieval is a bounded, read-only run
        through the provider router. An empty result after a run means no activity in the scanned window, not
        that the wallet was never used.
        {showRetrieve || retrieveAbove ? null : (
          <div className="hint">
            <Link to={`${base}/activity`}>Go to Activity to retrieve it</Link>
          </div>
        )}
      </EmptyState>
      {showRetrieve ? <IngestPanel chain={s.chain} address={s.address} onDone={onChange} /> : null}
    </>
  );
}

function TabContent({ tab, s, base, onChange, nonce }: { tab: Tab; s: WalletStatus; base: string; onChange: () => void; nonce: number }) {
  if (tab === "overview")
    // The retrieval panel stays mounted in the same place, so its run result survives the refresh
    // that turns an unstored wallet into a stored one.
    return (
      <>
        <IngestPanel chain={s.chain} address={s.address} onDone={onChange} />
        {s.stored ? <OverviewTab s={s} base={base} /> : <NotStored s={s} onChange={onChange} base={base} showRetrieve={false} retrieveAbove />}
      </>
    );
  if (!s.stored && NEEDS_DATA.has(tab)) return <NotStored s={s} onChange={onChange} base={base} showRetrieve={false} />;
  switch (tab) {
    case "activity":
      return <WalletActivity chain={s.chain} address={s.address} onIngested={onChange} />;
    case "flows":
      return <FlowsView chain={s.chain} address={s.address} />;
    case "graph":
      return <GraphTab chain={s.chain} address={s.address} />;
    case "classification":
      return <ClassificationPanel chain={s.chain} address={s.address} />;
    case "research":
      return <ResearchTab s={s} />;
    case "cases":
      return <CasesTab s={s} onChange={onChange} />;
    case "evidence":
      return <WalletIntel chain={s.chain} address={s.address} refreshKey={nonce} showOrigin={false} />;
  }
}

function OverviewTab({ s, base }: { s: WalletStatus; base: string }) {
  const cls = useApi(() => api.classifications(s.chain, s.address), [s.chain, s.address]);
  const runs = useApi(() => api.researchHistory({ chain: s.chain, address: s.address, limit: 1 }), [s.chain, s.address]);
  const latest = cls.status === "ready" ? cls.data.items[0] : undefined;
  const run = runs.status === "ready" ? runs.data.items[0] : undefined;
  return (
    <div className="grid grid-2">
      <Panel title="Coverage">
        <dl className="kv">
          <dt>Transactions stored</dt>
          <dd className="num">{fmtCount(s.transaction_count)}</dd>
          <dt>Transfers stored</dt>
          <dd className="num">{fmtCount(s.transfer_count)}</dd>
          <dt>Observed window</dt>
          <dd>{fmtTime(s.first_observed_at)} → {fmtTime(s.last_observed_at)}</dd>
          <dt>Last retrieval</dt>
          <dd>{fmtTime(s.last_ingested_at)}</dd>
        </dl>
        <p className="note">Describes the retrieved dataset only, not lifetime history. <Link to={`${base}/activity`}>Activity</Link></p>
      </Panel>
      <Panel title="Latest classification">
        {cls.status === "loading" ? <Loading /> : null}
        {cls.status === "error" ? <ErrorState error={cls.error} onRetry={cls.reload} /> : null}
        {cls.status === "ready" && !latest ? <p className="muted">Not classified yet.</p> : null}
        {latest ? (
          <dl className="kv">
            <dt>Account type</dt>
            <dd>{humanize(latest.account_type.label)} ({humanize(latest.account_type.status)})</dd>
            <dt>Roles</dt>
            <dd>{latest.behavioural_roles.map((r) => humanize(r.label)).join(", ") || "none assigned"}</dd>
            <dt>Source</dt>
            <dd>{latest.model ? `${latest.model.name} ${latest.model.version} (${humanize(latest.model.status)})` : "rules baseline only"}</dd>
            <dt>Classified</dt>
            <dd>{fmtTime(latest.classification_timestamp)}</dd>
          </dl>
        ) : null}
        <p className="note"><Link to={`${base}/classification`}>Classification</Link></p>
      </Panel>
      <Panel title="Latest research">
        {runs.status === "ready" && !run ? <p className="muted">No research run for this wallet yet.</p> : null}
        {run ? (
          <p>
            <Link to={`/research/runs/${encodeURIComponent(run.run_id)}`}>{run.question}</Link>{" "}
            <Pill tone={run.state.startsWith("completed") ? "ok" : run.state === "failed" ? "bad" : "info"}>{humanize(run.state)}</Pill>
          </p>
        ) : null}
        <p className="note"><Link to={`${base}/research`}>Research</Link></p>
      </Panel>
      <Panel title="Cases and watchlists">
        <Memberships s={s} />
        <p className="note"><Link to={`${base}/cases`}>Cases</Link></p>
      </Panel>
    </div>
  );
}

function Memberships({ s }: { s: WalletStatus }) {
  return (
    <dl className="kv">
      <dt>Watchlists</dt>
      <dd>
        {s.watchlists.length === 0 ? "Not on any watchlist" : s.watchlists.map((w, i) => (
          <span key={w.id}>{i ? ", " : ""}<Link to="/watchlists">{w.name}</Link></span>
        ))}
      </dd>
      <dt>Cases</dt>
      <dd>
        {s.investigations.length === 0 ? "Not the subject of any case" : s.investigations.map((c, i) => (
          <span key={c.id}>{i ? ", " : ""}<Link to={`/investigations/${c.id}`}>{c.title}</Link></span>
        ))}
      </dd>
    </dl>
  );
}

function GraphTab({ chain, address }: { chain: string; address: string }) {
  const [depth, setDepth] = useState(1);
  return (
    <>
      <label className="field depth-field">
        Depth
        <select value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
          <option value={1}>1 hop</option>
          <option value={2}>2 hops</option>
        </select>
      </label>
      <GraphView chain={chain} address={address} depth={depth} />
    </>
  );
}

function ResearchTab({ s }: { s: WalletStatus }) {
  const runs = useApi(() => api.researchHistory({ chain: s.chain, address: s.address, limit: 20 }), [s.chain, s.address]);
  return (
    <>
      <Panel title="Research history for this wallet">
        {runs.status === "loading" ? <Loading /> : null}
        {runs.status === "error" ? <ErrorState error={runs.error} onRetry={runs.reload} /> : null}
        {runs.status === "ready" && runs.data.items.length === 0 ? <p className="muted">No research runs yet. Start one below.</p> : null}
        {runs.status === "ready" && runs.data.items.length > 0 ? (
          <ul className="list">
            {runs.data.items.map((r) => (
              <li key={r.run_id}>
                <Link to={`/research/runs/${encodeURIComponent(r.run_id)}`}>{r.question}</Link>
                <Pill tone={r.state.startsWith("completed") ? "ok" : r.state === "failed" ? "bad" : "info"}>{humanize(r.state)}</Pill>
                <span className="muted">{fmtTime(r.created_at)}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </Panel>
      <Investigation chain={s.chain} address={s.address} />
    </>
  );
}

function CasesTab({ s, onChange }: { s: WalletStatus; onChange: () => void }) {
  const uid = useId();
  const lists = useApi(api.watchlists);
  const [listId, setListId] = useState("");
  const [title, setTitle] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const can = useCan("POST", "/watchlists");
  const fail = (e: unknown) => {
    setError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Request failed."));
    setBusy(false);
  };
  const addToList = (e: FormEvent) => {
    e.preventDefault();
    if (!listId) return;
    setBusy(true);
    setError(null);
    api.addWatchlistEntry(Number(listId), s.chain, s.address, null).then(() => { setBusy(false); onChange(); }, fail);
  };
  const openCase = (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    setError(null);
    api.createInvestigation({ title: title.trim(), description: null, subject_chain: s.chain, subject_address: s.address })
      .then(() => { setBusy(false); setTitle(""); onChange(); }, fail);
  };
  return (
    <div className="grid grid-2">
      <Panel title="Watchlists and cases">
        <Memberships s={s} />
      </Panel>
      <Panel title="Add">
        {error ? <ErrorState error={error} /> : null}
        <form className="form" onSubmit={addToList}>
          <div className="field">
            <label htmlFor={`${uid}-wl`}>Watchlist</label>
            <select id={`${uid}-wl`} value={listId} onChange={(e) => setListId(e.target.value)} disabled={lists.status !== "ready"}>
              <option value="">Choose a watchlist</option>
              {lists.status === "ready" ? lists.data.map((w) => <option key={w.id} value={w.id}>{w.name}</option>) : null}
            </select>
          </div>
          {lists.status === "ready" && lists.data.length === 0 ? (
            <p className="note">No watchlists yet. <Link to="/watchlists">Create one</Link>.</p>
          ) : null}
          <button type="submit" className="btn" disabled={busy || !listId || !can.allowed} title={can.reason}>Add to watchlist</button>
        </form>
        <form className="form" onSubmit={openCase}>
          <label className="field">
            Case title
            <input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <button type="submit" className="btn" disabled={busy || !title.trim() || !can.allowed} title={can.reason}>Open case</button>
          <RoleHint can={can} />
        </form>
      </Panel>
    </div>
  );
}
