import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import App, { NAV } from "./App";
import { SOL_WALLET, installFakeBackend } from "./test/fakeBackend";

const renderAt = (path = "/") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );

describe("BIP dashboard", () => {
  it("loads the overview with backend values and honest empty states", async () => {
    installFakeBackend();
    renderAt("/");
    expect(screen.getByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(await screen.findByText(/No blockchain data has been ingested/)).toBeInTheDocument();
    expect(screen.getByText("No findings recorded")).toBeInTheDocument();
    expect(screen.getByText("No activity yet")).toBeInTheDocument();
    expect(screen.getAllByText("Adapter ready, not connected")).toHaveLength(2);
    expect(screen.getByText("0 / 10")).toBeInTheDocument();
    expect(screen.getAllByText("Not retrieved")).toHaveLength(2);
  });

  it("shows a loading state before data arrives", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(() => undefined));
    renderAt("/chains");
    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
  });

  it("shows a connection error with a working retry", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    renderAt("/settings");
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Backend unreachable")).toBeInTheDocument();
    const calls = fetchMock.mock.calls.length;
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(calls));
  });

  it("navigates between all sections", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderAt("/");
    const nav = screen.getByRole("navigation", { name: "Main" });
    for (const item of NAV) {
      await user.click(within(nav).getByRole("link", { name: item.label }));
      expect(screen.getByRole("heading", { level: 1, name: item.label })).toBeInTheDocument();
    }
  });

  it("marks unbuilt sections as placeholders without invented data", async () => {
    installFakeBackend();
    renderAt("/opportunities");
    expect(screen.getByText("Not built yet")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText(/\b\d+(\.\d+)?\s*(%|\/\s*100)/)).toBeNull();
  });

  it("validates wallet search input before calling the API", async () => {
    const user = userEvent.setup();
    const { fetchMock } = installFakeBackend();
    renderAt("/wallets");
    await screen.findByText("No wallet data stored yet");
    const before = fetchMock.mock.calls.length;
    await user.type(screen.getByLabelText("Address"), "0x1234");
    await user.click(screen.getByRole("button", { name: "Look up" }));
    expect(screen.getByRole("alert")).toHaveTextContent(/40 hexadecimal characters/);
    expect(fetchMock.mock.calls.length).toBe(before);

    await user.selectOptions(screen.getByLabelText("Chain"), "solana");
    await user.clear(screen.getByLabelText("Address"));
    await user.type(screen.getByLabelText("Address"), "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
    await user.click(screen.getByRole("button", { name: "Look up" }));
    expect(await screen.findByText("No data stored for this address yet")).toBeInTheDocument();
  });

  it("creates a watchlist, adds and removes entries, rejects duplicates, deletes the list", async () => {
    const user = userEvent.setup();
    const { watchlists } = installFakeBackend();
    renderAt("/watchlists");
    await screen.findByText("No watchlists yet");

    await user.type(screen.getByLabelText("Name"), "Whales");
    await user.click(screen.getByRole("button", { name: "Create watchlist" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Whales" })).toBeInTheDocument();
    expect(watchlists.size).toBe(1);

    const address = "0x" + "ab".repeat(20);
    await user.type(screen.getByLabelText("Address"), "0x123");
    await user.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByRole("alert")).toHaveTextContent(/40 hexadecimal/);

    await user.clear(screen.getByLabelText("Address"));
    await user.type(screen.getByLabelText("Address"), address);
    await user.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText(address)).toBeInTheDocument();

    await user.type(screen.getByLabelText("Address"), address);
    await user.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/already on this watchlist/);

    await user.click(screen.getByRole("button", { name: `Remove ${address}` }));
    expect(await screen.findByText("No addresses on this list")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Delete list" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("No watchlists yet")).toBeInTheDocument();
    expect(watchlists.size).toBe(0);
  });

  it("runs a provider health check and shows the real result", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderAt("/providers");
    expect(await screen.findByText("Untested")).toBeInTheDocument();
    expect(screen.getByText("Adapter, not configured")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Health check" })).toHaveLength(1); // unconfigured: no button
    await user.click(screen.getByRole("button", { name: "Health check" }));
    expect(await screen.findByText("Check passed")).toBeInTheDocument();
    expect(await screen.findByText("Healthy")).toBeInTheDocument();
  });

  it("fetches the latest block on demand", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderAt("/chains");
    expect(await screen.findAllByText("Not retrieved yet")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Fetch latest block" }));
    expect(await screen.findByText("20000000")).toBeInTheDocument();
    expect(screen.getByText("Connected", { selector: ".pill" })).toBeInTheDocument();
  });

  it("retrieves wallet activity and shows partial results honestly", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderAt(`/wallets/solana/${SOL_WALLET}`);
    expect(await screen.findByText("No retrieval has been run for this address yet.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retrieve recent activity" }));
    expect(await screen.findByText("Run 1: partial")).toBeInTheDocument();
    expect(screen.getByText(/Not covered by this run/)).toBeInTheDocument();
    expect(screen.getByText("1 recorded issue(s)")).toBeInTheDocument();
    expect(await screen.findByText("Transactions (1)")).toBeInTheDocument();
    expect(screen.getByText("partial")).toBeInTheDocument();
    expect(screen.getByText("1 unknown instr.")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /5555/ });
    expect(link).toHaveAttribute("href", `https://explorer.solana.com/tx/${"5".repeat(64)}`);
    expect(screen.getByText("unknown")).toBeInTheDocument(); // null block time is not fabricated
  });
});
