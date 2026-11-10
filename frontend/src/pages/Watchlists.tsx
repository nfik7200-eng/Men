import { useState, type FormEvent } from "react";
import { ApiError, api } from "../api/client";
import type { Watchlist } from "../api/types";
import { RoleHint } from "../auth/RoleHint";
import { useCan } from "../auth/useAuth";
import { Address } from "../components/Address";
import { DataTable } from "../components/DataTable";
import { EmptyState, ErrorState, FieldError, Loading, PageHeader, Panel, Pill } from "../components/ui";
import { CHAINS, chainLabel, validateAddress } from "../lib/address";
import { fmtTime, humanize } from "../lib/format";
import { useApi } from "../lib/useApi";

const message = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);

function AddEntry({ watchlist, onChange }: { watchlist: Watchlist; onChange: () => void }) {
  const [chain, setChain] = useState("ethereum");
  const [address, setAddress] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const can = useCan("POST", "/watchlists");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!can.allowed) return;
    const problem = validateAddress(chain, address);
    if (problem) return setError(problem);
    setBusy(true);
    setError(null);
    try {
      await api.addWatchlistEntry(watchlist.id, chain, address.trim(), note.trim() || null);
      setAddress("");
      setNote("");
      onChange();
    } catch (err) {
      setError(message(err, "Could not add the address."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="row add-entry" onSubmit={submit} noValidate aria-label="Add address">
      <label className="field">
        <span>Chain</span>
        <select id="entry-chain" value={chain} onChange={(e) => { setChain(e.target.value); setError(null); }}>
          {CHAINS.map((c) => (
            <option key={c.id} value={c.id}>{c.label}</option>
          ))}
        </select>
      </label>
      <label className="field grow">
        <span>Address</span>
        <input id="entry-address" value={address} onChange={(e) => setAddress(e.target.value)} spellCheck={false} autoComplete="off" />
      </label>
      <label className="field grow">
        <span>Note (optional)</span>
        <input id="entry-note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
      </label>
      <button type="submit" className="btn btn-primary" disabled={busy || !can.allowed} title={can.reason}>Add</button>
      <RoleHint can={can} />
      <FieldError id="entry-error" message={error} />
    </form>
  );
}

function WatchlistDetail({ id, onDeleted, onChanged }: { id: number; onDeleted: () => void; onChanged: () => void }) {
  const list = useApi(() => api.watchlist(id), [id]);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [analysis, setAnalysis] = useState<Awaited<ReturnType<typeof api.analyzeWatchlist>> | null>(null);
  const [analysing, setAnalysing] = useState(false);
  const canEdit = useCan("DELETE", "/watchlists/1");
  const canAnalyse = useCan("POST", "/intelligence/watchlists/1/analyze");
  const analyse = async () => {
    setAnalysing(true);
    setError(null);
    try {
      setAnalysis(await api.analyzeWatchlist(id));
    } catch (err) {
      setError(message(err, "Could not analyse the watchlist."));
    } finally {
      setAnalysing(false);
    }
  };

  const refresh = () => {
    list.reload();
    onChanged();
  };
  const remove = async (entryId: number) => {
    setError(null);
    try {
      await api.removeWatchlistEntry(id, entryId);
      refresh();
    } catch (err) {
      setError(message(err, "Could not remove the address."));
    }
  };
  const destroy = async () => {
    try {
      await api.deleteWatchlist(id);
      onDeleted();
    } catch (err) {
      setError(message(err, "Could not delete the watchlist."));
    }
  };

  if (list.status === "loading") return <Loading />;
  if (list.status === "error") return <ErrorState error={list.error} onRetry={list.reload} />;
  const w = list.data;
  const q = filter.trim().toLowerCase();
  const shown = q ? w.entries.filter((e) => e.address.toLowerCase().includes(q) || (e.note ?? "").toLowerCase().includes(q)) : w.entries;
  return (
    <Panel
      title={w.name}
      aside={
        confirming ? (
          <span className="row">
            <span className="muted">Delete this list and its {w.entry_count} entries?</span>
            <button type="button" className="btn btn-danger" onClick={() => void destroy()} disabled={!canEdit.allowed}>Delete</button>
            <button type="button" className="btn" onClick={() => setConfirming(false)}>Cancel</button>
          </span>
        ) : (
          <button type="button" className="btn" onClick={() => setConfirming(true)} disabled={!canEdit.allowed} title={canEdit.reason}>Delete list</button>
        )
      }
    >
      {w.description ? <p className="muted">{w.description}</p> : null}
      <AddEntry watchlist={w} onChange={refresh} />
      <FieldError id="watchlist-error" message={error} />
      {w.entries.length === 0 ? (
        <EmptyState title="No addresses on this list" />
      ) : (
        <>
          <div className="row">
            <label className="field grow">
              Filter entries
              <input type="search" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Address or note" />
            </label>
            <button type="button" className="btn" disabled={analysing || !canAnalyse.allowed} title={canAnalyse.reason} onClick={() => void analyse()}>
              {analysing ? "Analysing…" : "Analyse watchlist"}
            </button>
            <RoleHint can={canAnalyse} />
          </div>
          <p className="muted">{shown.length} of {w.entries.length} shown</p>
          {analysis ? (
            <section role="status" aria-label="Watchlist analysis" className="stack">
              <p>{summarise(analysis.items)}</p>
              {analysis.skipped > 0 ? <p className="notice">{analysis.skipped} entries were not analysed (per-request limit). Run again after reviewing these.</p> : null}
              <ul className="list">
                {analysis.items.map((r) => (
                  <li key={`${r.chain}:${r.address}`}>
                    <Address value={r.address} chain={r.chain} link copy={false} />
                    <Pill tone={r.status === "succeeded" ? "ok" : r.status === "failed" ? "bad" : "warn"}>{humanize(r.status)}</Pill>
                    {r.reused ? <span className="muted">reused earlier run</span> : null}
                  </li>
                ))}
              </ul>
              <p className="note">Analysis reads stored observations only; it makes no network requests and does not retrieve new activity.</p>
            </section>
          ) : null}
          <DataTable
            caption={`Entries of ${w.name}`}
            rows={shown}
            rowKey={(e) => String(e.id)}
            empty="No entry matches the filter."
            columns={[
              { key: "addr", header: "Address", priority: 1, render: (e) => <Address value={e.address} chain={e.chain} link /> },
              { key: "chain", header: "Chain", priority: 2, render: (e) => chainLabel(e.chain) },
              { key: "note", header: "Note", priority: 2, render: (e) => e.note ?? <span className="muted">—</span> },
              { key: "added", header: "Added", priority: 3, render: (e) => fmtTime(e.added_at) },
              {
                key: "rm", header: "", priority: 2, render: (e) => (
                  <button type="button" className="btn btn-small" onClick={() => void remove(e.id)} aria-label={`Remove ${e.address}`} disabled={!canEdit.allowed} title={canEdit.reason}>
                    Remove
                  </button>
                ),
              },
            ]}
          />
        </>
      )}
    </Panel>
  );
}

export default function Watchlists() {
  const lists = useApi(api.watchlists);
  const [selected, setSelected] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const canCreate = useCan("POST", "/watchlists");

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!canCreate.allowed) return;
    if (!name.trim()) return setError("Give the watchlist a name.");
    setError(null);
    try {
      const created = await api.createWatchlist(name.trim(), description.trim() || null);
      setName("");
      setDescription("");
      setSelected(created.id);
      lists.reload();
    } catch (err) {
      setError(message(err, "Could not create the watchlist."));
    }
  };

  const active = lists.status === "ready" && selected !== null && lists.data.some((l) => l.id === selected) ? selected : null;

  return (
    <>
      <PageHeader title="Watchlists" lede="Addresses you want BIP to follow. Stored in the local database." />
      <div className="grid grid-side">
        <div className="stack">
          <Panel title="New watchlist">
            <form className="form" onSubmit={create} noValidate aria-label="New watchlist">
              <label className="field">
                <span>Name</span>
                <input id="wl-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={100} />
              </label>
              <label className="field">
                <span>Description (optional)</span>
                <input id="wl-description" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={2000} />
              </label>
              <FieldError id="wl-error" message={error} />
              <RoleHint can={canCreate} />
              <button type="submit" className="btn btn-primary" disabled={!canCreate.allowed} title={canCreate.reason}>Create watchlist</button>
            </form>
          </Panel>
          <Panel title="Lists">
            {lists.status === "loading" ? <Loading /> : null}
            {lists.status === "error" ? <ErrorState error={lists.error} onRetry={lists.reload} /> : null}
            {lists.status === "ready" && lists.data.length === 0 ? <EmptyState title="No watchlists yet" /> : null}
            {lists.status === "ready" && lists.data.length > 0 ? (
              <ul className="cards">
                {lists.data.map((l) => (
                  <li key={l.id}>
                    <button type="button" className="card-link" aria-pressed={active === l.id} onClick={() => setSelected(l.id)}>
                      <div className="card-top">
                        <strong>{l.name}</strong>
                        <span className="num muted">{l.entry_count}</span>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </Panel>
        </div>
        {active !== null ? (
          <WatchlistDetail
            key={active}
            id={active}
            onChanged={lists.reload}
            onDeleted={() => {
              setSelected(null);
              lists.reload();
            }}
          />
        ) : (
          <Panel>
            <EmptyState title="Select or create a watchlist">Its addresses appear here.</EmptyState>
          </Panel>
        )}
      </div>
    </>
  );
}

function summarise(results: { status: string }[]): string {
  const by = new Map<string, number>();
  for (const r of results) by.set(r.status, (by.get(r.status) ?? 0) + 1);
  const parts = [...by.entries()].map(([k, n]) => `${n} ${humanize(k)}`);
  return `${results.length} wallet${results.length === 1 ? "" : "s"} analysed: ${parts.join(", ")}`;
}
