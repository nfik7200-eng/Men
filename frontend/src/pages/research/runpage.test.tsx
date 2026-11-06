import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import App from "../../App";
import { installFakeBackend } from "../../test/fakeBackend";

const RUN1 = "aaaaaaaa-0000-0000-0000-000000000001";
const RUN2 = "aaaaaaaa-0000-0000-0000-000000000002";

function Where() {
  return <output aria-label="location">{useLocation().pathname}</output>;
}
const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
      <Where />
    </MemoryRouter>,
  );

describe("research history and run page", () => {
  it("lists persisted runs on the research index", async () => {
    installFakeBackend({}, { seeded: true });
    renderAt("/research");
    const link = await screen.findByRole("link", { name: "q" });
    expect(link).toHaveAttribute("href", `/research/runs/${RUN1}`);
  });

  it("polls a deep-linked run to completion and shows the report with its provenance", async () => {
    installFakeBackend();
    renderAt(`/research/runs/${RUN1}`);
    expect(await screen.findByText("collecting evidence")).toBeInTheDocument();
    expect(await screen.findByText("completed with limitations", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(await screen.findByText(/Deterministic summary/)).toBeInTheDocument();
    const prov = screen.getByRole("region", { name: "Provenance" });
    expect(prov).toHaveTextContent(RUN1);
    expect(prov).toHaveTextContent("Not recorded in this report version"); // old report shape
  });

  it("shows feature digest and model dataset version for traced reports", async () => {
    installFakeBackend({}, { traced: true });
    renderAt(`/research/runs/${RUN1}`);
    const prov = await screen.findByRole("region", { name: "Provenance" }, { timeout: 4000 });
    expect(await screen.findByText("1.1.0", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(prov).toHaveTextContent("wallet-classifier-ethereum 1.0.1");
    expect(prov).toHaveTextContent("dddddddd…dddddd");
    expect(prov).toHaveTextContent("eeeeeeee…eeeeee");
  });

  it("says when a run does not exist instead of spinning", async () => {
    installFakeBackend();
    renderAt("/research/runs/missing");
    expect(await screen.findByText("Research run not found")).toBeInTheDocument();
  });

  it("reruns as a new linked run and adds a run to a case", async () => {
    const user = userEvent.setup();
    const fake = installFakeBackend();
    fake.cases.push({ id: 5, title: "Case A", description: null, status: "open", subject_chain: null, subject_address: null, created_at: "", updated_at: "", findings: [] });
    renderAt(`/research/runs/${RUN1}`);
    await screen.findByText("completed with limitations", {}, { timeout: 4000 });
    await user.selectOptions(screen.getByLabelText("Case"), "5");
    await user.click(screen.getByRole("button", { name: "Add to case" }));
    expect(await screen.findByText(/Added to Case A/)).toBeInTheDocument();
    expect(fake.caseLinks.get(5)?.[0]?.run_id).toBe(RUN1);
    await user.click(screen.getByRole("button", { name: "Rerun" }));
    expect(await screen.findByLabelText("location")).toHaveTextContent(`/research/runs/${RUN2}`);
    expect(await screen.findByRole("link", { name: /Rerun of/ })).toHaveAttribute("href", `/research/runs/${RUN1}`);
  });

  it("labels runs over offline fixture data", async () => {
    installFakeBackend({}, { fixtureRuns: true });
    renderAt(`/research/runs/${RUN1}`);
    expect(await screen.findByText(/Demo data: offline fixtures/)).toBeInTheDocument();
  });
});
