import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  SYNTHETIC_ADDR,
  SYNTHETIC_ADDR_2,
  SYNTHETIC_TX,
  makeFact,
  makeMemory,
  makeSignal,
} from "../test/discoveryBackend";
import { renderDiscovery } from "../test/renderDiscovery";
import type { DiscoveryRunRecord } from "../api/types";

const WALLET = `/wallets/ethereum/${SYNTHETIC_ADDR}/memory`;
const posts = (b: { state: { calls: { method: string; path: string; body: Record<string, unknown> }[] } }, prefix: string) =>
  b.state.calls.filter((c) => c.method === "POST" && c.path.startsWith(prefix));

const populated = () =>
  makeMemory({
    in_memory: true,
    entity: {
      chain: "ethereum", ref: SYNTHETIC_ADDR, kind: "wallet", first_seen_at: "2026-11-01T00:00:00+00:00", last_seen_at: "2026-11-09T00:00:00+00:00", updated_at: "2026-11-09T01:00:00+00:00",
      current_facts: [
        makeFact({ id: 1, key: "first_funder", evidence_class: "observed" }),
        makeFact({ id: 2, key: "behaviour_pattern", value: { label: "SYNTHETIC pattern" }, evidence_class: "hypothesis", source_tx: null }),
      ],
      contradictions: [makeFact({ id: 3, key: "account_type", value: { type: "contract" }, change: "contradiction", evidence_class: "heuristic" })],
      history: [
        makeFact({ id: 1, key: "first_funder" }),
        makeFact({ id: 4, key: "account_type", value: { type: "eoa" }, status: "superseded", change: "changed" }),
        makeFact({ id: 5, key: "label_hint", status: "outdated", change: "new" }),
      ],
    },
    counterparties: [{ ref: SYNTHETIC_ADDR_2, out: 3, in: 1, first_at: "2026-11-02T00:00:00+00:00", last_at: "2026-11-08T00:00:00+00:00" }],
    relationships: {
      total: 1,
      items: [{ id: 1, kind: "funded_by", source: { kind: "wallet", ref: SYNTHETIC_ADDR }, target: { kind: "wallet", ref: SYNTHETIC_ADDR_2 }, basis: "SYNTHETIC first inbound transfer", confidence: 0.7, status: "active", evidence: [{ kind: "transaction", chain: "ethereum", ref: SYNTHETIC_TX }] }],
    },
    candidates: [{ id: 7, title: "SYNTHETIC candidate on this entity", detector_id: "large_transfer", score: 55, status: "open", review_state: "monitoring" }],
  });

