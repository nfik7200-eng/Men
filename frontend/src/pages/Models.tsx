import { useState } from "react";
import { api } from "../api/client";
import type { RankingRun, RankingScorer } from "../api/types";
import { RoleHint } from "../auth/RoleHint";
import { useCan } from "../auth/useAuth";
import { DataTable } from "../components/DataTable";
import { humanLabel } from "../components/discovery";
import { ErrorState, FieldError, Loading, PageHeader, Panel, Pill } from "../components/ui";
import { fmtTime } from "../lib/format";
import { useApi } from "../lib/useApi";

const SCORER_TEXT: Record<string, string> = {
  rule_score: "Rule score (baseline)",
  anomaly: "Anomaly score (unsupervised)",
  supervised: "Supervised ranker",
};

function num(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : v.toFixed(2);
}

function Comparison({ run }: { run: RankingRun }) {
  const rows = Object.entries(run.scorers ?? {}).map(([name, s]) => ({ name, ...s }));
  if (run.status !== "completed") {
    return <p>Not evaluated: {run.reason ?? "not enough verified labels"}.</p>;
  }
  return (
    <DataTable
      caption="Scorers compared on the test split"
      rows={rows}
      rowKey={(r) => r.name}
      empty="No scorer was evaluated."
      columns={[
        { key: "name", header: "Scorer", priority: 1, render: (r) => SCORER_TEXT[r.name] ?? humanLabel(r.name) },
        {
          key: "status",
          header: "Status",
          priority: 1,
          render: (r: RankingScorer & { name: string }) =>
            r.status === "evaluated" ? <Pill tone="ok">evaluated</Pill> : <Pill tone="idle">{humanLabel(r.status)}</Pill>,
        },
        { key: "p", header: "Precision", priority: 2, render: (r) => num(r.precision) },
        { key: "r", header: "Recall", priority: 2, render: (r) => num(r.recall) },
        { key: "f1", header: "F1", priority: 2, render: (r) => num(r.f1) },
        { key: "auc", header: "PR-AUC", priority: 2, render: (r) => num(r.pr_auc) },
        { key: "top", header: "Precision@10", priority: 3, render: (r) => num(r.precision_at_10) },
        { key: "brier", header: "Brier", priority: 3, render: (r) => num(r.brier) },
        { key: "fp", header: "False positives", priority: 3, render: (r) => (r.false_positives ?? "—").toString() },
      ]}
    />
  );
}

function metric(m: { macro_pr_auc: number | null; brier: number | null } | null | undefined): string {
  if (!m) return "—";
  return `PR-AUC ${num(m.macro_pr_auc)} · Brier ${num(m.brier)}`;
}

/** Scheduled retraining behind the promotion gate: the switch and each chain's last decision. */
function ContinuousPanel() {
  const status = useApi(() => api.continuousTraining(), []);
  return (
    <Panel title="Continuous retraining">
      {status.status === "loading" ? <Loading /> : null}
      {status.status === "error" ? <ErrorState error={status.error} onRetry={status.reload} /> : null}
      {status.status === "ready" ? (
        <>
          <p>
            <Pill tone={status.data.enabled ? "ok" : "idle"}>{status.data.enabled ? "On" : "Off"}</Pill>{" "}
            Every {status.data.interval_hours} h once {status.data.min_new_examples} new filtered examples exist. A challenger
            replaces the served model only if its PR-AUC is not lower and its Brier score is within{" "}
            {status.data.promotion_brier_tolerance} of the champion&apos;s. Switch it on in Setup.
          </p>
          <DataTable
            caption="Last retraining decision per model and chain"
            rows={status.data.items}
            rowKey={(r) => `${r.kind}:${r.chain}`}
            empty="No models."
            columns={[
              { key: "model", header: "Model", priority: 1, render: (r) => `${humanLabel(r.kind)} · ${r.chain}` },
              { key: "decision", header: "Last decision", priority: 1,
                render: (r) => (r.error ? r.error : r.last_decision ? humanLabel(r.last_decision.decision) : "None yet") },
              { key: "reason", header: "Why", priority: 2, render: (r) => r.last_decision?.reason ?? "—" },
              { key: "challenger", header: "Challenger", priority: 3, render: (r) => metric(r.last_decision?.challenger) },
              { key: "champion", header: "Champion", priority: 3, render: (r) => metric(r.last_decision?.champion) },
              { key: "when", header: "When", priority: 2, render: (r) => (r.last_decision?.time ? fmtTime(r.last_decision.time) : "—") },
            ]}
          />
          <p className="note">Model scores are statistical signals, not verified observations.</p>
        </>
      ) : null}
    </Panel>
  );
}

