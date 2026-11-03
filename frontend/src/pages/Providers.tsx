import { api } from "../api/client";
import type { Provider } from "../api/types";
import { ErrorState, Loading, PageHeader, Pill } from "../components/ui";
import { chainLabel } from "../lib/address";
import { fmtTime, humanize } from "../lib/format";
import { useApi } from "../lib/useApi";

const configTone = (p: Provider) =>
  p.configuration_status === "configured" ? "ok" : p.configuration_status === "not_required" ? "info" : "idle";

export default function Providers() {
  const providers = useApi(api.providers);
  return (
    <>
      <PageHeader
        title="Providers"
        lede="Candidate data sources from the provider research. No adapter exists yet, so none has ever been contacted."
      />
      {providers.status === "loading" ? <Loading /> : null}
      {providers.status === "error" ? <ErrorState error={providers.error} onRetry={providers.reload} /> : null}
      {providers.status === "ready" ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Provider</th>
                <th>Kind</th>
                <th>Chains</th>
                <th>Capabilities</th>
                <th>Configuration</th>
                <th>Connection</th>
                <th>Last successful check</th>
                <th>Research</th>
              </tr>
            </thead>
            <tbody>
              {providers.data.map((p) => (
                <tr key={p.id}>
                  <td>
                    <strong>{p.name}</strong>
                    <div className="mono muted">{p.id}</div>
                  </td>
                  <td>{humanize(p.kind)}</td>
                  <td>{p.chains.length ? p.chains.map(chainLabel).join(", ") : "—"}</td>
                  <td className="muted">{p.implemented_capabilities.length ? p.implemented_capabilities.join(", ") : "None implemented"}</td>
                  <td>
                    <Pill tone={configTone(p)}>{humanize(p.configuration_status)}</Pill>
                  </td>
                  <td>
                    <Pill tone="bad">{p.connection_status}</Pill>
                  </td>
                  <td className="mono">{fmtTime(p.last_success_at)}</td>
                  <td>
                    <a href={p.docs_url} target="_blank" rel="noopener noreferrer">
                      {p.research_grade}
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <p className="note">
        "Configured" only means the credential variable is set on the server; its value is never
        read or shown here. Research grades: V-search = quoted from an official page via search,
        Conflict = official sources disagree, 3rd-party = unofficial sources only.
      </p>
    </>
  );
}
