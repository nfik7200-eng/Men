import { fireEvent, render, screen, within } from "@testing-library/react";
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
const URL = `/explorer?chain=ethereum&address=${ETH_WALLET}`;

describe("temporal explorer", () => {
  it("asks for a wallet first", () => {
    installFakeBackend();
    renderAt("/explorer");
    expect(screen.getByRole("heading", { level: 1, name: "Temporal Explorer" })).toBeInTheDocument();
    expect(screen.getByText("Choose a wallet")).toBeInTheDocument();
  });

  it("renders the filtered graph, legend and the node evidence trail", async () => {
    installFakeBackend();
    renderAt(URL);
    expect(await screen.findByRole("img", { name: /3 nodes, 2 edges/ })).toBeInTheDocument();
    expect(screen.getByText(/cross-verified by independent providers/)).toBeInTheDocument();
    const trail = await screen.findByRole("region", { name: "Evidence trail" });
    expect(await within(trail).findByText(/19999995 to 19999995/)).toBeInTheDocument();
    expect(within(trail).getByText(/req req-42/)).toBeInTheDocument();
    expect(within(trail).getByText(/raw bronze\/eth\/0001.parquet#7/)).toBeInTheDocument();
    expect(within(trail).getByText(/do not establish ownership/)).toBeInTheDocument();
  });

  it("sends the evidence-status filter and explains an empty result", async () => {
    const user = userEvent.setup();
    const { fetchMock } = installFakeBackend();
    renderAt(URL);
    await screen.findByRole("img", { name: /3 nodes, 2 edges/ });
    await user.click(screen.getByLabelText("Single-source"));
    await user.click(screen.getByLabelText("Hypothesis"));
    expect(await screen.findByText("No observations match these filters")).toBeInTheDocument();
    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls.some((u) => u.includes("graph-explorer") && u.includes("status=verified") && !u.includes("status=single_source"))).toBe(true);
  });

  it("applies a time window from the slider", async () => {
    const { fetchMock } = installFakeBackend();
    renderAt(URL);
    await screen.findByRole("img", { name: /3 nodes, 2 edges/ });
    const start = await screen.findByLabelText("Window start");
    fireEvent.change(start, { target: { value: "1" } });
    expect(await screen.findByText("No observations match these filters")).toBeInTheDocument();
    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls.some((u) => u.includes("graph-explorer") && u.includes("since=2026-11-02"))).toBe(true);
  });
});
