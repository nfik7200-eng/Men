import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { makeSignal } from "../test/discoveryBackend";
import { renderDiscovery } from "../test/renderDiscovery";

const posts = (b: { state: { calls: { method: string; path: string; body: Record<string, unknown> }[] } }, prefix: string) =>
  b.state.calls.filter((c) => c.method === "POST" && c.path.startsWith(prefix));

describe("Stage 4 review lifecycle", () => {
  it("offers only the reasons that fit the chosen state", async () => {
    const user = userEvent.setup();
    renderDiscovery("/signals/1", { signals: [makeSignal()] }, "analyst");
    const form = await screen.findByRole("form", { name: "Review candidate" });
    const reason = within(form).getByLabelText("Reason");
    expect(reason).toBeDisabled(); // a state comes first
    await user.selectOptions(within(form).getByLabelText("Move to"), "confirmed");
    const options = within(reason).getAllByRole("option").map((o) => o.getAttribute("value"));
    expect(options).toContain("evidence_sufficient");
    expect(options).not.toContain("false_positive"); // contradicts a confirmation
  });

  it("requires the duplicated candidate and posts it", async () => {
    const user = userEvent.setup();
    const b = renderDiscovery("/signals/1", { signals: [makeSignal(), makeSignal({ id: 2, key: "k2" })] }, "analyst");
    const form = await screen.findByRole("form", { name: "Review candidate" });
    await user.selectOptions(within(form).getByLabelText("Move to"), "duplicate");
    await user.selectOptions(within(form).getByLabelText("Reason"), "duplicate");
    await user.click(within(form).getByRole("button", { name: "Save review" }));
    const dup = within(form).getByLabelText("Duplicate of candidate number");
    expect(dup).toHaveAttribute("aria-invalid", "true");
    expect(posts(b, "/signals/1/review")).toHaveLength(0);
    await user.type(dup, "2");
    await user.click(within(form).getByRole("button", { name: "Save review" }));
    await screen.findByText("Review state updated.");
    expect(posts(b, "/signals/1/review")[0]?.body).toEqual({ state: "duplicate", reason_code: "duplicate", duplicate_of: 2 });
    const hist = (await screen.findByRole("heading", { level: 2, name: "Decision history" })).closest("section") as HTMLElement;
    expect(await within(hist).findByRole("link", { name: "#2" })).toHaveAttribute("href", "/signals/2");
  });

  it("sends a correction and a wallet label with a confirmation and keeps them in history", async () => {
    const user = userEvent.setup();
    const b = renderDiscovery("/signals/1", { signals: [makeSignal()] }, "analyst");
    const form = await screen.findByRole("form", { name: "Review candidate" });
    await user.selectOptions(within(form).getByLabelText("Move to"), "confirmed");
    await user.selectOptions(within(form).getByLabelText("Reason"), "evidence_sufficient");
    await user.type(within(form).getByLabelText(/Wallet label/), "exchange_hot_wallet");
    await user.type(within(form).getByLabelText("Correction (optional)"), "SYNTHETIC router, not pool");
    await user.click(within(form).getByRole("button", { name: "Save review" }));
    await screen.findByText("Review state updated.");
    expect(posts(b, "/signals/1/review")[0]?.body).toEqual({
      state: "confirmed",
      reason_code: "evidence_sufficient",
      correction: "SYNTHETIC router, not pool",
      wallet_label: "exchange_hot_wallet",
    });
    const hist = (await screen.findByRole("heading", { level: 2, name: "Decision history" })).closest("section") as HTMLElement;
    expect(await within(hist).findByText(/Correction: SYNTHETIC router, not pool/)).toBeInTheDocument();
  });

  it("says whether the review is a training label and why not", async () => {
    renderDiscovery("/signals/1", { signals: [makeSignal()] });
    expect(await screen.findByText("Not a label yet")).toBeInTheDocument();
    expect(screen.getByText(/not marked as holding real network data/)).toBeInTheDocument();
    expect(screen.getByText(/every reviewer is recorded as .local./)).toBeInTheDocument();
  });

  it("explains the ranking without presenting it as a verdict", async () => {
    renderDiscovery("/signals/1", { signals: [makeSignal()] });
    const panel = (await screen.findByRole("heading", { level: 2, name: "Ranking explanation" })).closest("section") as HTMLElement;
    expect(await within(panel).findByText(/0\.42/)).toBeInTheDocument();
    expect(within(panel).getByText(/most unusual: magnitude/)).toBeInTheDocument();
    expect(within(panel).getByText(/Not ready: too few verified labels/)).toBeInTheDocument();
    expect(within(panel).getByText(/a person confirms or rejects, never the model/)).toBeInTheDocument();
  });
});

