import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import App from "../App";
import { installFakeBackend } from "../test/fakeBackend";

const A = "0x" + "aa".repeat(20);
const B = "0x" + "bb".repeat(20);

function seeded() {
  const fake = installFakeBackend();
  fake.watchlists.set(1, {
    id: 1, name: "Whales", description: null,
    entries: [
      { id: 10, chain: "ethereum", address: A, note: "treasury?", added_at: "2026-11-06T00:00:00Z" },
      { id: 11, chain: "ethereum", address: B, note: null, added_at: "2026-11-06T00:00:00Z" },
    ],
  });
  return fake;
}

describe("watchlists", () => {
  it("filters entries by address or note", async () => {
    const user = userEvent.setup();
    seeded();
    render(<MemoryRouter initialEntries={["/watchlists"]}><App /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: /Whales/ }));
    await user.type(await screen.findByLabelText("Filter entries"), "treasury");
    expect(screen.getByText("1 of 2 shown")).toBeInTheDocument();
    expect(screen.queryByText(B)).toBeNull();
  });

  it("analyses every entry and reports each outcome without inventing results", async () => {
    const user = userEvent.setup();
    seeded();
    render(<MemoryRouter initialEntries={["/watchlists"]}><App /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: /Whales/ }));
    await user.click(await screen.findByRole("button", { name: "Analyse watchlist" }));
    const result = await screen.findByRole("status", { name: "Watchlist analysis" });
    expect(result).toHaveTextContent("2 wallets analysed: 1 succeeded, 1 insufficient data");
    expect(within(result).getAllByRole("listitem")).toHaveLength(2);
  });
});