describe("wallet memory tab", () => {
  it("shows the not-in-memory empty state and a refresh control", async () => {
    renderDiscovery(WALLET, {}, "analyst");
    expect(await screen.findByText("Not in intelligence memory yet")).toBeInTheDocument();
    expect(screen.getByText(/Memory is filled from stored data by a refresh/)).toBeInTheDocument();
    expect(screen.getByText(/SYNTHETIC note/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Refresh memory" })).toBeEnabled());
    expect(screen.queryByText("Needs the analyst role. You are signed in as analyst.")).toBeNull();
  });

  it("renders facts by evidence class, contradictions, superseded history, counterparties, relationships and candidates", async () => {
    renderDiscovery(WALLET, { memory: { [`ethereum:${SYNTHETIC_ADDR}`]: populated() } });
    const current = (await screen.findByRole("heading", { level: 2, name: "Current facts" })).closest("section") as HTMLElement;
    expect(within(current).getByText("Observed")).toBeInTheDocument();
    expect(within(current).getByText("Hypothesis (not verified)")).toBeInTheDocument();
    const contra = screen.getByRole("heading", { level: 2, name: "Contradictions" }).closest("section") as HTMLElement;
    expect(within(contra).getByText(/Stored facts disagree/)).toBeInTheDocument();
    expect(within(contra).getByText("account type")).toBeInTheDocument();
    expect(within(contra).getByText("Heuristic (not verified)")).toBeInTheDocument();
    const hist = screen.getByRole("table", { name: "Fact history" });
    expect(within(hist).getByText("superseded")).toBeInTheDocument();
    expect(within(hist).getByText("outdated")).toBeInTheDocument();
    expect(within(hist).getByText("changed")).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "Counterparties" })).getByText("3")).toBeInTheDocument();
    const rel = screen.getByRole("table", { name: "Relationships" });
    expect(within(rel).getByText("SYNTHETIC first inbound transfer")).toBeInTheDocument();
    expect(within(rel).getByRole("link", { name: SYNTHETIC_TX })).toBeInTheDocument();
    const cand = screen.getByRole("link", { name: "SYNTHETIC candidate on this entity" });
    expect(cand).toHaveAttribute("href", "/signals/7");
    expect(screen.getByText("review: monitoring")).toBeInTheDocument();
  });

  it("keeps facts with an unrecognised evidence class in an unverified group", async () => {
    const m = populated();
    m.entity?.current_facts.push(makeFact({ id: 9, key: "mystery_fact", evidence_class: "novel_class" as never }));
    renderDiscovery(WALLET, { memory: { [`ethereum:${SYNTHETIC_ADDR}`]: m } });
    const group = (await screen.findByRole("region", { name: "Other (unverified) facts" })) as HTMLElement;
    expect(within(group).getByText("mystery fact")).toBeInTheDocument();
  });

  it("shows an error state when memory cannot be read", async () => {
    renderDiscovery(WALLET, { failures: { [`GET /entities/memory/ethereum/${SYNTHETIC_ADDR}`]: { status: 500, code: "internal", message: "SYNTHETIC memory failure" } } });
    expect(await screen.findByText("SYNTHETIC memory failure")).toBeInTheDocument();
  });

  it("lets an analyst refresh memory with no request body, and says it is local only", async () => {
    const b = renderDiscovery(WALLET, {}, "analyst");
    const button = await screen.findByRole("button", { name: "Refresh memory" });
    expect(screen.getByText(/never contacts a provider/)).toBeInTheDocument();
    await userEvent.click(button);
    expect(await screen.findByText(/Refresh finished: 4 entities touched, 6 new facts/)).toBeInTheDocument();
    expect(posts(b, "/entities/memory/refresh/ethereum")).toEqual([{ method: "POST", path: "/entities/memory/refresh/ethereum", body: {} }]);
  });

  it("disables refresh for a viewer with a role hint", async () => {
    const b = renderDiscovery(WALLET, {}, "viewer");
    await screen.findByText("Not in intelligence memory yet");
    await waitFor(() => expect(screen.getByRole("button", { name: "Refresh memory" })).toBeDisabled());
    expect(screen.getByText("Needs the analyst role. You are signed in as viewer.")).toBeInTheDocument();
    expect(posts(b, "/entities")).toHaveLength(0);
  });
});

