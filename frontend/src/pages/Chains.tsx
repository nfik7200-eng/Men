import { RoleHint } from "../auth/RoleHint";
import { useCan } from "../auth/useAuth";
import { useState } from "react";
import { ApiError, api } from "../api/client";
import type { ChainStatus } from "../api/types";
import { ErrorState, FieldError, Loading, Mono, PageHeader, Panel, Pill } from "../components/ui";
import { chainLabel } from "../lib/address";
import { fmtInt, fmtTime, shortId } from "../lib/format";
import { useApi } from "../lib/useApi";
import { chainStateLabel, chainTone } from "./Overview";

const yes = (v: boolean) => <Pill tone={v ? "ok" : "idle"}>{v ? "Yes" : "No"}</Pill>;

function ChainPanel({ c, onChange }: { c: ChainStatus; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const can = useCan("POST", `/chains/${c.chain}/latest-block`);
  const obs = c.latest_observation;
  const unit = c.position_kind === "block" ? "block" : "slot";

  const fetchHead = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.fetchLatestBlock(c.chain);
      onChange();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not fetch the latest block.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title={chainLabel(c.chain)} aside={<Pill tone={chainTone(c)}>{chainStateLabel(c)}</Pill>}>
      <dl className="kv">
        <dt>Network</dt>
        <dd>
          {c.family.toUpperCase()} · {c.network}
          {obs ? <div className="muted">verified: {obs.identity}</div> : null}
        </dd>
        <dt>Latest known {unit}</dt>
        <dd className="num">
          {obs ? (c.position_kind === "block" ? obs.head_block : obs.head_slot) ?? "—" : "Not retrieved yet"}
          {obs?.finalized_block != null ? <div className="muted">finalized block {obs.finalized_block}</div> : null}
          {obs?.commitment ? <div className="muted">commitment: {obs.commitment}</div> : null}
        </dd>
        <dt>Head hash</dt>
        <dd>{obs?.head_hash ? <Mono title={obs.head_hash}>{shortId(obs.head_hash, 10)}</Mono> : "—"}</dd>
        <dt>Provider used</dt>
        <dd className="mono">{obs?.provider_id ?? "—"}</dd>
        <dt>Observed</dt>
        <dd className="mono">{fmtTime(obs?.observed_at ?? null)}</dd>
        <dt>Last successful retrieval</dt>
        <dd className="mono">{fmtTime(c.last_successful_retrieval)}</dd>
        <dt>Adapter</dt>
        <dd>{yes(c.adapter_implemented)}</dd>
        <dt>Connected</dt>
        <dd>{yes(c.connected)}</dd>
        <dt>Usable providers</dt>
        <dd className="mono">{c.usable_providers.length ? c.usable_providers.join(", ") : "None"}</dd>
        <dt>Transactions stored</dt>
        <dd className="num">{fmtInt(c.transactions_stored)}</dd>
      </dl>
      <p className="note">{c.status_note}</p>
      <div className="row">
        <button type="button" className="btn btn-primary" disabled={busy || c.usable_providers.length === 0 || !can.allowed} title={can.reason} onClick={() => void fetchHead()}>
          {busy ? "Fetching…" : `Fetch latest ${unit}`}
        </button>
        <RoleHint can={can} />
      </div>
      <FieldError id={`chain-error-${c.chain}`} message={error} />
    </Panel>
  );
}

export default function Chains() {
  const chains = useApi(api.chains);
  return (
    <>
      <PageHeader
        title="Chains"
        lede="Network identity is verified (chain id / genesis hash) before any provider's data is used. A chain counts as connected only after a verified request succeeded in the last 30 minutes."
      />
      {chains.status === "loading" ? <Loading /> : null}
      {chains.status === "error" ? <ErrorState error={chains.error} onRetry={chains.reload} /> : null}
      {chains.status === "ready" ? (
        <div className="grid grid-2">
          {chains.data.map((c) => (
            <ChainPanel key={c.chain} c={c} onChange={chains.reload} />
          ))}
        </div>
      ) : null}
    </>
  );
}
