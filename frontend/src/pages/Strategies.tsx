import { useState } from "react";
import { ApiError, api } from "../api/client";
import type { WalletClassification, CopyTraderSignal, MemeMomentum, DevTracker } from "../api/types";
import { EmptyState, ErrorState, Loading, PageHeader, Panel, Pill, Stat } from "../components/ui";
import { fmtInt, humanize } from "../lib/format";

/** Evidence label and limitations shown on every strategy panel (CLAUDE.md §2.12). */
function Basis({ evidenceClass, limitations }: { evidenceClass: string; limitations: string[] }) {
  return (
    <div style={{ marginTop: "1rem" }}>
      <Pill tone="info">{evidenceClass}</Pill>
      <ul className="list muted" style={{ marginTop: "0.5rem", fontSize: "0.85rem" }}>
        {limitations.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
    </div>
  );
}

function checked(value: boolean | null, yes: string, no: string): string {
  return value === null ? "Not checked" : value ? yes : no;
}

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
        lede="Heuristic hypotheses about a wallet's profile, activity, assets and contract creations, computed from the data BIP has stored for it. Research leads, not advice."
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
              label="Activity Score"
              value={copyTrader ? `${Math.round(copyTrader.score * 100)}%` : "—"}
              hint={copyTrader ? `Lead: ${humanize(copyTrader.lead)}` : undefined}
            />
            <Stat
              label="Meme Momentum"
              value={meme ? `${Math.round(meme.momentum_score * 100)}%` : "—"}
              hint={meme ? `${meme.tokens_involved.length} tokens active` : undefined}
            />
            <Stat
              label="Deployments"
              value={dev ? fmtInt(dev.deployments_count) : "—"}
              hint={dev?.funder ? `First funder seen: ${dev.funder.slice(0, 6)}…${dev.funder.slice(-4)}` : "No funder observed"}
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
              {classification.reason ? <p className="muted">{classification.reason}</p> : null}
              <Basis evidenceClass={classification.evidence_class} limitations={classification.limitations} />
            </Panel>

            <Panel title="Copy-Trader Research Lead">
              {copyTrader ? (
                <>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
                    <span>Lead:</span>
                    <Pill tone={copyTrader.lead === "research_lead" ? "info" : "idle"}>{humanize(copyTrader.lead)}</Pill>
                  </div>
                  <ul className="list">
                    {copyTrader.signals.map((sig, i) => (
                      <li key={i}>
                        <strong>{humanize(sig.type)}:</strong> {sig.description}
                      </li>
                    ))}
                  </ul>
                  <p className="muted" style={{ marginTop: "1rem" }}>{copyTrader.provenance}</p>
                  <Basis evidenceClass={copyTrader.evidence_class} limitations={copyTrader.limitations} />
                </>
              ) : (
                <EmptyState title="No signals evaluated" />
              )}
            </Panel>
          </div>

          <div className="grid grid-2" style={{ marginTop: "1.5rem" }}>
            <Panel title="Asset Activity">
              {meme ? (
                <>
                  <div style={{ marginBottom: "1rem" }}>
                    <strong>Tokens Involved:</strong>{" "}
                    {meme.tokens_involved.length > 0 ? meme.tokens_involved.join(", ") : "None detected"}
                  </div>
                  {meme.first_transfers.length > 0 ? (
                    <>
                      <strong>First stored transfers:</strong>
                      <ul className="list">
                        {meme.first_transfers.map((entry, i) => (
                          <li key={i}>
                            <span className="mono">{entry.asset ?? "Unknown Asset"}</span> · {entry.direction} at {entry.time ?? "time unknown"}
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : (
                    <EmptyState title="No stored transfers" />
                  )}
                  <p className="muted" style={{ marginTop: "1rem" }}>{meme.provenance}</p>
                  <Basis evidenceClass={meme.evidence_class} limitations={meme.limitations} />
                </>
              ) : (
                <EmptyState title="No momentum data" />
              )}
            </Panel>

            <Panel title="Contract Creations">
              {dev ? (
                <>
                  <div style={{ display: "flex", gap: "1rem", marginBottom: "1rem" }}>
                    <Pill tone="idle">GitHub link: {checked(dev.github_linked, "linked", "none found")}</Pill>
                    <Pill tone="idle">Upgrade authority: {checked(dev.upgrade_authority_held, "held", "not held")}</Pill>
                  </div>
                  <dl className="kv">
                    <dt>Deployments Count</dt>
                    <dd className="num">{fmtInt(dev.deployments_count)}</dd>
                    <dt>Unconfirmed creations</dt>
                    <dd className="num">{fmtInt(dev.unconfirmed_creations)}</dd>
                    <dt>First funder seen</dt>
                    <dd className="mono">{dev.funder ?? "None observed"}</dd>
                  </dl>
                  {dev.funder_basis ? <p className="muted">{dev.funder_basis}</p> : null}
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
                  <Basis evidenceClass={dev.evidence_class} limitations={dev.limitations} />
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
          Results are heuristic hypotheses over stored data: profile scores, activity, assets moved and contract creations.
        </EmptyState>
      ) : null}
    </>
  );
}
