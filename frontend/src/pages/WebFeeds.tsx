import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { WebRefresh } from "../api/types";
import { RoleHint } from "../auth/RoleHint";
import { useCan } from "../auth/useAuth";
import { DataTable } from "../components/DataTable";
import { ExternalLink } from "../components/ExternalLink";
import { EmptyState, ErrorState, Loading, PageHeader, Panel, Pill } from "../components/ui";
import { fmtTime } from "../lib/format";
import { useApi } from "../lib/useApi";

function outcomeTone(outcome: string | null): "ok" | "warn" | "bad" | "idle" {
  if (outcome === "ok" || outcome === "not_modified") return "ok";
  if (outcome === null) return "idle";
  if (outcome === "robots_blocked") return "warn";
  return "bad";
}

export default function WebFeeds() {
  const feeds = useApi(api.webFeeds);
  const [feed, setFeed] = useState("");
  const items = useApi(() => api.webItems(feed || undefined, 100), [feed]);
  const can = useCan("POST", "/web/feeds/refresh");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<WebRefresh | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.refreshWebFeeds());
      feeds.reload();
      items.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The refresh could not run.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Web feeds"
        lede="Items published by the RSS and Atom feeds you follow. That a feed published an item is observed; what the item says is not verified."
      />
      <div className="stack">
        <Panel
          title="Feeds"
          aside={
            <span className="row-inline">
              <RoleHint can={can} />
              <button type="button" className="btn" disabled={!can.allowed || busy} title={can.reason} onClick={refresh}>
                {busy ? "Fetching…" : "Fetch now"}
              </button>
            </span>
          }
        >
          {feeds.status === "loading" ? <Loading /> : null}
          {feeds.status === "error" ? <ErrorState error={feeds.error} onRetry={feeds.reload} /> : null}
          {feeds.status === "ready" && feeds.data.items.length === 0 ? (
            <EmptyState title="No feeds configured">
              Add feed URLs on the <Link to="/setup">Setup</Link> page (Web feeds). Fetching contacts the feed sites and honours
              their robots.txt.
            </EmptyState>
          ) : null}
          {feeds.status === "ready" && feeds.data.items.length > 0 ? (
            <>
              {!feeds.data.contact_set ? (
                <p className="note">Tip: add a contact on the Setup page so site operators can reach you about BIP&apos;s requests.</p>
              ) : null}
              <DataTable
                caption="Followed feeds"
                rows={feeds.data.items}
                rowKey={(f) => f.url}
                empty="No feeds."
                columns={[
                  { key: "url", header: "Feed", priority: 1, render: (f) => <ExternalLink url={f.url} /> },
                  { key: "outcome", header: "Last fetch", priority: 1,
                    render: (f) => <Pill tone={outcomeTone(f.last_outcome)}>{f.last_outcome ? f.last_outcome.replace(/_/g, " ") : "never"}</Pill> },
                  { key: "when", header: "When", priority: 2, render: (f) => (f.last_fetch_at ? fmtTime(f.last_fetch_at) : "—") },
                  { key: "items", header: "Items stored", priority: 2, render: (f) => String(f.items_stored) },
                  { key: "error", header: "Reason", priority: 3, render: (f) => f.last_error ?? "—" },
                ]}
              />
            </>
          ) : null}
          {result ? (
            <p role="status">
              Fetched {result.feeds.length} feed{result.feeds.length === 1 ? "" : "s"}: {result.feeds.reduce((n, f) => n + f.new_items, 0)} new item(s).
            </p>
          ) : null}
          {error ? (
            <p role="alert" className="setup-bad">
              {error}
            </p>
          ) : null}
        </Panel>

        <Panel
          title="Items"
          aside={
            feeds.status === "ready" && feeds.data.items.length > 1 ? (
              <select aria-label="Filter by feed" value={feed} onChange={(e) => setFeed(e.target.value)}>
                <option value="">All feeds</option>
                {feeds.data.items.map((f) => (
                  <option key={f.url} value={f.url}>
                    {f.url}
                  </option>
                ))}
              </select>
            ) : null
          }
        >
          {items.status === "loading" ? <Loading /> : null}
          {items.status === "error" ? <ErrorState error={items.error} onRetry={items.reload} /> : null}
          {items.status === "ready" ? (
            <DataTable
              caption="Feed items, newest first"
              rows={items.data.items}
              rowKey={(i) => String(i.id)}
              empty="No items stored yet."
              columns={[
                { key: "title", header: "Title", priority: 1,
                  render: (i) => (i.link ? <ExternalLink url={i.link} text={i.title ?? i.link} /> : (i.title ?? "(untitled)")) },
                { key: "published", header: "Published", priority: 2, render: (i) => i.published ?? "not stated" },
                { key: "feed", header: "Feed", priority: 3, render: (i) => <span className="mono wrap-any">{i.feed_url}</span> },
                { key: "fetched", header: "Fetched", priority: 3, render: (i) => fmtTime(i.fetched_at) },
              ]}
            />
          ) : null}
        </Panel>
      </div>
    </>
  );
}
