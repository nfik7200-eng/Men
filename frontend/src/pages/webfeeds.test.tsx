import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import App from "../App";
import { installFakeBackend } from "../test/fakeBackend";

describe("Web feeds", () => {
  it("lists followed feeds, fetches on request and shows items as observations", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    render(
      <MemoryRouter initialEntries={["/web"]}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByText("never")).toBeInTheDocument();
    expect(screen.getByText("No items stored yet.")).toBeInTheDocument();
    expect(screen.getByText(/what the item says is not verified/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Fetch now" }));
    expect(await screen.findByRole("status")).toHaveTextContent("1 new item");
    expect(await screen.findByRole("link", { name: "Upgrade scheduled" })).toHaveAttribute("href", "https://blog.example.org/a1");
  });
});
