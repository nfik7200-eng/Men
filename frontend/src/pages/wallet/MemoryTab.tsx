import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../../api/client";
import type { EvidenceClass, Fact, MemoryProfile, MemoryRefresh, RelationshipItem } from "../../api/types";
import { RoleHint } from "../../auth/RoleHint";
import { useCan } from "../../auth/useAuth";
import { Address } from "../../components/Address";
import { DataTable } from "../../components/DataTable";
import { EvidenceClassBadge, ExplorerRef, ReviewStatePill, SignalStatusPill, humanLabel, valueText } from "../../components/discovery";
import { EmptyState, ErrorState, Loading, Panel, Pill } from "../../components/ui";
import { fmtInt, fmtTime } from "../../lib/format";
import { useApi } from "../../lib/useApi";

const CLASS_ORDER: EvidenceClass[] = ["observed", "reviewed", "statistical", "heuristic", "hypothesis"];

const factValue = (f: Fact): string => valueText(f.value);

function FactSource({ f, chain }: { f: Fact; chain: string }) {
  return (
    <>
      {f.source ?? <span className="muted">not recorded</span>}
      {f.source_tx ? (
        <>
          {" "}
          <ExplorerRef kind="tx" chain={chain} id={f.source_tx} />
        </>
      ) : null}
    </>
  );
}

