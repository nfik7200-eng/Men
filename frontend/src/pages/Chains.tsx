import { api } from "../api/client";
import { ErrorState, Loading, PageHeader, Panel, Pill } from "../components/ui";
import { chainLabel } from "../lib/address";
import { fmtInt, fmtTime } from "../lib/format";
import { useApi } from "../lib/useApi";
import { chainStateLabel, chainTone } from "./Overview";

const yes = (v: boolean) => <Pill tone={v ? "ok" : "idle"}>{v ? "Yes" : "No"}</Pill>;

export default function Chains() {
  const chains = useApi(api.chains);
  const providers = useApi(api.providers);
  return (
    <>
      <PageHeader
        title="Chains"
        lede="Integration status per chain. A chain counts as connected only after a real adapter has retrieved data."
      />
      {chains.status === "loading" ? <Loading /> : null}
      {chains.status === "error" ? <ErrorState error={chains.error} onRetry={chains.reload} /> : null}
      {chains.status === "ready" ? (
        <div className="grid grid-2">
          {chains.data.map((c) => {
            const latest = c.position_kind === "block" ? c.latest_stored_block : c.latest_stored_slot;
            const relevant =
              providers.status === "ready" ? providers.data.filter((p) => p.chains.includes(c.chain)) : [];
            return (
              <Panel key={c.chain} title={chainLabel(c.chain)} aside={<Pill tone={chainTone(c)}>{chainStateLabel(c)}</Pill>}>
                <dl className="kv">
                  <dt>Chain id</dt>
                  <dd className="mono">{c.chain}</dd>
                  <dt>Family · network</dt>
                  <dd>
                    {c.family.toUpperCase()} · {c.network}
                  </dd>
                  <dt>Position unit</dt>
                  <dd>{c.position_kind === "block" ? "Block number" : "Slot"}</dd>
                  <dt>Configured</dt>
                  <dd>{yes(c.configured)}</dd>
                  <dt>Adapter implemented</dt>
                  <dd>{yes(c.adapter_implemented)}</dd>
                  <dt>Connected</dt>
                  <dd>{yes(c.connected)}</dd>
                  <dt>Data available</dt>
                  <dd>{yes(c.data_available)}</dd>
                  <dt>Transactions stored</dt>
                  <dd className="num">{fmtInt(c.transactions_stored)}</dd>
                  <dt>Latest stored {c.position_kind}</dt>
                  <dd className="num">{latest ?? "None stored"}</dd>
                  <dt>Last successful retrieval</dt>
                  <dd className="mono">{fmtTime(c.last_successful_retrieval)}</dd>
                  <dt>Providers</dt>
                  <dd>
                    {providers.status === "ready"
                      ? `${relevant.length} catalogued, 0 connected`
                      : providers.status === "loading"
                        ? "Loading…"
                        : "Unavailable"}
                  </dd>
                </dl>
                <p className="note">{c.status_note}</p>
              </Panel>
            );
          })}
        </div>
      ) : null}
    </>
  );
}
