import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import App from "../App";
import { installFakeBackend } from "../test/fakeBackend";

const renderSources = () =>
  render(
    <MemoryRouter initialEntries={["/sources"]}>
      <App />
    </MemoryRouter>,
  );

describe("Data sources", () => {
  it("shows each integration, marks paid plans and tests a connection", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderSources();
    expect(await screen.findByText("CoinGecko API")).toBeInTheDocument();
    expect(screen.getByText("Needs a paid plan")).toBeInTheDocument();
    expect(screen.getByText("keyless")).toBeInTheDocument();
    expect(screen.getByText("needs a key")).toBeInTheDocument();
    const [first] = screen.getAllByRole("button", { name: "Test" });
    if (!first) throw new Error("no Test button");
    await user.click(first);
    expect(await screen.findByText("Connected (120 ms).")).toBeInTheDocument();
  });

  it("refreshes prices and credits the provider", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderSources();
    expect(await screen.findByText("No prices stored yet.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Refresh prices" }));
    expect(await screen.findByText("3123.45 USD")).toBeInTheDocument();
    expect(screen.getByText(/Price data by CoinGecko/)).toBeInTheDocument();
    expect(screen.getByText(/not independently verified by BIP/)).toBeInTheDocument();
  });

  it("explains a missing key instead of failing silently", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderSources();
    await user.type(await screen.findByLabelText("Ethereum address"), "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48");
    await user.click(screen.getByRole("button", { name: "Contract info" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("needs a key");
  });
});