function CurrentFacts({ facts, chain }: { facts: Fact[]; chain: string }) {
  if (facts.length === 0) return <p className="muted">No current facts are recorded for this entity.</p>;
  // Known classes first; anything the UI does not recognise is kept, never hidden.
  const groups: { id: string; label: string; cls: EvidenceClass | null; facts: Fact[] }[] = CLASS_ORDER.map((cls) => ({
    id: cls, label: humanLabel(cls), cls, facts: facts.filter((f) => f.evidence_class === cls),
  }));
  groups.push({ id: "other", label: "Other (unverified)", cls: null, facts: facts.filter((f) => !CLASS_ORDER.includes(f.evidence_class)) });
  const shown = groups.filter((g) => g.facts.length > 0);
  return (
    <div className="stack disc">
      {shown.map((g) => (
        <section key={g.id} className="stack-tight" aria-label={`${g.label} facts`}>
          <h3>{g.cls ? <EvidenceClassBadge value={g.cls} /> : <Pill tone="warn">Other (unverified)</Pill>}</h3>
          <ul className="list">
            {g.facts.map((f) => (
              <li key={f.id}>
                <strong>{humanLabel(f.key)}</strong>
                <span className="mono wrap-any">{factValue(f)}</span>
                {g.cls ? null : <span className="muted">evidence class: {String(f.evidence_class)}</span>}
                <span className="muted">
                  confirmed {fmtInt(f.confirmations)} time(s) · <FactSource f={f} chain={chain} />
                </span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function History({ facts, chain }: { facts: Fact[]; chain: string }) {
  return (
    <DataTable
      caption="Fact history"
      rows={facts}
      rowKey={(f) => String(f.id)}
      empty="No fact history is recorded."
      columns={[
        { key: "key", header: "Fact", priority: 1, render: (f) => humanLabel(f.key) },
        { key: "value", header: "Value", priority: 2, render: (f) => <span className="mono wrap-any">{factValue(f)}</span> },
        { key: "change", header: "Change", priority: 2, render: (f) => <Pill tone={f.change === "contradiction" ? "bad" : f.change === "invalidated" ? "idle" : "info"}>{f.change}</Pill> },
        { key: "status", header: "Status", priority: 2, render: (f) => <Pill tone={f.status === "current" ? "ok" : f.status === "invalidated" ? "bad" : "idle"}>{f.status}</Pill> },
        { key: "class", header: "Evidence class", priority: 2, render: (f) => <EvidenceClassBadge value={f.evidence_class} /> },
        { key: "source", header: "Source", priority: 3, render: (f) => f.source ?? <span className="muted">not recorded</span> },
        { key: "tx", header: "Source transaction", priority: 3, render: (f) => (f.source_tx ? <ExplorerRef kind="tx" chain={chain} id={f.source_tx} /> : <span className="muted">none</span>) },
        { key: "rec", header: "Recorded", priority: 3, render: (f) => fmtTime(f.recorded_at) },
        { key: "conf", header: "Confirmations", priority: 3, numeric: true, render: (f) => fmtInt(f.confirmations) },
      ]}
    />
  );
}

const endpoint = (r: RelationshipItem, side: "source" | "target"): string | null => {
  const nested = r[side];
  if (nested && typeof nested.ref === "string") return nested.ref;
  const flat = r[`${side}_ref`];
  return typeof flat === "string" ? flat : null;
};

function evidenceTx(r: RelationshipItem): string[] {
  if (!Array.isArray(r.evidence)) return [];
  const out: string[] = [];
  for (const e of r.evidence as unknown[]) {
    if (typeof e === "string") out.push(e);
    else if (typeof e === "object" && e !== null && "ref" in e && typeof e.ref === "string") out.push(e.ref);
  }
  return out;
}

function Relationships({ items, chain }: { items: RelationshipItem[]; chain: string }) {
  const end = (r: RelationshipItem, side: "source" | "target") => {
    const v = endpoint(r, side);
    return v ? <Address value={v} chain={chain} link copy={false} /> : <span className="muted">not recorded</span>;
  };
  return (
    <DataTable
      caption="Relationships"
      rows={items}
      rowKey={(r) => String(r.id ?? `${r.kind}:${endpoint(r, "source")}:${endpoint(r, "target")}`)}
      empty="No relationships are stored for this entity."
      columns={[
        { key: "kind", header: "Kind", priority: 1, render: (r) => humanLabel(r.kind ?? "unknown") },
        { key: "src", header: "Source", priority: 2, render: (r) => end(r, "source") },
        { key: "dst", header: "Target", priority: 2, render: (r) => end(r, "target") },
        { key: "basis", header: "Basis", priority: 2, render: (r) => r.basis ?? <span className="muted">not recorded</span> },
        { key: "conf", header: "Confidence", priority: 3, numeric: true, render: (r) => (typeof r.confidence === "number" ? r.confidence.toFixed(2) : "not recorded") },
        { key: "status", header: "Status", priority: 3, render: (r) => r.status ?? "not recorded" },
        {
          key: "ev",
          header: "Evidence transactions",
          priority: 3,
          render: (r) => {
            const tx = evidenceTx(r);
            return tx.length === 0 ? <span className="muted">none stored</span> : (
              <ul className="plain">
                {tx.map((t) => (
                  <li key={t}><ExplorerRef kind="tx" chain={chain} id={t} /></li>
                ))}
              </ul>
            );
          },
        },
      ]}
    />
  );
}

function RefreshPanel({ chain, onDone }: { chain: string; onDone: () => void }) {
  const can = useCan("POST", `/entities/memory/refresh/${chain}`);
  const [state, setState] = useState<{ busy: boolean; result?: MemoryRefresh; error?: string }>({ busy: false });
  const run = async () => {
    setState({ busy: true });
    try {
      const result = await api.refreshMemory(chain);
      setState({ busy: false, result });
      onDone();
    } catch (err) {
      setState({ busy: false, error: err instanceof ApiError ? err.message : "The memory refresh could not run." });
    }
  };
  const r = state.result;
  return (
    <Panel title="Refresh memory">
      <p className="note">
        Rebuilds memory for this chain from data BIP already stored. Local database only: it never contacts a provider.
      </p>
      <div className="row">
        <button type="button" className="btn" disabled={!can.allowed || state.busy} title={can.reason} onClick={() => void run()}>
          {state.busy ? "Refreshing…" : "Refresh memory"}
        </button>
        <RoleHint can={can} />
      </div>
      <div role="status" aria-live="polite">
        {r ? (
          <>
            <p>
              Refresh finished: {fmtInt(r.entities_touched)} entities touched, {fmtInt(r.facts_new)} new facts,{" "}
              {fmtInt(r.facts_changed)} changed, {fmtInt(r.facts_confirmed)} confirmed, {fmtInt(r.contradictions)} contradiction(s),{" "}
              {fmtInt(r.facts_outdated)} outdated.
            </p>
            {r.truncated_sources.length > 0 ? (
              <p className="notice">Row caps were reached, so memory is incomplete for: {r.truncated_sources.map(humanLabel).join(", ")}.</p>
            ) : null}
          </>
        ) : null}
        {state.error ? <p className="field-error">{state.error}</p> : null}
      </div>
    </Panel>
  );
}

function Profile({ p }: { p: MemoryProfile }) {
  const e = p.entity;
  return (
    <div className="stack disc">
      {!p.in_memory || e === null ? (
        <EmptyState title="Not in intelligence memory yet">
          Memory is filled from stored data by a refresh. This is not a statement about the address, only about what BIP has recorded.
        </EmptyState>
      ) : (
        <>
          <Panel title="Entity">
            <dl className="kv">
              <dt>Kind</dt>
              <dd>{humanLabel(e.kind)}</dd>
              <dt>First seen</dt>
              <dd>{fmtTime(e.first_seen_at)}</dd>
              <dt>Last seen</dt>
              <dd>{fmtTime(e.last_seen_at)}</dd>
              <dt>Memory updated</dt>
              <dd>{fmtTime(e.updated_at)}</dd>
            </dl>
          </Panel>
          {e.contradictions.length > 0 ? (
            <Panel title="Contradictions">
              <div className="notice" role="alert">
                Stored facts disagree. Both sides are kept; neither is assumed correct.
              </div>
              <ul className="list" aria-label="Contradicting facts">
                {e.contradictions.map((f) => (
                  <li key={f.id} className="contradiction">
                    <Pill tone="bad">contradiction</Pill>
                    <strong>{humanLabel(f.key)}</strong>
                    <span className="mono wrap-any">{factValue(f)}</span>
                    <EvidenceClassBadge value={f.evidence_class} />
                    <span className="muted">
                      <FactSource f={f} chain={p.chain} />
                    </span>
                  </li>
                ))}
              </ul>
            </Panel>
          ) : null}
          <Panel title="Current facts">
            <CurrentFacts facts={e.current_facts} chain={p.chain} />
            <p className="note">Only observed and reviewed facts rest on stored records or a person's review. Statistical, heuristic and hypothesis facts are leads, not verified.</p>
          </Panel>
          <Panel title="Fact history" aside={<span className="muted">nothing is overwritten</span>}>
            <History facts={e.history} chain={p.chain} />
          </Panel>
        </>
      )}
      <Panel title="Counterparties">
        <DataTable
          caption="Counterparties"
          rows={p.counterparties}
          rowKey={(c) => c.ref}
          empty="No counterparties in stored transfers."
          columns={[
            { key: "ref", header: "Counterparty", priority: 1, render: (c) => <Address value={c.ref} chain={p.chain} link copy={false} /> },
            { key: "out", header: "Sent to", priority: 2, numeric: true, render: (c) => fmtInt(c.out) },
            { key: "in", header: "Received from", priority: 2, numeric: true, render: (c) => fmtInt(c.in) },
            { key: "first", header: "First", priority: 3, render: (c) => fmtTime(c.first_at) },
            { key: "last", header: "Last", priority: 3, render: (c) => fmtTime(c.last_at) },
          ]}
        />
      </Panel>
      <Panel title="Relationships">
        <Relationships items={p.relationships.items ?? []} chain={p.chain} />
        <p className="note">Relationships describe observed transactions between addresses, not people or organisations.</p>
      </Panel>
      <Panel title="Candidates naming this entity">
        <DataTable
          caption="Candidates"
          rows={p.candidates}
          rowKey={(c) => String(c.id)}
          empty="No candidate names this entity."
          columns={[
            { key: "title", header: "Candidate", priority: 1, render: (c) => <Link to={`/signals/${c.id}`}>{c.title}</Link> },
            { key: "det", header: "Detector", priority: 3, render: (c) => c.detector_id },
            { key: "score", header: "Score", priority: 2, numeric: true, render: (c) => c.score.toFixed(1) },
            { key: "status", header: "Status", priority: 2, render: (c) => <SignalStatusPill status={c.status} /> },
            { key: "review", header: "Review", priority: 2, render: (c) => <ReviewStatePill state={c.review_state} /> },
          ]}
        />
        <p className="note">Candidates are leads for review, not findings and not statements about who controls an address.</p>
      </Panel>
      {p.note ? <p className="note">{p.note}</p> : null}
    </div>
  );
}

/** What BIP remembers about this wallet: facts with their evidence class, history, counterparties, relationships and candidates. */
export default function MemoryTab({ chain, address }: { chain: string; address: string }) {
  const memory = useApi(() => api.memory(chain, address), [chain, address]);
  return (
    <>
      {memory.status === "loading" ? <Loading label="Loading memory…" /> : null}
      {memory.status === "error" ? <ErrorState error={memory.error} onRetry={memory.reload} /> : null}
      <div className="stack disc">
        {memory.status === "ready" ? <Profile p={memory.data} /> : null}
        <RefreshPanel chain={chain} onDone={memory.reload} />
      </div>
    </>
  );
}