describe("signal review", () => {
  const decision = { id: 1, from_state: "new" as const, to_state: "pending_review" as const, reason_code: "needs_more_data" as const, note: "SYNTHETIC note", actor: "analyst-a", decided_at: "2026-11-10T08:00:00+00:00" };

  it("renders the review state and decision history", async () => {
    renderDiscovery("/signals/1", { signals: [makeSignal({ review_state: "pending_review" })], decisions: { 1: [decision] } });
    const hist = (await screen.findByRole("heading", { level: 2, name: "Decision history" })).closest("section") as HTMLElement;
    expect(within(hist).getByText("new to pending review")).toBeInTheDocument();
    expect(within(hist).getByText("needs more data")).toBeInTheDocument();
    expect(within(hist).getByText("analyst-a")).toBeInTheDocument();
    expect(screen.getAllByText("review: pending review").length).toBeGreaterThan(0);
  });

  it("lists decisions newest first even though the API returns oldest first", async () => {
    const older = { ...decision, id: 1, note: "SYNTHETIC older", decided_at: "2026-11-09T08:00:00+00:00" };
    const newer = { ...decision, id: 2, note: "SYNTHETIC newer", decided_at: "2026-11-10T08:00:00+00:00" };
    renderDiscovery("/signals/1", { signals: [makeSignal()], decisions: { 1: [older, newer] } });
    const table = await screen.findByRole("table", { name: "Review decisions" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("SYNTHETIC newer");
    expect(rows[1]).toHaveTextContent("SYNTHETIC older");
  });

  it("ties validation errors to the control and moves focus to it", async () => {
    const user = userEvent.setup();
    renderDiscovery("/signals/1", { signals: [makeSignal()] }, "analyst");
    const form = await screen.findByRole("form", { name: "Review candidate" });
    await user.click(within(form).getByRole("button", { name: "Save review" }));
    const state = within(form).getByLabelText("Move to");
    expect(state).toHaveAttribute("aria-invalid", "true");
    expect(state).toHaveAttribute("aria-describedby", "signal-review-invalid");
    expect(state).toHaveFocus();
    await user.selectOptions(state, "rejected");
    await user.selectOptions(within(form).getByLabelText("Reason"), "other");
    await user.click(within(form).getByRole("button", { name: "Save review" }));
    const note = within(form).getByLabelText(/Note/);
    expect(note).toHaveAttribute("aria-invalid", "true");
    expect(note).toHaveFocus();
    expect(state).not.toHaveAttribute("aria-invalid");
  });

  it("shows an empty decision history", async () => {
    renderDiscovery("/signals/1", { signals: [makeSignal()] });
    expect(await screen.findByText("No review decision has been recorded yet.")).toBeInTheDocument();
  });

  it("requires a note for reason Other, then posts the exact body and refreshes history", async () => {
    const user = userEvent.setup();
    const b = renderDiscovery("/signals/1", { signals: [makeSignal()] }, "analyst");
    const form = await screen.findByRole("form", { name: "Review candidate" });
    await user.selectOptions(within(form).getByLabelText("Move to"), "rejected");
    await user.selectOptions(within(form).getByLabelText("Reason"), "other");
    await user.click(within(form).getByRole("button", { name: "Save review" }));
    expect(await screen.findByText("A note is required when the reason is Other.")).toBeInTheDocument();
    expect(posts(b, "/signals/1/review")).toHaveLength(0);
    await user.type(within(form).getByLabelText(/Note/), "SYNTHETIC reason");
    await user.click(within(form).getByRole("button", { name: "Save review" }));
    expect(await screen.findByText("Review state updated.")).toBeInTheDocument();
    expect(posts(b, "/signals/1/review")[0]?.body).toEqual({ state: "rejected", reason_code: "other", note: "SYNTHETIC reason" });
    const hist = (await screen.findByRole("heading", { level: 2, name: "Decision history" })).closest("section") as HTMLElement;
    expect(await within(hist).findByText("SYNTHETIC reason")).toBeInTheDocument();
  });

  it("omits the note when none is given and keeps dismiss and promote", async () => {
    const user = userEvent.setup();
    const b = renderDiscovery("/signals/1", { signals: [makeSignal()] }, "analyst");
    const form = await screen.findByRole("form", { name: "Review candidate" });
    await user.selectOptions(within(form).getByLabelText("Move to"), "monitoring");
    await user.selectOptions(within(form).getByLabelText("Reason"), "evidence_sufficient");
    await user.click(within(form).getByRole("button", { name: "Save review" }));
    await screen.findByText("Review state updated.");
    expect(posts(b, "/signals/1/review")[0]?.body).toEqual({ state: "monitoring", reason_code: "evidence_sufficient" });
    expect(screen.getByRole("form", { name: "Dismiss signal" })).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Promote signal" })).toBeInTheDocument();
  });

  it("shows a server rejection", async () => {
    const user = userEvent.setup();
    renderDiscovery("/signals/1", { signals: [makeSignal()], failures: { "POST /signals/1/review": { status: 422, code: "invalid_input", message: "SYNTHETIC refused" } } }, "analyst");
    const form = await screen.findByRole("form", { name: "Review candidate" });
    await user.selectOptions(within(form).getByLabelText("Move to"), "archived");
    await user.selectOptions(within(form).getByLabelText("Reason"), "duplicate");
    await user.click(within(form).getByRole("button", { name: "Save review" }));
    expect(await screen.findByText("SYNTHETIC refused")).toBeInTheDocument();
  });

  it("disables the form for a viewer with a role hint", async () => {
    const b = renderDiscovery("/signals/1", { signals: [makeSignal()] }, "viewer");
    const form = await screen.findByRole("form", { name: "Review candidate" });
    await waitFor(() => expect(within(form).getByRole("button", { name: "Save review" })).toBeDisabled());
    expect(within(form).getByText("Needs the analyst role. You are signed in as viewer.")).toBeInTheDocument();
    expect(posts(b, "/signals/1/review")).toHaveLength(0);
  });
});

describe("signals queue review state", () => {
  it("shows a review pill and filters by review state through the query", async () => {
    const user = userEvent.setup();
    const b = renderDiscovery("/signals", { signals: [makeSignal({ id: 1, title: "SYNTHETIC one", review_state: "monitoring" }), makeSignal({ id: 2, key: "k2", title: "SYNTHETIC two" })] });
    const table = await screen.findByRole("table", { name: "Signals" });
    expect(within(table).getByText("review: monitoring")).toBeInTheDocument();
    expect(within(table).getByText("review: new")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Review state"), "monitoring");
    await waitFor(() => expect(within(screen.getByRole("table", { name: "Signals" })).queryByText("SYNTHETIC two")).toBeNull());
    expect(b.state.calls.some((c) => c.path.includes("review_state=monitoring"))).toBe(true);
  });
});

describe("coverage: run history and expansion", () => {
  const run: DiscoveryRunRecord = {
    id: 9, chain: "ethereum", status: "partial", started_at: "2026-11-10T07:00:00+00:00", finished_at: "2026-11-10T07:01:00+00:00",
    summary: { policy: { id: "shared_neighbourhood" }, seeds_used: 2, entities_examined: 5, relationship_rows_read: 9, truncated: ["relationship_rows"], detectors: { "x:ethereum": { created: 2, merged: 1 } } },
  };

  it("shows empty history, policies, budgets and the local-only statement", async () => {
    renderDiscovery("/coverage");
    expect(await screen.findByRole("heading", { level: 2, name: "Run history" })).toBeInTheDocument();
    expect((await screen.findAllByText("No run recorded.")).length).toBe(2);
    expect(await screen.findByRole("group", { name: "Policy shared neighbourhood" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Policy deployer network" })).toBeInTheDocument();
    const budgets = screen.getByLabelText("Expansion budgets");
    expect(within(budgets).getByText("5,000")).toBeInTheDocument();
    expect(screen.getAllByText(/never contacts a provider/i).length).toBeGreaterThan(0);
  });

  it("renders runs, including a truncated one, and the demo banner", async () => {
    renderDiscovery("/coverage", { runs: [run], origin: "offline_fixture" });
    const table = await screen.findByRole("table", { name: "Discovery run history" });
    expect(within(table).getByText("partial")).toBeInTheDocument();
    expect(within(table).getByText("relationship rows")).toBeInTheDocument();
    expect(within(table).getByText("2 created, 1 merged")).toBeInTheDocument();
    expect((await screen.findAllByText(/Demo data: offline fixtures/)).length).toBeGreaterThan(0);
  });

  it("shows errors for both panels", async () => {
    renderDiscovery("/coverage", {
      failures: {
        "GET /discovery/runs": { status: 500, code: "internal", message: "SYNTHETIC runs failure" },
        "GET /discovery/expansion": { status: 500, code: "internal", message: "SYNTHETIC expansion failure" },
      },
    });
    expect(await screen.findByText("SYNTHETIC runs failure")).toBeInTheDocument();
    expect(await screen.findByText("SYNTHETIC expansion failure")).toBeInTheDocument();
  });

  it("runs a policy on a chain with the exact body", async () => {
    const b = renderDiscovery("/coverage", {}, "analyst");
    const button = await screen.findByRole("button", { name: "Run deployer network on Solana" });
    await userEvent.click(button);
    expect(await screen.findByText(/Run \d+ succeeded on Solana: 3 seeds/)).toBeInTheDocument();
    expect(posts(b, "/discovery/expand/")).toEqual([{ method: "POST", path: "/discovery/expand/solana", body: { policy: "deployer_network" } }]);
  });

  it("reports a busy discovery run", async () => {
    renderDiscovery("/coverage", { failures: { "POST /discovery/expand/ethereum": { status: 409, code: "discovery_busy", message: "A discovery pass is running. Try again." } } }, "analyst");
    await userEvent.click(await screen.findByRole("button", { name: "Run shared neighbourhood on Ethereum" }));
    expect(await screen.findByText("A discovery pass is running. Try again.")).toBeInTheDocument();
  });

  it("disables run controls for a viewer", async () => {
    const b = renderDiscovery("/coverage", {}, "viewer");
    const button = await screen.findByRole("button", { name: "Run shared neighbourhood on Ethereum" });
    await waitFor(() => expect(button).toBeDisabled());
    expect(screen.getAllByText("Needs the analyst role. You are signed in as viewer.").length).toBeGreaterThanOrEqual(4);
    expect(posts(b, "/discovery/expand/")).toHaveLength(0);
  });
});
