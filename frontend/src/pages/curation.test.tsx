import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import App from "../App";
import { installFakeBackend } from "../test/fakeBackend";

const renderCuration = () =>
  render(
    <MemoryRouter initialEntries={["/curation"]}>
      <App />
    </MemoryRouter>,
  );

describe("Model curation", () => {
  it("labels predictions as hypotheses and requires a reviewer name", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderCuration();
    expect(await screen.findByText("hypothesis")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(screen.getByText("Enter your reviewer name before recording a verdict.")).toBeInTheDocument();
  });

  it("records a verdict, removes the item from the queue and updates statistics", async () => {
    const user = userEvent.setup();
    installFakeBackend();
    renderCuration();
    await user.type(await screen.findByLabelText("Reviewer"), "ana");
    await user.click(await screen.findByRole("button", { name: "Reject" }));
    expect(await screen.findByText("Nothing to review")).toBeInTheDocument();
    expect(await screen.findByRole("status")).toHaveTextContent("analyst label");
    expect(await screen.findByText(/by ana/)).toBeInTheDocument();
  });
});
