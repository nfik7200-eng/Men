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

describe("Case workbench", () => {
  it("shows an honest empty state and validates the create form", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderAt("/cases");
    expect(await screen.findByText("No cases yet")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Create case" }));
    expect(screen.getByText("Give the case a title.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Title"), "Token X");
    await user.click(screen.getByRole("button", { name: "Create case" }));
    expect(screen.getByText("Choose a case type.")).toBeInTheDocument();
  });

  it("creates a case, lists it, and opens its detail", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderAt("/cases");
    await user.type(await screen.findByLabelText("Title"), "Token X");
    const form = screen.getByRole("form", { name: "New case" });
    await user.selectOptions(within(form).getByLabelText("Case type"), "rugpull");
    await user.click(within(form).getByRole("button", { name: "Create case" }));
    const link = await screen.findByRole("link", { name: /Token X/ });
    await user.click(link);
    expect(await screen.findByText("No evidence recorded")).toBeInTheDocument();
    expect(screen.getByText("No hypotheses")).toBeInTheDocument();
    expect(screen.getByText("No leads")).toBeInTheDocument();
  });

  it("requires provenance for evidence, labels its kind, and refuses unsupported 'supported'", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderAt("/cases");
    await user.type(await screen.findByLabelText("Title"), "Case A");
    const form = screen.getByRole("form", { name: "New case" });
    await user.selectOptions(within(form).getByLabelText("Case type"), "rugpull");
    await user.click(within(form).getByRole("button", { name: "Create case" }));
    await user.click(await screen.findByRole("link", { name: /Case A/ }));
    await screen.findByText("No evidence recorded");

    await user.click(screen.getByRole("button", { name: "Add evidence" }));
    expect(screen.getByText(/evidence must carry provenance/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Subject (address)"), ETH_WALLET);
    await user.type(screen.getByLabelText("Description"), "Liquidity removed");
    await user.type(screen.getByLabelText("Source reference"), "tx:0xabc");
    await user.selectOptions(screen.getByLabelText("Evidence kind"), "statistical_signal");
    await user.click(screen.getByRole("button", { name: "Add evidence" }));
    expect(await screen.findByText(/Liquidity removed/)).toBeInTheDocument();
    expect(screen.getAllByText("Statistical signal").length).toBeGreaterThan(0);

    await user.type(screen.getByLabelText("Hypothesis"), "Deployer drained the pool");
    await user.click(screen.getByRole("button", { name: "Add hypothesis" }));
    expect(await screen.findByText("no evidence cited")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText(/Status of hypothesis/), "supported");
    expect(await screen.findByText("a supported hypothesis must cite evidence ids")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Lead"), "Trace funder");
    await user.click(screen.getByRole("button", { name: "Add lead" }));
    expect(await screen.findByText("Trace funder")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText(/Status of lead/), "investigating");
    expect(await screen.findByDisplayValue("investigating")).toBeInTheDocument();
  });

  it("runs the automated scan and reports coverage gaps instead of zeros", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderAt("/cases");
    const form = await screen.findByRole("form", { name: "Automated scan" });
    await user.click(within(form).getByRole("button", { name: "Run automated scan" }));
    expect(screen.getByText("Choose a case type.")).toBeInTheDocument();
    await user.selectOptions(within(form).getByLabelText("Case type"), "rugpull");
    await user.click(within(form).getByRole("button", { name: "Run automated scan" }));
    expect(screen.getByText("Enter an address.")).toBeInTheDocument();
    await user.type(within(form).getByLabelText("Target"), ETH_WALLET);
    await user.click(within(form).getByRole("button", { name: "Run automated scan" }));
    const result = await screen.findByLabelText("Scan result");
    expect(within(result).getByText(/No transactions or transfers are stored/)).toBeInTheDocument();
    expect(within(result).getByText("Liquidity removal transaction")).toBeInTheDocument();
    expect(within(result).getByText(/unsupported suggestions/)).toBeInTheDocument();
  });
});
