import { useState } from "react";
import { ApiError, api } from "../api/client";
import type { WalletClassification, CopyTraderSignal, MemeMomentum, DevTracker } from "../api/types";
import { EmptyState, ErrorState, Loading, PageHeader, Panel, Pill, Stat } from "../components/ui";
import { fmtInt, humanize } from "../lib/format";

export default function Strategies() {
  const [chain, setChain] = useState<"ethereum" | "solana">("solana");
  const [address, setAddress] = useState<string>("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const [classification, setClassification] = useState<WalletClassification | null>(null);
  const [copyTrader, setCopyTrader] = useState<CopyTraderSignal | null>(null);
  const [meme, setMeme] = useState<MemeMomentum | null>(null);
  const [dev, setDev] = useState<DevTracker | null>(null);

  async function loadStrategies(e?: React.FormEvent) {
    if (e) e.preventDefault();
    if (!address.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const [cls, copy, mem, dv] = await Promise.all([
        api.strategyClassification(chain, address.trim()),
        api.copyTraderSignals(chain, address.trim()),
        api.memeMomentum(chain, address.trim()),
        api.devTracker(chain, address.trim()),
      ]);
      setClassification(cls);
      setCopyTrader(copy);
      setMeme(mem);
      setDev(dv);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, "unknown", "Could not load the strategies."));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Strategies & Trackers"
        lede="Analyze wallet profiles, copy trading signals, meme momentum, and developer activity across chains."
      />

      <Panel title="Target Selection">
        <form onSubmit={loadStrategies} className="row" style={{ gap: "1rem", display: "flex", flexWrap: "wrap", alignItems: "flex-end" }}>
          <div>
            <label style={{ display: "block", fontSize: "0.85rem", marginBottom: "0.3rem" }}>Chain</label>
            <select
              value={chain}
              onChange={(e) => setChain(e.target.value as "ethereum" | "solana")}
              style={{ padding: "0.5rem", borderRadius: "4px", border: "1px solid var(--border)" }}
            >
              <option value="solana">Solana</option>
              <option value="ethereum">Ethereum</option>
            </select>
          </div>
          <div style={{ flex: 1, minWidth: "280px" }}>
            <label style={{ display: "block", fontSize: "0.85rem", marginBottom: "0.3rem" }}>Wallet Address</label>
            <input
              type="text"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              placeholder="Enter wallet address..."
              style={{ width: "100%", padding: "0.5rem", borderRadius: "4px", border: "1px solid var(--border)" }}
            />
          </div>
          <div>
            <button type="submit" className="btn" disabled={loading}>
              {loading ? "Analyzing…" : "Run Strategy Analysis"}
            </button>
          </div>
        </form>
      </Panel>

      {loading ? <Loading label="Evaluating multi-strategy intelligence…" /> : null}
      {error ? <ErrorState error={error} onRetry={loadStrategies} /> : null}

      {!loading && !error && classification ? (
        <>
          <div className="stats" style={{ marginTop: "1.5rem" }}>
            <Stat
              label="Primary Strategy"
              value={humanize(classification.primary_strategy)}
              hint={`Confidence: ${Math.round(classification.confidence * 100)}%`}
            />
            <Stat
              label="Copy Trade Score"
              value={copyTrader ? `${Math.round(copyTrader.score * 100)}%` : "—"}
              hint={copyTrader?.recommendation ? `Recommendation: ${humanize(copyTrader.recommendation)}` : undefined}
            />
            <Stat
              label="Meme Momentum"
              value={meme ? `${Math.round(meme.momentum_score * 100)}%` : "—"}
              hint={meme ? `${meme.tokens_involved.length} tokens active` : undefined}
            />
            <Stat
              label="Deployments"
              value={dev ? fmtInt(dev.deployments_count) : "—"}
              hint={dev?.funder ? `Funder: ${dev.funder.slice(0, 6)}…${dev.funder.slice(-4)}` : "No funder recorded"}
            />
          </div>

          <div className="grid grid-2" style={{ marginTop: "1.5rem" }}>
            <Panel title="Wallet Profile Classification">
              <p className="muted" style={{ marginBottom: "1rem" }}>{classification.provenance}</p>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
                    <th style={{ padding: "0.5rem" }}>Strategy / Archetype</th>
                    <th style={{ padding: "0.5rem" }}>Score</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(classification.strategies).map(([strat, score]) => (
                    <tr key={strat} style={{ borderBottom: "1px solid var(--border)" }}>
                      <td style={{ padding: "0.5rem" }}>{humanize(strat)}</td>
                      <td style={{ padding: "0.5rem" }} className="num">{Number(score).toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ marginTop: "1rem", fontSize: "0.9rem" }}>
                <strong>Observed Metrics:</strong> {classification.metrics.transactions_observed} transactions, {classification.metrics.transfers_observed} transfers, {classification.metrics.unique_assets} unique assets.
              </div>
            </Panel>

            <Panel title="Copy Trader Signals">
              {copyTrader ? (
                <>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
                    <span>Recommendation:</span>
                    <Pill tone={copyTrader.recommendation === "moderate_copy" ? "ok" : "info"}>
                      {humanize(copyTrader.recommendation)}
                    </Pill>
                  </div>
                  <ul className="list">
                    {copyTrader.signals.map((sig, i) => (
                      <li key={i}>
                        <strong>{humanize(sig.type)}:</strong> {sig.description}
                      </li>
                    ))}
                  </ul>
                  <p className="muted" style={{ marginTop: "1rem" }}>{copyTrader.provenance}</p>
                </>
              ) : (
                <EmptyState title="No signals evaluated" />
              )}
            </Panel>
          </div>

          <div className="grid grid-2" style={{ marginTop: "1.5rem" }}>
            <Panel title="Meme Momentum Opportunities">
              {meme ? (
                <>
                  <div style={{ marginBottom: "1rem" }}>
                    <strong>Tokens Involved:</strong>{" "}
                    {meme.tokens_involved.length > 0 ? meme.tokens_involved.join(", ") : "None detected"}
                  </div>
                  {meme.early_entries.length > 0 ? (
                    <ul className="list">
                      {meme.early_entries.map((entry, i) => (
                        <li key={i}>
                          <span className="mono">{entry.asset ?? "Unknown Asset"}</span> · {entry.direction} at {entry.time}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <EmptyState title="No early entries found" />
                  )}
                  <p className="muted" style={{ marginTop: "1rem" }}>{meme.provenance}</p>
                </>
              ) : (
                <EmptyState title="No momentum data" />
              )}
            </Panel>

            <Panel title="Developer Activity & Trackers">
              {dev ? (
                <>
                  <div style={{ display: "flex", gap: "1rem", marginBottom: "1rem" }}>
                    <Pill tone={dev.github_linked ? "ok" : "idle"}>
                      {dev.github_linked ? "GitHub Linked" : "No GitHub Link"}
                    </Pill>
                    <Pill tone={dev.upgrade_authority_held ? "warn" : "ok"}>
                      {dev.upgrade_authority_held ? "Upgrade Authority Held" : "Immutable"}
                    </Pill>
                  </div>
                  <dl className="kv">
                    <dt>Deployments Count</dt>
                    <dd className="num">{fmtInt(dev.deployments_count)}</dd>
                    <dt>Deployer Funder</dt>
                    <dd className="mono">{dev.funder ?? "None"}</dd>
                  </dl>
                  {dev.deployments.length > 0 ? (
                    <div style={{ marginTop: "1rem" }}>
                      <strong>Contracts Deployed:</strong>
                      <ul className="list" style={{ marginTop: "0.5rem" }}>
                        {dev.deployments.map((contract, i) => (
                          <li key={i} className="mono">{contract}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  <p className="muted" style={{ marginTop: "1rem" }}>{dev.provenance}</p>
                </>
              ) : (
                <EmptyState title="No developer data" />
              )}
            </Panel>
          </div>
        </>
      ) : null}

      {!loading && !error && !classification ? (
        <EmptyState title="Enter a wallet address and run strategy analysis">
          Strategies include copy trading evaluation, meme momentum detection, and developer tracking.
        </EmptyState>
      ) : null}
    </>
  );
}
