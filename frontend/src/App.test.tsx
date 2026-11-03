import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import App, { NAV } from "./App";
import { installFakeBackend } from "./test/fakeBackend";

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
    expect(screen.getAllByText("Configured, no adapter")).toHaveLength(2);
    expect(screen.getByText("0 / 14")).toBeInTheDocument();
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
    expect(await screen.findByText("No data stored for this address")).toBeInTheDocument();
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
});
