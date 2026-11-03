import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { IngestionJob, ObservationRow } from "../api/types";
import { addressUrl, txUrl } from "../lib/explorer";
import { EmptyState, ErrorState, FieldError, Loading, Mono, PageHeader, Panel, Pill } from "../components/ui";
import { CHAINS, chainLabel, validateAddress } from "../lib/address";
import { fmtInt, fmtTime, humanize, shortId } from "../lib/format";
import { useApi } from "../lib/useApi";

export function WalletSearch() {
  const navigate = useNavigate();
  const [chain, setChain] = useState<string>("ethereum");
  const [address, setAddress] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const problem = validateAddress(chain, address);
    setError(problem);
    if (!problem) navigate(`/wallets/${chain}/${encodeURIComponent(address.trim())}`);
  };

  return (
    <form className="search" onSubmit={submit} noValidate aria-label="Wallet search">
      <label className="field">
        <span>Chain</span>
        <select id="wallet-chain" value={chain} onChange={(e) => { setChain(e.target.value); setError(null); }}>
          {CHAINS.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
      <label className="field grow">
        <span>Address</span>
        <input
          id="wallet-address"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          placeholder={chain === "ethereum" ? "0x…" : "Base58 public key"}
          autoComplete="off"
          spellCheck={false}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "wallet-address-error" : undefined}
        />
      </label>
      <button type="submit" className="btn btn-primary">
        Look up
      </button>
      <FieldError id="wallet-address-error" message={error} />
    </form>
  );
}

