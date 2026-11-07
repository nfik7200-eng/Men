import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import App from "../App";
import { ETH_WALLET, installFakeBackend } from "../test/fakeBackend";

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );

const CASE = { id: 5, title: "Case A", description: null, status: "open", subject_chain: null, subject_address: null, created_at: "", updated_at: "", findings: [] };
const RUN = "aaaaaaaa-0000-0000-0000-000000000001";

describe("cases, findings, labels and provider detail", () => {
  it("shows research runs linked to a case", async () => {
    const fake = installFakeBackend();
    fake.cases.push({ ...CASE });
    fake.caseLinks.set(5, [{ run_id: RUN, note: "supports A", attached_at: "2026-11-06T00:00:00Z", state: "completed", question: "Who is this?", chain: "ethereum", address: ETH_WALLET, created_at: "" }]);
    renderAt("/investigations/5");
    const panel = await screen.findByRole("region", { name: "Research runs" });
    expect(await within(panel).findByRole("link", { name: "Who is this?" })).toHaveAttribute("href", `/research/runs/${RUN}`);
    expect(within(panel).getByText("supports A")).toBeInTheDocument();
  });

  it("attaches an anomaly finding to a case from the Evidence tab", async () => {
    const user = userEvent.setup();
    const fake = installFakeBackend({ finding: true });
    fake.cases.push({ ...CASE });
    renderAt(`/wallets/ethereum/${ETH_WALLET}/evidence`);
    await user.click(await screen.findByText("Method, evidence and limitations"));
    await user.selectOptions(await screen.findByLabelText("Case for finding fnd-1"), "5");
    await user.click(screen.getByRole("button", { name: "Attach to case" }));
    expect(await screen.findByText(/Attached to Case A/)).toBeInTheDocument();
    expect(fake.attached).toEqual([{ finding_id: "fnd-1", investigation_id: 5 }]);
  });

  it("curates labels with provenance and shows progress against the training gate", async () => {
    const user = userEvent.setup();
    const fake = installFakeBackend();
    renderAt("/research");
    await user.click(screen.getByRole("tab", { name: "Labels" }));
    expect(await screen.findAllByText(/0 of 30 distinct wallets/)).toHaveLength(2); // Ethereum and Solana
    await user.type(screen.getByLabelText("Address"), ETH_WALLET);
    await user.selectOptions(await screen.findByLabelText("Label"), "defi_trader");
    await user.selectOptions(screen.getByLabelText("Status"), "weak");
    await user.type(screen.getByLabelText("Source"), "public exchange list");
    await user.click(screen.getByRole("button", { name: "Add label" }));
    expect(await screen.findByRole("cell", { name: "defi trader" })).toBeInTheDocument();
    expect(fake.labels[0]?.source).toBe("public exchange list");
    expect(screen.getAllByText(/0 of 30 distinct wallets/)).toHaveLength(2); // weak labels do not train
    await user.type(screen.getByLabelText("Reviewer"), "analyst-1");
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByText(/1 of 30 distinct wallets/)).toBeInTheDocument();
  });

  it("opens a provider detail page with its capability history", async () => {
    installFakeBackend();
    renderAt("/providers/publicnode-eth");
    expect(await screen.findByRole("heading", { level: 1, name: "Provider publicnode-eth" })).toBeInTheDocument();
    expect(await screen.findByText("No capability observations recorded yet.")).toBeInTheDocument();
  });
});
