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

describe("AI research workspace", () => {
  it("classifies a wallet and labels predictions, rules and synthetic models", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderAt(`/research?chain=ethereum&address=${ETH_WALLET}`);
    await user.click(screen.getByRole("button", { name: "Classify wallet" }));
    const result = await screen.findByLabelText("Classification result");
    expect(within(result).getByText("externally owned account")).toBeInTheDocument();
    // synthetic-trained models are capped at low confidence, whatever their score
    expect(within(result).getAllByText("low confidence")).toHaveLength(2);
    expect(within(result).getByText(/model score 0.93 \(not certainty\)/)).toBeInTheDocument();
    expect(within(result).getAllByText("synthetic-trained model").length).toBe(2);
    expect(within(result).getByText("Conflicting classifications")).toBeInTheDocument();
    expect(within(result).getByText(/trained on SYNTHETIC data/)).toBeInTheDocument();
    expect(within(result).getByText("no probability")).toBeInTheDocument(); // rules carry none
  });

  it("previews a bounded plan, runs, shows progress and an evidence-labelled report", async () => {
    const user = userEvent.setup();
    installFakeBackend({}, { llm: false });
    renderAt(`/research?chain=ethereum&address=${ETH_WALLET}`);
    const start = screen.getByRole("button", { name: "Start investigation" });
    expect(start).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Preview research plan" }));
    const plan = await screen.findByLabelText("Research plan");
    expect(within(plan).getByText(/2 tool calls/)).toBeInTheDocument();
    expect(within(plan).getByText(/AI: not available/)).toBeInTheDocument();
    expect(within(plan).getByText(/cannot choose tools/)).toBeInTheDocument();
    await user.click(start);
    expect(await screen.findByText("collecting evidence", {}, { timeout: 3000 })).toBeInTheDocument();
    const report = await screen.findByLabelText("Research report", {}, { timeout: 4000 });
    expect(within(report).getByText("Observed blockchain data")).toBeInTheDocument();
    expect(within(report).getByText("ML prediction · classification analyst")).toBeInTheDocument();
    expect(within(report).getByText("Hypothesis · behaviour analyst")).toBeInTheDocument();
    expect(within(report).getByText(/No AI model available/)).toBeInTheDocument();
    expect(within(report).getByText(/not facts/)).toBeInTheDocument();
  });

  it("cancels a running investigation", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderAt(`/research?chain=ethereum&address=${ETH_WALLET}`);
    await user.click(screen.getByRole("button", { name: "Preview research plan" }));
    await screen.findByLabelText("Research plan");
    await user.click(screen.getByRole("button", { name: "Start investigation" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("cancelled", {}, { timeout: 3000 })).toBeInTheDocument();
  });

  it("shows the model registry with synthetic warnings and honest training refusal", async () => {
    const user = userEvent.setup();
    installFakeBackend({}, { models: true });
    renderAt("/research");
    await user.click(screen.getByRole("tab", { name: "Models and AI provider" }));
    const table = await screen.findByRole("table", { name: "Model registry" });
    expect(within(table).getByText("experimental synthetic")).toBeInTheDocument();
    expect(within(table).getByText("not real-world performance")).toBeInTheDocument();
    await user.click(within(table).getByRole("button", { name: "Evaluation" }));
    expect(await screen.findByRole("table", { name: "Model evaluation" })).toBeInTheDocument();
    expect(screen.getByText(/metrics are not real-world performance/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Train Ethereum on reviewed labels" }));
    expect(await screen.findByText(/insufficient data: 0 labelled wallet/)).toBeInTheDocument();
    expect(screen.getByText(/No AI provider is configured/)).toBeInTheDocument();
  });

  it("shows an empty registry state", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderAt("/research");
    expect(screen.getByText("Choose a wallet")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Models and AI provider" }));
    expect(await screen.findByText("No model trained")).toBeInTheDocument();
  });
});
