import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { manifest } from "../test/discoveryBackend";
import { renderDiscovery } from "../test/renderDiscovery";

const heading = () => screen.findByRole("heading", { level: 1, name: "Coverage" });
const panel = (name: string) => {
  const p = screen.getByRole("heading", { level: 2, name }).closest("section");
  if (!p) throw new Error(`no panel ${name}`);
  return p;
};

describe("coverage", () => {
  it("shows honest empty states: cursor not set, no run, nothing invented", async () => {
    renderDiscovery("/coverage");
    await heading();
    expect((await screen.findAllByText("No run recorded")).length).toBe(2);
    expect(screen.getAllByText("Not started: the cursor is not set")).toHaveLength(2);
    expect(screen.getAllByText("unknown (no run recorded)")).toHaveLength(2);
    expect(screen.getByText(/No coverage gaps recorded/)).toBeInTheDocument();
    expect(screen.getByText("ordinary transactions below the value threshold (counted, not stored)")).toBeInTheDocument();
  });

  it("lists the detector catalogue with coverage, limitations, cost and confidence cap", async () => {
    renderDiscovery("/coverage");
    await heading();
    const card = await screen.findByRole("article", { name: "Large transfer" });
    expect(within(card).getByText("Transfers of ingested wallets.")).toBeInTheDocument();
    expect(within(card).getByText("No internal transfers.")).toBeInTheDocument();
    expect(within(card).getByText("database only")).toBeInTheDocument();
    expect(within(card).getByText("confidence cap 0.80")).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "New liquidity pool" })).toBeInTheDocument();
  });

  it("shows the last run, lag, gaps and limits for both chains, and recognised programs with links", async () => {
    renderDiscovery("/coverage", {
      origin: "offline_fixture",
      eth: {
        data_origin: "offline_fixture", discovery_version: "1", version: "3", chain: "ethereum", last_block_scanned: 119,
        last_run: manifest({ status: "partial", message: "SYNTHETIC: stopped at the call budget." }),
        limits: { blocks_per_run: 20, max_rpc_calls_per_run: 150 }, not_covered: ["SYNTHETIC not covered item"],
      },
      sol: {
        data_origin: "offline_fixture", discovery_version: "1", version: "1", chain: "solana", last_slot_scanned: 5000,
        last_run: manifest({ status: "succeeded", message: "SYNTHETIC solana run", first_block: null, last_block: null, first_slot: 4990, last_slot: 5000, finalized_head: 5010, lag_slots: 10, slots_scanned: 10, rpc_calls: 12, providers: ["solana-public"] }),
        coverage_gaps: [{ from_slot: 100, to_slot: 200, reason: "lag exceeded max_lag_slots", at: "2026-11-09T00:00:00+00:00" }],
        limits: { blocks_per_run: 10, run_deadline_s: 90 }, not_covered: ["SPL token transfers"],
        recognised_programs: [{ program_id: "SynthProgram1111111111111111111111111111111", name: "SYNTHETIC AMM", source_url: "https://example.org/amm", verified_at: "2026-10-01" }],
      },
    });
    await heading();
    expect(await screen.findByText(/Demo data: offline fixtures/)).toBeInTheDocument();
    const eth = await screen.findByRole("region", { name: "Ethereum last run" });
    expect(within(eth).getByText("partial")).toBeInTheDocument();
    expect(within(eth).getByText("SYNTHETIC: stopped at the call budget.")).toBeInTheDocument();
    expect(within(eth).getByText("blocks 100 to 119")).toBeInTheDocument();
    expect(within(eth).getByText("publicnode-eth")).toBeInTheDocument();
    expect(within(eth).getByText("61")).toBeInTheDocument();
    const ethPanel = panel("Ethereum collector");
    expect(within(ethPanel).getByText("11 blocks")).toBeInTheDocument(); // finalized head 130 - cursor 119
    const sol = panel("Solana collector");
    expect(within(sol).getByText("10 slots")).toBeInTheDocument();
    expect(within(sol).getByText(/slots 100 to 200: lag exceeded max_lag_slots/)).toBeInTheDocument();
    const link = within(sol).getByRole("link", { name: "https://example.org/amm" });
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(sol).getByText("2026-10-01")).toBeInTheDocument();
    expect(within(sol).getByText("SPL token transfers")).toBeInTheDocument();
    expect(within(sol).getByText("run deadline s")).toBeInTheDocument();
  });

  it("shows an error state for one collector without hiding the other", async () => {
    renderDiscovery("/coverage", { failures: { "GET /discovery/collector/solana": { status: 500, code: "internal_error", message: "SYNTHETIC solana status failure" } } });
    expect(await screen.findByText("SYNTHETIC solana status failure")).toBeInTheDocument();
    expect(await screen.findByText("Not started: the cursor is not set")).toBeInTheDocument();
  });

  it("never runs a collector on its own", async () => {
    const backend = renderDiscovery("/coverage", {}, "admin");
    await heading();
    await screen.findAllByText("No run recorded");
    expect(backend.state.calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("disables Run once for analysts and viewers and says it needs the admin role", async () => {
    const backend = renderDiscovery("/coverage", {}, "analyst");
    await heading();
    const buttons = await screen.findAllByRole("button", { name: /Run (Ethereum|Solana) collector once/ });
    expect(buttons).toHaveLength(2);
    for (const b of buttons) {
      expect(b).toBeDisabled();
      expect(b).toHaveAttribute("title", "Needs the admin role. You are signed in as analyst.");
    }
    expect(screen.getAllByText("Needs the admin role. You are signed in as analyst.").length).toBeGreaterThanOrEqual(2);
    expect(backend.state.calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("asks for confirmation that says what contacting a provider means, and Cancel runs nothing", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/coverage", {}, "admin");
    await user.click(await screen.findByRole("button", { name: "Run Ethereum collector once" }));
    const dialog = await screen.findByRole("dialog", { name: "Run the Ethereum collector once?" });
    expect(dialog).toHaveTextContent("This contacts a public RPC provider.");
    expect(dialog).toHaveTextContent("capped at 150 requests and 90 seconds");
    expect(dialog).toHaveTextContent("finalized data only");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(backend.state.calls.filter((c) => c.method === "POST")).toHaveLength(0);
    // Escape closes it too
    await user.click(screen.getByRole("button", { name: "Run Ethereum collector once" }));
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(backend.state.calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("runs exactly once on confirm and shows the returned manifest", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/coverage", { collectResult: manifest({ status: "partial", message: "SYNTHETIC manual run result", rpc_calls: 150 }) }, "admin");
    await user.click(await screen.findByRole("button", { name: "Run Solana collector once" }));
    const dialog = await screen.findByRole("dialog", { name: "Run the Solana collector once?" });
    await user.click(within(dialog).getByRole("button", { name: "Contact provider and run once" }));
    const result = await screen.findByRole("region", { name: "Solana manual run result" });
    expect(within(result).getByText("SYNTHETIC manual run result")).toBeInTheDocument();
    expect(within(result).getByText("150")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    const posts = backend.state.calls.filter((c) => c.method === "POST");
    expect(posts.map((p) => p.path)).toEqual(["/discovery/collect/solana"]);
  });

  it("shows a refused run (another ingestion in progress) instead of a result", async () => {
    const user = userEvent.setup();
    renderDiscovery("/coverage", { failures: { "POST /discovery/collect/ethereum": { status: 409, code: "ingestion_busy", message: "Another ingestion run is in progress. Try again shortly." } } }, "admin");
    await user.click(await screen.findByRole("button", { name: "Run Ethereum collector once" }));
    await user.click(await screen.findByRole("button", { name: "Contact provider and run once" }));
    expect(await screen.findByText("Another ingestion run is in progress. Try again shortly.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Ethereum manual run result" })).toBeNull();
  });
});
