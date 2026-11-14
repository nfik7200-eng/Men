import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { DataTable } from "../components/DataTable";
import { ErrorState, Loading, PageHeader, Panel } from "../components/ui";
import { fmtTime, humanize } from "../lib/format";
import { useApi } from "../lib/useApi";
import { ProfileDetail } from "./ProviderIntel";

type HistoryRow = { capability?: string; status?: string; connectivity?: string; observed_at?: string; explanation?: string; run_id?: string };

/** One provider: its profile, reliability and the full history of capability observations. */
export default function ProviderDetail() {
  const { id = "" } = useParams();
  const hist = useApi(() => api.capabilityHistory(id), [id]);
  const rows = hist.status === "ready" && Array.isArray(hist.data.items) ? (hist.data.items as HistoryRow[]) : [];
  return (
    <>
      <PageHeader title={`Provider ${id}`} actions={<Link className="btn" to="/providers">All providers</Link>} />
      <ProfileDetail id={id} />
      <Panel title="Capability history">
        {hist.status === "loading" ? <Loading /> : null}
        {hist.status === "error" ? <ErrorState error={hist.error} onRetry={hist.reload} /> : null}
        {hist.status === "ready" ? (
          <DataTable
            caption="Capability history"
            rows={rows}
            rowKey={(r) => `${r.run_id ?? ""}:${r.capability ?? ""}:${r.observed_at ?? ""}`}
            empty="No capability observations recorded yet."
            columns={[
              { key: "cap", header: "Capability", priority: 1, render: (r) => humanize(r.capability ?? "unknown") },
              { key: "status", header: "Status", priority: 2, render: (r) => humanize(r.status ?? "not tested") },
              { key: "conn", header: "Connectivity", priority: 2, render: (r) => humanize(r.connectivity ?? "unknown") },
              { key: "at", header: "Observed", priority: 2, render: (r) => fmtTime(r.observed_at ?? null) },
              { key: "why", header: "Explanation", priority: 3, render: (r) => r.explanation ?? "—" },
            ]}
          />
        ) : null}
        <p className="note">A blocked development network is recorded as an environment restriction, not as a provider failure.</p>
      </Panel>
    </>
  );
}