export default function Models() {
  const status = useApi(() => api.rankingStatus(), []);
  const runs = useApi(() => api.rankingRuns(), []);
  const can = useCan("POST", "/ml/signal-ranking/evaluate");
  const [latest, setLatest] = useState<RankingRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const evaluate = async () => {
    setBusy(true);
    setError(null);
    try {
      setLatest(await api.evaluateRanking());
      status.reload();
      runs.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The evaluation could not run.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <PageHeader title="Models" lede="How candidates are ranked for review, and how well each ranker does against human decisions." />
      <div className="stack">
        <Panel title="Candidate ranking">
          {status.status === "loading" ? <Loading /> : null}
          {status.status === "error" ? <ErrorState error={status.error} onRetry={status.reload} /> : null}
          {status.status === "ready" ? (
            <>
              <p>
                Supervised ranker:{" "}
                {status.data.supervised === "ready" ? <Pill tone="ok">ready</Pill> : <Pill tone="warn">not ready</Pill>}
              </p>
              <p className="muted">{status.data.authority}</p>
              {status.data.labels_excluded_because ? (
                <p className="muted">Review decisions are not used as labels here: {status.data.labels_excluded_because}.</p>
              ) : null}
              <DataTable
                caption="Verified labels per chain against the supervised gate"
                rows={Object.entries(status.data.gate.chains).map(([chain, g]) => ({ chain, ...g }))}
                rowKey={(r) => r.chain}
                empty={`No verified labels yet. The supervised ranker needs ${status.data.gate.needed_per_class} confirmed and ${status.data.gate.needed_per_class} rejected candidates per chain.`}
                columns={[
                  { key: "chain", header: "Chain", priority: 1, render: (r) => humanLabel(r.chain) },
                  { key: "pos", header: "Confirmed", priority: 1, render: (r) => String(r.positive) },
                  { key: "neg", header: "Rejected", priority: 1, render: (r) => String(r.negative) },
                  { key: "need", header: "Needed each", priority: 2, render: (r) => String(r.needed_per_class) },
                  {
                    key: "ready",
                    header: "Supervised",
                    priority: 1,
                    render: (r) => (r.supervised_ready ? <Pill tone="ok">ready</Pill> : <Pill tone="warn">not ready</Pill>),
                  },
                ]}
              />
              <p className="muted">
                {status.data.snapshots} candidate snapshots (features fixed when each candidate was created). Feature version {status.data.feature_version}.
              </p>
            </>
          ) : null}
          <RoleHint can={can} />
          <button type="button" className="btn btn-primary" disabled={busy || !can.allowed} title={can.reason} onClick={() => void evaluate()}>
            Evaluate rankers
          </button>
          <FieldError id="ranking-error" message={error} />
        </Panel>
        {latest ? (
          <Panel title="Latest evaluation">
            <Comparison run={latest} />
          </Panel>
        ) : null}
        <ContinuousPanel />
        <Panel title="Evaluation history">
          {runs.status === "loading" ? <Loading /> : null}
          {runs.status === "error" ? <ErrorState error={runs.error} onRetry={runs.reload} /> : null}
          {runs.status === "ready" ? (
            <DataTable
              caption="Ranking evaluations"
              rows={runs.data.items}
              rowKey={(r) => String(r.id)}
              empty="No evaluation has been run yet."
              columns={[
                { key: "when", header: "When", priority: 1, render: (r) => fmtTime(r.created_at) },
                { key: "status", header: "Status", priority: 1, render: (r) => humanLabel(r.status) },
                { key: "n", header: "Labelled", priority: 2, render: (r) => String(r.labelled ?? 0) },
                { key: "window", header: "Data window", priority: 3, render: (r) => (r.window_from ? `${fmtTime(r.window_from)} to ${fmtTime(r.window_to)}` : "—") },
                { key: "by", header: "By", priority: 3, render: (r) => r.actor },
              ]}
            />
          ) : null}
        </Panel>
      </div>
    </>
  );
}
