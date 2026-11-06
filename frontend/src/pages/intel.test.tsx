import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import App from "../App";
import { ETH_WALLET, installFakeBackend } from "../test/fakeBackend";
import { layout } from "./Graph";

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );

describe("wallet intelligence", () => {
  it("shows scope-labelled metrics, rules, detectors and verification", async () => {
    installFakeBackend();
    renderAt(`/wallets/ethereum/${ETH_WALLET}/evidence`);
    const intel = await screen.findByRole("region", { name: "Wallet intelligence" });
    expect(within(intel).getByText(/not the wallet's lifetime history/)).toBeInTheDocument();
    expect(within(intel).getByText("transactions observed in retrieved dataset")).toBeInTheDocument();
    expect(await within(intel).findByText("High transaction frequency")).toBeInTheDocument();
    expect(within(intel).getAllByText("not enough data").length).toBeGreaterThan(0);
    expect(await within(intel).findByText("Transaction frequency spike")).toBeInTheDocument();
    expect(within(intel).getByText(/No USD values/)).toBeInTheDocument();
    expect(screen.queryByText(/Demo data/)).toBeNull();
  });

  it("runs analysis and reports a verification check that could not run", async () => {
    const user = userEvent.setup();
    const { fetchMock } = installFakeBackend();
    renderAt(`/wallets/ethereum/${ETH_WALLET}/evidence`);
    await user.click(await screen.findByRole("button", { name: "Run analysis" }));
    expect(await screen.findByText(/Analysis run 1 completed/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText(/No provider on independent infrastructure/)).toBeInTheDocument();
    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST").map(([u]) => String(u));
    expect(posts.some((u) => u.includes("/intelligence/analyze/ethereum/"))).toBe(true);
    expect(posts.some((u) => u.includes("/intelligence/verification/ethereum/0x"))).toBe(true);
  });

  it("labels offline fixture data as demo data", async () => {
    installFakeBackend({ origin: "offline_fixture" });
    renderAt(`/wallets/ethereum/${ETH_WALLET}/evidence`);
    expect(await screen.findByText(/Demo data: offline fixtures/)).toBeInTheDocument();
  });
});

describe("capital flows", () => {
  it("asks for a wallet, validates input, then shows per-asset flows without USD", async () => {
    const user = userEvent.setup();
    const { fetchMock } = installFakeBackend();
    renderAt("/flows");
    expect(screen.getByText("Choose a wallet")).toBeInTheDocument();
    const before = fetchMock.mock.calls.length;
    await user.type(screen.getByLabelText("Address"), "0x12");
    await user.click(screen.getByRole("button", { name: "Show flows" }));
    expect(screen.getByRole("alert")).toHaveTextContent(/40 hexadecimal/);
    expect(fetchMock.mock.calls.length).toBe(before);
    await user.clear(screen.getByLabelText("Address"));
    await user.type(screen.getByLabelText("Address"), ETH_WALLET);
    await user.click(screen.getByRole("button", { name: "Show flows" }));
    expect(await screen.findByText("Per asset: in, out and net")).toBeInTheDocument();
    expect(screen.getAllByText("1500000 raw").length).toBeGreaterThan(0); // unknown decimals stay raw
    expect(screen.getByRole("img", { name: /Daily flows/ })).toBeInTheDocument();
    expect(screen.queryByText(/\$\s?\d/)).toBeNull();
  });
});

describe("relationship graph", () => {
  it("renders nodes and shows edge evidence on click", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderAt(`/graph?chain=ethereum&address=${ETH_WALLET}`);
    expect(await screen.findByRole("img", { name: /3 nodes, 2 edges/ })).toBeInTheDocument();
    expect(screen.getByText(/do not establish common ownership/)).toBeInTheDocument();
    expect(screen.getByText(/1 nodes at depth 1 not expanded/)).toBeInTheDocument();
    const details = screen.getByRole("complementary", { name: "Selection details" });
    expect(within(details).getByText("Center wallet")).toBeInTheDocument();
    await user.click(within(details).getByRole("button", { name: /native transfer/ }));
    expect(within(details).getByText("Evidence")).toBeInTheDocument();
    expect(within(details).getByText(/does not show common ownership/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Zoom in" }));
  });

  it("lays out deterministically with the center at the origin", () => {
    const nodes = [
      { id: "c", kind: "address" as const, depth: 0, degree: 2, center: true },
      { id: "b", kind: "token" as const, depth: 1, degree: 1 },
      { id: "a", kind: "address" as const, depth: 1, degree: 1 },
    ];
    const p = layout(nodes);
    expect(p.get("c")).toEqual({ x: 0, y: 0 });
    expect(layout([...nodes].reverse())).toEqual(p);
  });
});

describe("opportunities", () => {
  it("shows transparent leads with uncertainty and no advice", async () => {
    const user = userEvent.setup();
    installFakeBackend({ leads: true });
    renderAt("/opportunities");
    expect(await screen.findByText("unverified research lead")).toBeInTheDocument();
    expect(screen.getByText(/Not investment advice; no buy or sell/)).toBeInTheDocument();
    expect(screen.getByText(/6 distinct participants/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Evaluate Ethereum" }));
    expect(await screen.findByText(/insufficient data/)).toBeInTheDocument();
  });
});

describe("overview", () => {
  it("shows verification counts from the backend", async () => {
    installFakeBackend();
    renderAt("/");
    expect(await screen.findByText("Corroborated transactions")).toBeInTheDocument();
    expect(screen.getByText("of 2 stored")).toBeInTheDocument();
  });
});
