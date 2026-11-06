import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import App, { NAV, NAV_GROUPS } from "./App";
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
      expect(screen.getByRole("heading", { level: 1, name: item.heading })).toBeInTheDocument();
      expect(document.title).toBe(`${item.heading} · BIP`);
    }
  });

  it("shows no research lead when the backend has none (nothing invented)", async () => {
    installFakeBackend();
    renderAt("/opportunities");
    expect(await screen.findByText("No research leads")).toBeInTheDocument();
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
    expect(await screen.findByText("Nothing has been retrieved for this address yet")).toBeInTheDocument();
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
    // stored observations now live in the workspace's Activity tab
    const sections = screen.getByRole("navigation", { name: "Wallet sections" });
    await user.click(within(sections).getByRole("link", { name: "Activity" }));
    expect(screen.getByText(/Not covered by this run/)).toBeInTheDocument();
    expect(screen.getByText("1 recorded issue(s)")).toBeInTheDocument();
    expect(await screen.findByText("Transactions (1)")).toBeInTheDocument();
    expect(screen.getByText("partial")).toBeInTheDocument();
    expect(screen.getByText("1 unknown instr.")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /5555/ });
    expect(link).toHaveAttribute("href", `https://explorer.solana.com/tx/${"5".repeat(64)}`);
    expect(screen.getByText("unknown")).toBeInTheDocument(); // null block time is not fabricated
  });

  it("groups the navigation into Monitor, Explore, Intelligence and Cases", () => {
    installFakeBackend();
    renderAt("/");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(NAV_GROUPS.map((g) => g.label)).toEqual(["Monitor", "Explore", "Intelligence", "Cases", ""]);
    for (const name of ["Monitor", "Explore", "Intelligence", "Cases"]) {
      expect(within(nav).getByText(name)).toBeInTheDocument();
    }
    const monitor = within(nav).getByRole("list", { name: "Monitor" });
    expect(within(monitor).getAllByRole("link").map((a) => a.textContent)).toEqual(["Overview", "Chains", "Providers"]);
    expect(within(nav).getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
  });

  it("opens the mobile menu, closes it with Escape and returns focus to the Menu button", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderAt("/");
    const menu = screen.getByRole("button", { name: "Menu" });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    await user.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{Escape}");
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(menu).toHaveFocus();
  });

  it("offers a skip link and shows the backend version from /health", async () => {
    installFakeBackend();
    renderAt("/");
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#content");
    expect(await screen.findByText(/BIP 0\.1\.0/)).toBeInTheDocument();
    expect(screen.queryByText(/· 0\.5/)).toBeNull();
  });

  it("shows one backend-offline banner instead of an error in every panel", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 502 }));
    renderAt("/");
    expect(await screen.findByRole("status", { name: "Backend status" })).toHaveTextContent(/Backend unreachable/);
  });

  it("treats a missing endpoint (route_not_found) as an error, not as 'nothing stored'", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      const body = url.endsWith("/health")
        ? { status: "ok", version: "0.1.0", environment: "test", database: "ok", storage_writes: "allowed", timestamp: "2026-11-06T00:00:00Z" }
        : { error: { code: "route_not_found", message: "Not Found" } };
      return Promise.resolve(new Response(JSON.stringify(body), { status: url.endsWith("/health") ? 200 : 404, headers: { "Content-Type": "application/json" } }));
    });
    renderAt(`/wallets/ethereum/0x${"aa".repeat(20)}`);
    expect((await screen.findAllByText("Request failed")).length).toBeGreaterThan(0);
    expect(screen.queryByText(/No data stored for this address yet|Nothing has been retrieved/)).toBeNull();
  });
});
