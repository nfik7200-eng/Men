import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { Watchlist } from "../api/types";
import { EmptyState, ErrorState, FieldError, Loading, Mono, PageHeader, Panel } from "../components/ui";
import { CHAINS, chainLabel, validateAddress } from "../lib/address";
import { fmtTime } from "../lib/format";
import { useApi } from "../lib/useApi";

const message = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);

function AddEntry({ watchlist, onChange }: { watchlist: Watchlist; onChange: () => void }) {
  const [chain, setChain] = useState("ethereum");
  const [address, setAddress] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
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
      <button type="submit" className="btn btn-primary" disabled={busy}>Add</button>
      <FieldError id="entry-error" message={error} />
    </form>
  );
}

function WatchlistDetail({ id, onDeleted, onChanged }: { id: number; onDeleted: () => void; onChanged: () => void }) {
  const list = useApi(() => api.watchlist(id), [id]);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
  return (
    <Panel
      title={w.name}
      aside={
        confirming ? (
          <span className="row">
            <span className="muted">Delete this list and its {w.entry_count} entries?</span>
            <button type="button" className="btn btn-danger" onClick={() => void destroy()}>Delete</button>
            <button type="button" className="btn" onClick={() => setConfirming(false)}>Cancel</button>
          </span>
        ) : (
          <button type="button" className="btn" onClick={() => setConfirming(true)}>Delete list</button>
        )
      }
    >
      {w.description ? <p className="muted">{w.description}</p> : null}
      <AddEntry watchlist={w} onChange={refresh} />
      <FieldError id="watchlist-error" message={error} />
      {w.entries.length === 0 ? (
        <EmptyState title="No addresses on this list" />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Chain</th><th>Address</th><th>Note</th><th>Added</th><th /></tr>
            </thead>
            <tbody>
              {w.entries.map((e) => (
                <tr key={e.id}>
                  <td>{chainLabel(e.chain)}</td>
                  <td><Link to={`/wallets/${e.chain}/${e.address}`}><Mono>{e.address}</Mono></Link></td>
                  <td>{e.note ?? <span className="muted">—</span>}</td>
                  <td className="mono">{fmtTime(e.added_at)}</td>
                  <td>
                    <button type="button" className="btn btn-small" onClick={() => void remove(e.id)} aria-label={`Remove ${e.address}`}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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

  const create = async (e: FormEvent) => {
    e.preventDefault();
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
              <button type="submit" className="btn btn-primary">Create watchlist</button>
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
