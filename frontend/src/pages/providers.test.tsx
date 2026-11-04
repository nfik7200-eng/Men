import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import App from "../App";
import { installFakeBackend } from "../test/fakeBackend";

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );

const rowOf = (table: HTMLElement, id: string): HTMLElement => {
  const row = within(table).getAllByText(id).map((el) => el.closest("tr")).find((tr) => tr !== null);
  if (!row) throw new Error(`no row for ${id}`);
  return row;
};

const posts = (fetchMock: ReturnType<typeof installFakeBackend>["fetchMock"]) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === "POST").map(([u]) => String(u));

describe("provider intelligence console", () => {
  it("shows capability profiles with text labels and never probes on page load", async () => {
    const { fetchMock } = installFakeBackend();
    renderAt("/providers");
    const table = await screen.findByRole("table", { name: "Provider capability profiles" });
    const infura = rowOf(table, "infura-eth");
    expect(within(infura).getByText("Needs configuration")).toBeInTheDocument();
    expect(within(infura).getByText("Needs credentials")).toBeInTheDocument();
    const eth = rowOf(table, "publicnode-eth");
    expect(within(eth).getByText("Not tested")).toBeInTheDocument();
    expect(within(eth).getByText("No observations")).toBeInTheDocument();
    expect(within(eth).getByText(/allnodes/)).toBeInTheDocument();
    expect(screen.getByText("No probe runs yet")).toBeInTheDocument();
    expect(posts(fetchMock)).toEqual([]); // opening the page sends no probe
  });

  it("requires a budget preview before running and then shows results", async () => {
    const user = userEvent.setup();
    const { fetchMock } = installFakeBackend();
    renderAt("/providers");
    const run = await screen.findByRole("button", { name: "Run bounded provider check" });
    expect(run).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Preview budget" }));
    const preview = await screen.findByLabelText("Probe preview");
    expect(within(preview).getByText(/At most 16 requests/)).toBeInTheDocument();
    expect(within(preview).getByText(/0 retries/)).toBeInTheDocument();
    expect(within(preview).getByText(/Over the per-provider cap/)).toBeInTheDocument();
    expect(within(preview).getByText(/cannot be probed/)).toBeInTheDocument();
    expect(posts(fetchMock)).toEqual([]);
    await user.click(screen.getByRole("button", { name: "Run bounded provider check" }));
    const result = await screen.findByLabelText("Probe run result");
    expect(within(result).getByText("Run completed")).toBeInTheDocument();
    expect(within(result).getByText(/2 requests sent/)).toBeInTheDocument();
    expect(within(result).getByText("Returned chain id 1 (Ethereum mainnet).")).toBeInTheDocument();
    expect(within(result).getAllByText("Inconclusive").length).toBeGreaterThan(0);
    expect(posts(fetchMock).filter((u) => u.endsWith("/providers/probe-runs"))).toHaveLength(1);
  });

  it("separates an environment network restriction from provider availability", async () => {
    const user = userEvent.setup();
    installFakeBackend({ probe: "blocked" });
    renderAt("/providers");
    await user.click(await screen.findByRole("button", { name: "Preview budget" }));
    await screen.findByLabelText("Probe preview");
    await user.click(screen.getByRole("button", { name: "Run bounded provider check" }));
    const result = await screen.findByLabelText("Probe run result");
    expect(within(result).getByText("Run partial")).toBeInTheDocument();
    expect(within(result).getAllByText("Blocked by local environment").length).toBeGreaterThan(0);
    expect(within(result).getByText(/Provider availability remains unknown/)).toBeInTheDocument();
    expect(screen.queryByText(/provider is down|offline provider/i)).toBeNull();
    expect(await screen.findByText(/local network restriction/)).toBeInTheDocument();
  });

  it("opens a capability matrix with reliability and insufficient-data states", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderAt("/providers");
    const table = await screen.findByRole("table", { name: "Provider capability profiles" });
    const eth = rowOf(table, "publicnode-eth");
    await user.click(within(eth).getByRole("button", { name: "Capabilities" }));
    const matrix = await screen.findByRole("table", { name: /Capability matrix/ });
    expect(within(matrix).getByText("Chain identification")).toBeInTheDocument();
    expect(within(matrix).getAllByText("Never probed.").length).toBe(3);
    expect(screen.getAllByText(/insufficient data/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Archive capability unverified/i)).toBeInTheDocument();
    expect(screen.getByText(/Documented facts are not verified operational capabilities/)).toBeInTheDocument();
  });

  it("labels offline fixture data", async () => {
    installFakeBackend({ origin: "offline_fixture" });
    renderAt("/providers");
    expect((await screen.findAllByText(/Demo data: offline fixtures/)).length).toBeGreaterThan(0);
  });

  it("shows loading then a retryable error", async () => {
    const user = userEvent.setup();
    const { fetchMock } = installFakeBackend();
    const real = fetchMock.getMockImplementation();
    if (!real) throw new Error("fake backend not installed");
    fetchMock.mockImplementation((input, init) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.includes("/providers/profiles"))
        return Promise.resolve(new Response(JSON.stringify({ error: { code: "x", message: "profiles failed" } }), { status: 500 }));
      return real(input, init);
    });
    renderAt("/providers");
    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
    const alert = await screen.findByText("profiles failed");
    const box = alert.closest("[role=alert]") as HTMLElement;
    fetchMock.mockImplementation(real);
    await user.click(within(box).getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("table", { name: "Provider capability profiles" })).toBeInTheDocument();
    vi.restoreAllMocks();
  });
});
