import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
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

export function WalletProfilePage() {
  const { chain = "", address = "" } = useParams();
  const profile = useApi(() => api.wallet(chain, address), [chain, address]);

  return (
    <>
      <PageHeader
        title="Wallet profile"
        lede={`${chainLabel(chain)} · ${address}`}
        actions={<Link className="btn" to="/wallets">Back to search</Link>}
      />
      {profile.status === "loading" ? <Loading /> : null}
      {profile.status === "error" && profile.error.status === 404 ? (
        <EmptyState title="No data stored for this address">
          The address is valid, but BIP has not ingested any transactions or transfers involving
          it. Add it to a <Link to="/watchlists">watchlist</Link> to track it once ingestion exists.
        </EmptyState>
      ) : null}
      {profile.status === "error" && profile.error.status !== 404 ? (
        <ErrorState error={profile.error} onRetry={profile.reload} />
      ) : null}
      {profile.status === "ready" ? (
        <>
          <div className="grid grid-2">
            <Panel title="Activity">
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
            </Panel>
          </div>
          <Panel title="Recent transactions">
            {profile.data.recent_transactions.length === 0 ? (
              <EmptyState title="No transactions stored" />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Tx</th>
                      <th>{chain === "solana" ? "Slot" : "Block"}</th>
                      <th>From</th>
                      <th>To</th>
                      <th>Status</th>
                      <th>Source · verification</th>
                    </tr>
                  </thead>
                  <tbody>
                    {profile.data.recent_transactions.map((t) => (
                      <tr key={t.tx_id}>
                        <td><Mono title={t.tx_id}>{shortId(t.tx_id)}</Mono></td>
                        <td className="num">{t.block_number ?? t.slot ?? "—"}</td>
                        <td><Mono title={t.from_address ?? ""}>{t.from_address ? shortId(t.from_address) : "—"}</Mono></td>
                        <td><Mono title={t.to_address ?? ""}>{t.to_address ? shortId(t.to_address) : "—"}</Mono></td>
                        <td>{t.status ?? "unknown"}</td>
                        <td className="muted">{t.source_provider} · {humanize(t.verification_status)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
          <Panel title="Recent transfers">
            {profile.data.recent_transfers.length === 0 ? (
              <EmptyState title="No transfers stored" />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Tx</th>
                      <th>Standard</th>
                      <th>Token</th>
                      <th className="num">Raw amount</th>
                      <th>Decimals</th>
                      <th>Classification</th>
                    </tr>
                  </thead>
                  <tbody>
                    {profile.data.recent_transfers.map((t) => (
                      <tr key={`${t.tx_id}:${t.event_key}`}>
                        <td><Mono title={t.tx_id}>{shortId(t.tx_id)}</Mono></td>
                        <td>{t.standard}</td>
                        <td><Mono title={t.token ?? ""}>{t.token ? shortId(t.token) : "native"}</Mono></td>
                        <td className="num mono">{t.amount_raw}</td>
                        <td>{t.decimals ?? "unknown"}</td>
                        <td>{humanize(t.classification)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        </>
      ) : null}
    </>
  );
}
