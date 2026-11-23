import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import App from "../App";
import { installFakeBackend } from "../test/fakeBackend";

const renderSetup = () =>
  render(
    <MemoryRouter initialEntries={["/setup"]}>
      <App />
    </MemoryRouter>,
  );

const row = async (testId: string) => within(await screen.findByTestId(testId));

describe("Setup", () => {
  it("saves a provider key without ever showing it, and offers a connection check", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderSetup();
    const alchemy = await row("secret-BIP_PROVIDER_ALCHEMY_ETH_URL");
    expect(alchemy.getByText("Not set")).toBeInTheDocument();
    const input = alchemy.getByLabelText("Alchemy (Ethereum)");
    expect(input).toHaveAttribute("type", "password");
    await user.type(input, "https://eth-mainnet.g.alchemy.com/v2/secretkey123");
    await user.click(alchemy.getByRole("button", { name: "Save" }));
    expect(await alchemy.findByText("Saved. In use now.")).toBeInTheDocument();
    expect(input).toHaveValue("");
    expect(screen.queryByDisplayValue(/secretkey123/)).not.toBeInTheDocument();
    expect(await alchemy.findByText(/Set \(saved here\)/)).toBeInTheDocument();
    expect(alchemy.getByRole("button", { name: "Check connection" })).toBeInTheDocument();
  });

  it("shows the server's reason when a value is refused", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderSetup();
    const alchemy = await row("secret-BIP_PROVIDER_ALCHEMY_ETH_URL");
    await user.type(alchemy.getByLabelText("Alchemy (Ethereum)"), "https://evil.example/v2/k");
    await user.click(alchemy.getByRole("button", { name: "Save" }));
    expect(await alchemy.findByText(/host evil.example is not allowed/)).toBeInTheDocument();
  });

  it("marks quota-spending jobs and asks for a restart after a settings change", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderSetup();
    const collector = await row("setting-workers.collector_enabled");
    expect(collector.getByText("Uses provider quota")).toBeInTheDocument();
    const scheduler = await row("setting-workers.enabled");
    await user.click(scheduler.getByRole("checkbox"));
    await user.click(scheduler.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Restart needed")).toBeInTheDocument();
    expect(screen.getByText("workers.enabled")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restart the API now" })).toBeInTheDocument();
  });

  it("is listed in the navigation", async () => {
    installFakeBackend();
    renderSetup();
    expect(await screen.findByRole("link", { name: "Setup" })).toBeInTheDocument();
  });
});