describe("Models page", () => {
  it("shows the supervised gate as not ready and records an evaluation", async () => {
    const user = userEvent.setup();
    const b = renderDiscovery("/models", {}, "analyst");
    expect(await screen.findByText(/No verified labels yet/)).toBeInTheDocument();
    expect(screen.getAllByText("not ready").length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.getByRole("button", { name: "Evaluate rankers" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Evaluate rankers" }));
    expect(await screen.findByText(/Not evaluated: the test split needs/)).toBeInTheDocument();
    expect(posts(b, "/ml/signal-ranking/evaluate")).toHaveLength(1);
  });

  it("disables evaluation for a viewer", async () => {
    const b = renderDiscovery("/models", {}, "viewer");
    await waitFor(() => expect(screen.getByRole("button", { name: "Evaluate rankers" })).toBeDisabled());
    expect(posts(b, "/ml/signal-ranking/evaluate")).toHaveLength(0);
  });
});

describe("feed freshness", () => {
  it("reports historical and expansion runs separately from live passes", async () => {
    const { FreshnessPanel } = await import("./Signals");
    const { render } = await import("@testing-library/react");
    render(
      <FreshnessPanel
        freshness={{
          last_run_at: null,
          last_run_status: null,
          age_seconds: null,
          stale: true,
          last_historical_run_at: "2026-11-14T10:00:00+00:00",
          last_historical_as_of: "2026-10-01T00:00:00+00:00",
          last_expansion_run_at: "2026-11-14T11:00:00+00:00",
        }}
        scope={null}
      />,
    );
    expect(screen.getByText("Last historical pass")).toBeInTheDocument();
    expect(screen.getAllByText(/does not count as current/)).toHaveLength(2);
    expect(screen.getByText(/Discovery has not run on this database/)).toBeInTheDocument();
  });
});

describe("Models page with an evaluated run", () => {
  it("compares the scorers on the test split", async () => {
    const run = {
      id: 1, created_at: "2026-11-14T10:00:00+00:00", actor: "analyst-a", status: "completed" as const,
      feature_version: "1", ranker_version: "1", dataset_hash: "SYNTHETIC", window_from: null, window_to: null,
      labelled: 120, label_counts: { ethereum: { positive: 60, negative: 60 } },
      scorers: {
        rule_score: { kind: "baseline", status: "evaluated", precision: 0.5, recall: 0.4, f1: 0.44, pr_auc: 0.52, precision_at_10: 0.5, brier: 0.3, false_positives: 9 },
        supervised: { kind: "supervised", status: "not_ready", reason: "SYNTHETIC" },
      },
    };
    const user = userEvent.setup();
    const b = renderDiscovery("/models", {}, "analyst");
    b.state.rankingRuns = [];
    const { api } = await import("../api/client");
    const spy = (await import("vitest")).vi.spyOn(api, "evaluateRanking").mockResolvedValue(run);
    await waitFor(() => expect(screen.getByRole("button", { name: "Evaluate rankers" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Evaluate rankers" }));
    const table = await screen.findByRole("table", { name: "Scorers compared on the test split" });
    expect(within(table).getByText("Rule score (baseline)")).toBeInTheDocument();
    expect(within(table).getByText("0.52")).toBeInTheDocument();
    expect(within(table).getByText("not ready")).toBeInTheDocument();
    spy.mockRestore();
  });
});
