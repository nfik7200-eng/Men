import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SYNTHETIC_ADDR, SYNTHETIC_ADDR_2, makeLabel } from "../test/discoveryBackend";
import { renderDiscovery } from "../test/renderDiscovery";

const heading = () => screen.findByRole("heading", { level: 1, name: "Attribution" });

describe("attribution", () => {
  it("explains what labels are and shows honest empty states", async () => {
    renderDiscovery("/attribution");
    await heading();
    expect(await screen.findByText("No proposals waiting for review")).toBeInTheDocument();
    expect(screen.getByText("No approved labels")).toBeInTheDocument();
    expect(screen.getAllByText(/organisation or protocol/).length).toBeGreaterThan(0);
    expect(screen.getByText(/never to a person/)).toBeInTheDocument();
    expect(screen.getByText(/at least one evidence item added by a person/)).toBeInTheDocument();
    expect(screen.getByText(/stays a proposal/)).toBeInTheDocument();
    expect(screen.getByText(/never people/)).toBeInTheDocument(); // the backend scope string
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("lists the review queue and approved labels with proposer, source and reviewer", async () => {
    renderDiscovery("/attribution", {
      labels: [
        makeLabel({ id: 1, name: "SYNTHETIC queued", proposed_by: "analyst-a" }),
        makeLabel({ id: 2, name: "SYNTHETIC AI queued", source: "ai", proposed_by: "ai:local-model", ref: SYNTHETIC_ADDR_2 }),
        makeLabel({ id: 3, name: "SYNTHETIC approved", status: "approved", reviewed_by: "reviewer-b", review_note: "SYNTHETIC note long enough", reviewed_at: "2026-11-09T10:00:00+00:00" }),
      ],
    });
    const queue = await screen.findByRole("table", { name: "Proposed labels" });
    expect(within(queue).getByText("SYNTHETIC queued")).toBeInTheDocument();
    expect(within(queue).getByText("AI proposal")).toBeInTheDocument();
    expect(within(queue).getByText("ai:local-model")).toBeInTheDocument();
    expect(screen.getByText("2 waiting")).toBeInTheDocument();
    const approved = await screen.findByRole("table", { name: "Approved labels" });
    expect(within(approved).getByText("SYNTHETIC approved")).toBeInTheDocument();
    expect(within(approved).getByText("reviewer-b")).toBeInTheDocument();
    expect(within(approved).getByText("SYNTHETIC note long enough")).toBeInTheDocument();
  });

  it("renders evidence urls as safe links only for http(s) and everything else as text", async () => {
    renderDiscovery("/attribution", {
      labels: [
        makeLabel({
          id: 1,
          evidence: [
            { kind: "url", ref: "https://example.org/ok", added_by: "analyst-a" },
            { kind: "url", ref: "javascript:alert(1)", added_by: "analyst-a" },
            { kind: "note", ref: "<img src=x onerror=alert(1)>", description: "<b>bold</b>", added_by: "analyst-a" },
          ],
        }),
      ],
    });
    const queue = await screen.findByRole("table", { name: "Proposed labels" });
    const evidence = within(queue).getByRole("list", { name: "Evidence" });
    const ok = within(evidence).getByRole("link", { name: "https://example.org/ok" });
    expect(ok).toHaveAttribute("href", "https://example.org/ok");
    expect(ok).toHaveAttribute("rel", "noopener noreferrer");
    expect(ok).toHaveAttribute("target", "_blank");
    expect(within(evidence).getAllByRole("link")).toHaveLength(1);
    expect(within(evidence).getByText("javascript:alert(1)")).toBeInTheDocument();
    expect(within(evidence).getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(within(evidence).getByText("<b>bold</b>")).toBeInTheDocument();
    expect(evidence.querySelector("img, b")).toBeNull();
  });

  it("validates the review: note length, and approval needs person-added evidence", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery(
      "/attribution",
      { labels: [makeLabel({ id: 1, name: "SYNTHETIC AI queued", source: "ai", proposed_by: "ai:local-model", evidence: [{ kind: "signal", ref: "1", added_by: "ai:local-model" }] })] },
      "analyst",
    );
    await user.click(await screen.findByRole("button", { name: "Review SYNTHETIC AI queued" }));
    const dialog = await screen.findByRole("dialog", { name: "Review label: SYNTHETIC AI queued" });
    await user.type(within(dialog).getByLabelText(/Review note/), "too short");
    await user.click(within(dialog).getByRole("button", { name: "Approve label" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("at least 10 characters");
    await user.type(within(dialog).getByLabelText(/Review note/), " but now long enough");
    await user.click(within(dialog).getByRole("button", { name: "Approve label" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("added by a person");
    expect(backend.posts("/entities/labels/1/review")).toHaveLength(0);
  });

  it("approves with extra person-added evidence and moves the label to the approved list", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery(
      "/attribution",
      { labels: [makeLabel({ id: 1, name: "SYNTHETIC AI queued", source: "ai", proposed_by: "ai:local-model", evidence: [{ kind: "signal", ref: "1", added_by: "ai:local-model" }] })] },
      "analyst",
    );
    await user.click(await screen.findByRole("button", { name: "Review SYNTHETIC AI queued" }));
    const dialog = await screen.findByRole("dialog", { name: "Review label: SYNTHETIC AI queued" });
    await user.type(within(dialog).getByLabelText(/Review note/), "Checked the public page myself");
    await user.click(within(dialog).getByRole("button", { name: "Add evidence" }));
    await user.type(within(dialog).getByLabelText("Reference 1"), "https://example.org/independent");
    await user.click(within(dialog).getByRole("button", { name: "Approve label" }));
    expect(await screen.findByText(/Reviewed "SYNTHETIC AI queued"/)).toBeInTheDocument();
    expect(backend.posts("/entities/labels/1/review")[0]?.body).toEqual({
      decision: "approve", note: "Checked the public page myself", evidence: [{ kind: "url", ref: "https://example.org/independent" }],
    });
    expect(await screen.findByRole("table", { name: "Approved labels" })).toBeInTheDocument();
    expect(screen.getByText("No proposals waiting for review")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("rejects a proposal without needing evidence", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/attribution", { labels: [makeLabel({ id: 1, evidence: [] })] }, "analyst");
    await user.click(await screen.findByRole("button", { name: "Review SYNTHETIC Exchange" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("radio", { name: "Reject" }));
    await user.type(within(dialog).getByLabelText(/Review note/), "Not supported by any evidence");
    await user.click(within(dialog).getByRole("button", { name: "Reject label" }));
    await screen.findByText(/Reviewed "SYNTHETIC Exchange"/);
    expect(backend.posts("/entities/labels/1/review")[0]?.body).toMatchObject({ decision: "reject" });
    expect(backend.state.labels[0]?.status).toBe("rejected");
  });

  it("shows the server's refusal inside the dialog and keeps it open", async () => {
    const user = userEvent.setup();
    renderDiscovery(
      "/attribution",
      { labels: [makeLabel({ id: 1 })], failures: { "POST /entities/labels/1/review": { status: 422, code: "invalid_input", message: "the proposer cannot approve their own label" } } },
      "analyst",
    );
    await user.click(await screen.findByRole("button", { name: "Review SYNTHETIC Exchange" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Review note/), "Looks right to me, checked it");
    await user.click(within(dialog).getByRole("button", { name: "Approve label" }));
    expect(await within(dialog).findByText("the proposer cannot approve their own label")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("proposes a label with evidence and validates the form first", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/attribution", {}, "analyst");
    const form = await screen.findByRole("form", { name: "Propose label" });
    await user.click(within(form).getByRole("button", { name: "Propose label" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(/Enter an address/);
    await user.type(within(form).getByLabelText("Address or program ID"), SYNTHETIC_ADDR);
    await user.click(within(form).getByRole("button", { name: "Propose label" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("Name the organisation or protocol.");
    await user.type(within(form).getByLabelText("Organisation or protocol name"), "SYNTHETIC Bridge");
    await user.selectOptions(within(form).getByLabelText("Category"), "bridge");
    await user.type(within(form).getByLabelText("Reference 1"), "javascript:alert(1)");
    await user.click(within(form).getByRole("button", { name: "Propose label" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("http or https");
    await user.clear(within(form).getByLabelText("Reference 1"));
    await user.type(within(form).getByLabelText("Reference 1"), "https://example.org/bridge");
    await user.click(within(form).getByRole("button", { name: "Propose label" }));
    expect(await screen.findByText(/Proposed "SYNTHETIC Bridge"/)).toBeInTheDocument();
    expect(backend.posts("/entities/labels")[0]?.body).toEqual({
      chain: "ethereum", ref: SYNTHETIC_ADDR, entity_kind: "contract", category: "bridge", name: "SYNTHETIC Bridge",
      evidence: [{ kind: "url", ref: "https://example.org/bridge" }], note: null,
    });
    expect(await screen.findByRole("table", { name: "Proposed labels" })).toBeInTheDocument();
  });

  it("lets a viewer read but not propose or review, and says why", async () => {
    const backend = renderDiscovery("/attribution", { labels: [makeLabel({ id: 1 })] }, "viewer");
    await screen.findByRole("table", { name: "Proposed labels" });
    expect(screen.getByRole("button", { name: "Review SYNTHETIC Exchange" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Propose label" })).toBeDisabled();
    expect(screen.getAllByText("Needs the analyst role. You are signed in as viewer.").length).toBeGreaterThanOrEqual(2);
    expect(backend.state.calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("shows an error state for the queue with a retry", async () => {
    renderDiscovery("/attribution", { failures: { "GET /entities/labels": { status: 500, code: "internal_error", message: "SYNTHETIC labels failure" } } });
    expect((await screen.findAllByText("SYNTHETIC labels failure")).length).toBe(2);
    expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(2);
  });
});
