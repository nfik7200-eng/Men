import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import App from "../App";
import { installFakeBackend } from "../test/fakeBackend";

const renderStrategies = () =>
  render(
    <MemoryRouter initialEntries={["/strategies"]}>
      <App />
    </MemoryRouter>,
  );

async function run() {
  const user = userEvent.setup();
  installFakeBackend();
  renderStrategies();
  await user.click(await screen.findByRole("button", { name: "Run Strategy Analysis" }));
  await screen.findByText("Mock strategy classification fixture");
}

describe("Strategies", () => {
  it("labels every panel a hypothesis and shows its limitations", async () => {
    await run();
    expect(screen.getAllByText("hypothesis")).toHaveLength(4);
    expect(screen.getByText(/no win rate, profit or trade timing is measured/)).toBeInTheDocument();
  });

  it("never recommends copying a wallet", async () => {
    await run();
    expect(screen.queryByText(/recommendation/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/moderate copy/i)).not.toBeInTheDocument();
    expect(screen.getAllByText(/research lead/).length).toBeGreaterThan(0);
  });

  it("shows unchecked facts as not checked instead of inventing them", async () => {
    await run();
    expect(screen.getByText("GitHub link: Not checked")).toBeInTheDocument();
    expect(screen.getByText("Upgrade authority: Not checked")).toBeInTheDocument();
    expect(screen.queryByText("Immutable")).not.toBeInTheDocument();
    expect(screen.queryByText("Upgrade Authority Held")).not.toBeInTheDocument();
    const creations = screen.getByText("Contract Creations").closest("section") ?? document.body;
    expect(within(creations as HTMLElement).getByText(/earliest inbound native transfer/)).toBeInTheDocument();
  });
});
