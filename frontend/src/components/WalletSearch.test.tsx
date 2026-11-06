import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WalletSearch } from "./WalletSearch";

function Target() {
  const { chain, address } = useParams();
  return <p>opened {chain} {address}</p>;
}

const renderSearch = () =>
  render(
    <MemoryRouter initialEntries={["/"]}>
      <WalletSearch />
      <Routes>
        <Route path="/" element={<p>home</p>} />
        <Route path="/wallets/:chain/:address" element={<Target />} />
      </Routes>
    </MemoryRouter>,
  );

afterEach(() => localStorage.clear());

describe("WalletSearch", () => {
  it("opens the wallet workspace for a valid address without calling the backend", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const user = userEvent.setup();
    renderSearch();
    await user.type(screen.getByLabelText("Search wallet address"), "0xAA00000000000000000000000000000000000001{Enter}");
    expect(screen.getByText("opened ethereum 0xaa00000000000000000000000000000000000001")).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled(); // search never triggers retrieval
  });

  it("explains invalid input and transaction hashes inline", async () => {
    const user = userEvent.setup();
    renderSearch();
    const input = screen.getByLabelText("Search wallet address");
    await user.type(input, "nonsense{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent(/Ethereum/);
    expect(input).toHaveAttribute("aria-invalid", "true");
    await user.clear(input);
    await user.type(input, `0x${"ab".repeat(32)}{Enter}`);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "This looks like a transaction, not a wallet. Transaction lookup is not available yet.",
    );
    expect(screen.getByText("home")).toBeInTheDocument();
  });

  it("focuses with the / key", async () => {
    const user = userEvent.setup();
    renderSearch();
    await user.keyboard("/");
    expect(screen.getByLabelText("Search wallet address")).toHaveFocus();
  });
});