export default function Wallets() {
  const wallets = useApi(() => api.wallets(50, 0));
  return (
    <>
      <PageHeader
        title="Wallet Intelligence"
        lede="Look up an address to see what BIP has stored about it. BIP describes on-chain behaviour only; it never attributes addresses to people."
      />
      <Panel>
        <WalletSearch />
      </Panel>
      <Panel title="Wallets with stored data">
        {wallets.status === "loading" ? <Loading /> : null}
        {wallets.status === "error" ? <ErrorState error={wallets.error} onRetry={wallets.reload} /> : null}
        {wallets.status === "ready" && wallets.data.total === 0 ? (
          <EmptyState title="No wallet data stored yet">
            Wallet records are created when chain data is ingested. Ingestion is not implemented
            yet, so this list is empty. Lookups still validate addresses.
          </EmptyState>
        ) : null}
        {wallets.status === "ready" && wallets.data.total > 0 ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Chain</th>
                  <th>Address</th>
                  <th className="num">Transactions</th>
                  <th className="num">Transfers</th>
                  <th>Last observed</th>
                </tr>
              </thead>
              <tbody>
                {wallets.data.items.map((w) => (
                  <tr key={`${w.chain}:${w.address}`}>
                    <td>{chainLabel(w.chain)}</td>
                    <td>
                      <Link to={`/wallets/${w.chain}/${w.address}`}>
                        <Mono title={w.address}>{shortId(w.address, 8)}</Mono>
                      </Link>
                    </td>
                    <td className="num">{fmtInt(w.transaction_count)}</td>
                    <td className="num">{fmtInt(w.transfer_count)}</td>
                    <td className="mono">{fmtTime(w.last_observed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Panel>
    </>
  );
}

function IngestPanel({ chain, address, onDone }: { chain: string; address: string; onDone: () => void }) {
  const [limit, setLimit] = useState(20);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<IngestionJob | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const history = useApi(() => api.jobs(chain, address), [chain, address]);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.ingestWallet(chain, address, limit);
      setJob(result);
      history.reload();
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, "unknown", "Retrieval failed."));
    } finally {
      setBusy(false);
    }
  };
  const latest = job ?? (history.status === "ready" ? (history.data[0] ?? null) : null);
  const notCovered = latest?.coverage?.["not_covered"];

  return (
    <Panel title="Retrieve recent activity">
      <p className="note">
        One bounded, read-only run through the provider router. {chain === "ethereum"
          ? "Ethereum: Transfer logs (ERC-20/721) involving this address in recent blocks, then each transaction and receipt."
          : "Solana: the most recent signatures for this address, then each transaction."}
      </p>
      <div className="row">
        <label className="field">
          <span>Transactions (max)</span>
          <select id="ingest-limit" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            {[5, 10, 20, 50].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </label>
        <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void run()}>
          {busy ? "Retrieving… (may take up to a minute)" : "Retrieve recent activity"}
        </button>
      </div>
      {error ? <ErrorState error={error} /> : null}
      {latest ? (
        <div className="stack" role="status">
          <div className="row">
            <Pill tone={latest.status === "succeeded" ? "ok" : latest.status === "partial" ? "warn" : "bad"}>
              Run {latest.id}: {latest.status}
            </Pill>
            <span className="muted">
              {fmtTime(latest.finished_at ?? latest.started_at)} · via {latest.providers.join(", ") || "no provider"} ·{" "}
              {latest.rpc_calls} RPC calls
            </span>
          </div>
          <p>{latest.message}</p>
          {Array.isArray(notCovered) ? (
            <p className="note">Not covered by this run: {(notCovered as string[]).join("; ")}.</p>
          ) : null}
          {latest.errors.length > 0 ? (
            <details>
              <summary>{latest.error_count} recorded issue(s)</summary>
              <ul className="list">
                {latest.errors.slice(0, 20).map((e, i) => (
                  <li key={i}>
                    <span className="mono">{e.error_code}</span> · {e.stage}
                    {e.item_ref ? <span className="mono muted"> {shortId(e.item_ref)}</span> : null} · {e.detail}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : history.status === "ready" ? (
        <p className="muted">No retrieval has been run for this address yet.</p>
      ) : null}
    </Panel>
  );
}

const str = (v: unknown): string => (v === null || v === undefined ? "—" : String(v));

function TxLink({ chain, id }: { chain: string; id: string }) {
  const url = txUrl(chain, id);
  return url ? (
    <a href={url} target="_blank" rel="noopener noreferrer"><Mono title={id}>{shortId(id)}</Mono></a>
  ) : (
    <Mono title={id}>{shortId(id)}</Mono>
  );
}

function Provenance({ row }: { row: ObservationRow }) {
  return (
    <span className="muted">
      {str(row["source_provider"])} · {humanize(str(row["verification_status"]))} · {str(row["finality"])}
    </span>
  );
}

export function WalletProfilePage() {
  const { chain = "", address = "" } = useParams();
  const profile = useApi(() => api.wallet(chain, address), [chain, address]);
  const txs = useApi(() => api.walletTransactions(chain, address), [chain, address]);
  const transfers = useApi(() => api.walletTransfers(chain, address), [chain, address]);
  const balances = useApi(() => api.walletBalanceChanges(chain, address), [chain, address]);
  const reloadAll = () => {
    profile.reload();
    txs.reload();
    transfers.reload();
    balances.reload();
  };
  const explorer = addressUrl(chain, address);

  return (
    <>
      <PageHeader
        title="Wallet profile"
        lede={`${chainLabel(chain)} · ${address}`}
        actions={
          <>
            {explorer ? <a className="btn" href={explorer} target="_blank" rel="noopener noreferrer">Explorer</a> : null}
            <Link className="btn" to="/wallets">Back to search</Link>
          </>
        }
      />
      <IngestPanel chain={chain} address={address} onDone={reloadAll} />
      {profile.status === "loading" ? <Loading /> : null}
      {profile.status === "error" && profile.error.status === 404 ? (
        <EmptyState title="No data stored for this address yet">
          The address is valid, but nothing has been retrieved for it. Use "Retrieve recent
          activity" above. An empty result after a run means no activity was found in the scanned
          window, not that the wallet has never been used.
        </EmptyState>
      ) : null}
      {profile.status === "error" && profile.error.status !== 404 ? (
        <ErrorState error={profile.error} onRetry={profile.reload} />
      ) : null}
      {profile.status === "ready" ? (
        <div className="grid grid-2">
          <Panel title="Stored activity">
            <dl className="kv">
              <dt>Transactions stored</dt>
              <dd className="num">{fmtInt(profile.data.transaction_count)}</dd>
              <dt>Transfers stored</dt>
              <dd className="num">{fmtInt(profile.data.transfer_count)}</dd>
              <dt>First observed</dt>
              <dd className="mono">{fmtTime(profile.data.first_observed_at)}</dd>
              <dt>Last observed</dt>
              <dd className="mono">{fmtTime(profile.data.last_observed_at)}</dd>
            </dl>
          </Panel>
          <Panel title="Provenance of first record">
            <dl className="kv">
              <dt>Source</dt>
              <dd className="mono">{profile.data.first_seen_source}</dd>
              <dt>Ingestion</dt>
              <dd>{humanize(profile.data.first_seen_ingestion)}</dd>
              <dt>Verification</dt>
              <dd>
                <Pill tone={profile.data.first_seen_verification === "cross_verified" ? "ok" : "warn"}>
                  {humanize(profile.data.first_seen_verification)}
                </Pill>
              </dd>
            </dl>
            <p className="note">BIP describes on-chain behaviour only. It does not label wallets as people, funds, exchanges or insiders.</p>
          </Panel>
        </div>
      ) : null}

      <Panel title={`Transactions${txs.status === "ready" ? ` (${txs.data.total})` : ""}`}>
        {txs.status === "loading" ? <Loading /> : null}
        {txs.status === "error" ? <ErrorState error={txs.error} onRetry={txs.reload} /> : null}
        {txs.status === "ready" && txs.data.total === 0 ? <EmptyState title="No transactions stored" /> : null}
        {txs.status === "ready" && txs.data.total > 0 ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Tx</th>
                  <th>{chain === "solana" ? "Slot" : "Block"}</th>
                  <th>Time</th>
                  <th>Status</th>
                  <th>{chain === "solana" ? "Fee (lamports)" : "Value (wei)"}</th>
                  <th>Parse</th>
                  <th>Source · verification · finality</th>
                </tr>
              </thead>
              <tbody>
                {txs.data.items.map((t) => (
                  <tr key={str(t["tx_id"])}>
                    <td><TxLink chain={chain} id={str(t["tx_id"])} /></td>
                    <td className="num">{str(t["block_number"] ?? t["slot"])}</td>
                    <td className="mono">{t["block_time"] ? fmtTime(str(t["block_time"])) : "unknown"}</td>
                    <td>{str(t["status"] ?? "unknown")}</td>
                    <td className="num mono">{str(chain === "solana" ? t["fee_raw"] : t["value_raw"])}</td>
                    <td>
                      <Pill tone={t["parse_status"] === "complete" ? "ok" : t["parse_status"] === "partial" ? "warn" : "bad"}>
                        {str(t["parse_status"])}
                      </Pill>
                      {typeof t["unknown_instruction_count"] === "number" && t["unknown_instruction_count"] > 0 ? (
                        <div className="muted">{str(t["unknown_instruction_count"])} unknown instr.</div>
                      ) : null}
                    </td>
                    <td><Provenance row={t} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Panel>

      <Panel title={`Transfer observations${transfers.status === "ready" ? ` (${transfers.data.total})` : ""}`}>
        {transfers.status === "ready" && transfers.data.total === 0 ? <EmptyState title="No transfers stored" /> : null}
        {transfers.status === "error" ? <ErrorState error={transfers.error} onRetry={transfers.reload} /> : null}
        {transfers.status === "ready" && transfers.data.total > 0 ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Tx</th>
                  <th>Kind</th>
                  <th>Token / mint</th>
                  <th>From</th>
                  <th>To</th>
                  <th className="num">Raw amount</th>
                  <th>Decimals</th>
                </tr>
              </thead>
              <tbody>
                {transfers.data.items.map((t) => (
                  <tr key={`${str(t["tx_id"])}:${str(t["event_key"])}`}>
                    <td><TxLink chain={chain} id={str(t["tx_id"])} /></td>
                    <td>{humanize(str(t["observation_kind"]))}</td>
                    <td><Mono title={str(t["token"])}>{t["token"] ? shortId(str(t["token"])) : "native"}</Mono></td>
                    <td><Mono title={str(t["from_address"])}>{t["from_address"] ? shortId(str(t["from_address"])) : "—"}</Mono></td>
                    <td><Mono title={str(t["to_address"])}>{t["to_address"] ? shortId(str(t["to_address"])) : "—"}</Mono></td>
                    <td className="num mono">{str(t["amount_raw"])}</td>
                    <td>{t["decimals"] === null || t["decimals"] === undefined ? "unknown" : str(t["decimals"])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Panel>

      {chain === "solana" ? (
        <Panel title={`Balance changes${balances.status === "ready" ? ` (${balances.data.total})` : ""}`}>
          <p className="note">Observed pre/post balances from transaction metadata. These are balance facts, not transfers between named parties.</p>
          {balances.status === "ready" && balances.data.total === 0 ? <EmptyState title="No balance changes stored" /> : null}
          {balances.status === "ready" && balances.data.total > 0 ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Tx</th>
                    <th>Kind</th>
                    <th>Account</th>
                    <th>Mint</th>
                    <th className="num">Change (raw)</th>
                    <th>Decimals</th>
                  </tr>
                </thead>
                <tbody>
                  {balances.data.items.map((b, i) => (
                    <tr key={i}>
                      <td><TxLink chain={chain} id={str(b["tx_id"])} /></td>
                      <td>{humanize(str(b["observation_kind"]))}</td>
                      <td><Mono title={str(b["account"])}>{shortId(str(b["account"]))}</Mono></td>
                      <td><Mono title={str(b["mint"])}>{b["mint"] ? shortId(str(b["mint"])) : "SOL"}</Mono></td>
                      <td className="num mono">{str(b["delta_raw"])}</td>
                      <td>{str(b["decimals"])}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </Panel>
      ) : null}
    </>
  );
}
