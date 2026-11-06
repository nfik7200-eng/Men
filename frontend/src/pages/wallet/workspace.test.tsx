import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import App from "../../App";
import { ETH_WALLET, installFakeBackend } from "../../test/fakeBackend";

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

const UNSTORED = "0x" + "12".repeat(20);

describe("wallet workspace", () => {
  it("shows an honest first-visit state for an unstored wallet, without errors", async () => {
    installFakeBackend();
    renderAt(`/wallets/ethereum/${UNSTORED}`);
    expect(await screen.findByText("Nothing has been retrieved for this address yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retrieve recent activity" })).toBeInTheDocument();
    expect(screen.queryByText("Request failed")).toBeNull();
    expect(document.title).toBe("0x121212…121212 · Ethereum · BIP");
  });

  it("switches tabs through the URL", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderAt(`/wallets/ethereum/${ETH_WALLET}`);
    const tabs = await screen.findByRole("navigation", { name: "Wallet sections" });
    expect(within(tabs).getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
    await user.click(within(tabs).getByRole("link", { name: "Classification" }));
    expect(screen.getByLabelText("location")).toHaveTextContent(`/wallets/ethereum/${ETH_WALLET}/classification`);
    expect(await screen.findByRole("button", { name: "Classify wallet" })).toBeInTheDocument();
    await user.click(within(tabs).getByRole("link", { name: "Evidence" }));
    expect(screen.getByLabelText("location")).toHaveTextContent(/\/evidence$/);
  });

  it("adds the wallet to a watchlist and opens a case from the Cases tab", async () => {
    const user = userEvent.setup();
    const fake = installFakeBackend();
    fake.watchlists.set(99, { id: 99, name: "Whales", description: null, entries: [] });
    renderAt(`/wallets/ethereum/${ETH_WALLET}/cases`);
    expect(await screen.findByText("Not on any watchlist")).toBeInTheDocument();
    await user.selectOptions(await screen.findByLabelText("Watchlist"), "99");
    await user.click(screen.getByRole("button", { name: "Add to watchlist" }));
    expect(await screen.findByRole("link", { name: "Whales" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Case title"), "Large outflow review");
    await user.click(screen.getByRole("button", { name: "Open case" }));
    expect(await screen.findByRole("link", { name: "Large outflow review" })).toBeInTheDocument();
    expect(fake.cases[0]?.subject_address).toBe(ETH_WALLET);
  });

  it("keeps the old Capital Flows link working", async () => {
    installFakeBackend();
    renderAt(`/flows?chain=ethereum&address=${ETH_WALLET}`);
    expect(await screen.findByRole("heading", { level: 1, name: "Capital Flows" })).toBeInTheDocument();
  });
});
