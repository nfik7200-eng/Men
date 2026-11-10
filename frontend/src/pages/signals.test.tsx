import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SYNTHETIC_ADDR, SYNTHETIC_ADDR_2, SYNTHETIC_TX, makeLabel, makeSignal } from "../test/discoveryBackend";
import { renderDiscovery } from "../test/renderDiscovery";

const fresh = { last_run_at: "2026-11-10T07:00:00+00:00", last_run_status: "succeeded", age_seconds: 3600, stale: false };
const heading = (name: string) => screen.findByRole("heading", { level: 1, name });

describe("signals feed", () => {
  it("shows an honest empty state, with a freshness warning when discovery never ran", async () => {
    renderDiscovery("/signals");
    expect(await screen.findByText("No open signals")).toBeInTheDocument();
    expect(screen.getByText(/None has run yet, so nothing has been examined/)).toBeInTheDocument();
    expect(screen.getByText(/Discovery has not run on this database/)).toBeInTheDocument();
    expect(screen.getByText("No pass has run yet")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("lets an analyst run a discovery pass, says it reads the database only, and refreshes the list", async () => {
    const backend = renderDiscovery("/signals", {}, "analyst");
    await heading("Signals");
    const button = await screen.findByRole("button", { name: "Run discovery now" });
    expect(screen.getByText(/reads the database only: no provider is contacted/)).toBeInTheDocument();
    await userEvent.click(button);
    expect(await screen.findByText(/Pass succeeded: 3 new, 1 merged, 0 expired, 1 alert\(s\)\./)).toBeInTheDocument();
    expect(backend.fetchMock.mock.calls.some(([u, i]) => String(u).endsWith("/discovery/run") && (i as RequestInit | undefined)?.method === "POST")).toBe(true);
  });

  it("disables the discovery button for a viewer and says why", async () => {
    renderDiscovery("/signals", {}, "viewer");
    await heading("Signals");
    await waitFor(() => expect(screen.getByRole("button", { name: "Run discovery now" })).toBeDisabled()); // the session resolves after first paint
    expect(screen.getAllByText(/analyst role/i).length).toBeGreaterThan(0);
  });

  it("says the data is demo data when the backend reports offline fixtures", async () => {
    renderDiscovery("/signals", { origin: "offline_fixture", signals: [makeSignal()], freshness: fresh });
    expect(await screen.findByText(/Demo data: offline fixtures/)).toBeInTheDocument();
  });

  it("lists SYNTHETIC signals ranked by score with severity, score and a link to the detail", async () => {
    renderDiscovery("/signals", {
      freshness: fresh,
      signals: [
        makeSignal({ id: 1, title: "SYNTHETIC low one", score: 20, severity: "low" }),
        makeSignal({ id: 2, title: "SYNTHETIC high one", score: 80, severity: "high" }),
      ],
    });
    const table = await screen.findByRole("table", { name: "Signals" });
    const rows = within(table).getAllByRole("row").slice(1);
    const [top, second] = rows;
    if (!top || !second) throw new Error("expected two rows");
    expect(within(top).getByRole("link", { name: "SYNTHETIC high one" })).toHaveAttribute("href", "/signals/2");
    expect(top).toHaveTextContent("80.0 / 100");
    expect(top).toHaveTextContent("high severity");
    expect(second).toHaveTextContent("SYNTHETIC low one");
    expect(screen.getByText(/The last discovery pass left no open candidate|1 to 2 of 2/)).toBeInTheDocument();
    expect(screen.getByText("Last discovery pass")).toBeInTheDocument();
    expect(screen.queryByText(/more than 24 hours old/)).toBeNull();
  });

  it("warns when the last pass is stale", async () => {
    renderDiscovery("/signals", { signals: [makeSignal()], freshness: { ...fresh, age_seconds: 200000, stale: true } });
    expect(await screen.findByText(/more than 24 hours old/)).toBeInTheDocument();
  });

  it("sends filters to /signals and shows a no-match state that says how to recover", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/signals", { freshness: fresh, signals: [makeSignal({ severity: "high" })] });
    await screen.findByRole("table", { name: "Signals" });
    await user.selectOptions(screen.getByLabelText("Severity"), "low");
    expect(await screen.findByText("No signal matches these filters")).toBeInTheDocument();
    const call = backend.state.calls.filter((c) => c.path.startsWith("/signals?")).at(-1);
    expect(call?.path).toContain("severity=low");
    expect(call?.path).toContain("status=open");
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByRole("table", { name: "Signals" })).toBeInTheDocument();
  });

  it("offers family and detector filters from the detector catalogue", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/signals", { freshness: fresh, signals: [makeSignal()] });
    await screen.findByRole("table", { name: "Signals" });
    await user.selectOptions(await screen.findByRole("option", { name: "Large transfer" }).then(() => screen.getByLabelText("Detector")), "large_transfer");
    await screen.findByRole("table", { name: "Signals" });
    expect(backend.state.calls.some((c) => c.path.includes("detector=large_transfer"))).toBe(true);
    await user.selectOptions(screen.getByLabelText("Family"), "pools");
    expect(await screen.findByText("No signal matches these filters")).toBeInTheDocument();
  });

  it("shows an error state with a retry when the feed fails", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/signals", { failures: { "GET /discovery/feed": { status: 500, code: "internal_error", message: "SYNTHETIC failure" } } });
    expect(await screen.findByText("SYNTHETIC failure")).toBeInTheDocument();
    expect(screen.queryByText("No open signals")).toBeNull();
    delete backend.state.failures["GET /discovery/feed"];
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("No open signals")).toBeInTheDocument();
  });
});

describe("signal detail", () => {
  const detail = () =>
    makeSignal({
      attribution: {
        labels: {
          [SYNTHETIC_ADDR]: [
            {
              name: "SYNTHETIC Exchange", category: "exchange", label_id: 7, reviewed_by: "reviewer-b", reviewed_at: "2026-11-09T10:00:00+00:00",
              evidence: [
                { kind: "url", ref: "https://example.org/proof", added_by: "analyst-a" },
                { kind: "url", ref: "javascript:alert(1)", added_by: "analyst-a" },
              ],
            },
          ],
        },
        note: "Only labels a person approved with evidence are shown.",
      },
    });

  it("explains the signal: statement, observed, method, caveat, score components, coverage and provenance", async () => {
    renderDiscovery("/signals/1", { signals: [makeSignal()] });
    expect(await heading("SYNTHETIC unusually large transfer")).toBeInTheDocument();
    expect(screen.getByText(/SYNTHETIC statement/)).toBeInTheDocument();
    expect(screen.getByText(/SYNTHETIC method/)).toBeInTheDocument();
    expect(screen.getByText(/SYNTHETIC caveat/)).toBeInTheDocument();
    expect(screen.getByText("Statistical signal")).toBeInTheDocument();
    expect(screen.getByText("72.5 / 100")).toBeInTheDocument();
    expect(screen.getByText(/0\.62 \(0 to 1/)).toBeInTheDocument();
    const comps = screen.getByRole("table", { name: "Score components" });
    expect(within(comps).getByText("baseline reliability")).toBeInTheDocument();
    expect(within(comps).getByText("40 baseline transfers (50 = full)")).toBeInTheDocument();
    expect(screen.getByText(/3 of 7 supporting transactions finalized/, { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByText(/not yet finalized and could still be reorganised/)).toBeInTheDocument();
    expect(screen.getByText(/SYNTHETIC limitations/)).toBeInTheDocument();
    expect(screen.getByText("large_transfer:ethereum:synthetic:1")).toBeInTheDocument();
    // nested observed values are text, not markup
    expect(screen.getByText('{"a":1}')).toBeInTheDocument();
  });

  it("shows a list of records as readable rows with address links, not as raw JSON", async () => {
    const base = makeSignal();
    renderDiscovery("/signals/1", {
      signals: [
        makeSignal({
          explanation: {
            ...base.explanation,
            observed: { top_addresses: [{ address: SYNTHETIC_ADDR, net_inflow_raw: "900" }, { address: SYNTHETIC_ADDR_2, net_inflow_raw: "120" }] },
          },
        }),
      ],
    });
    await heading("SYNTHETIC unusually large transfer");
    expect(screen.queryByText(/\[\{"address"/)).toBeNull();
    const rows = screen.getAllByRole("listitem").filter((li) => /net inflow raw/i.test(li.textContent ?? ""));
    expect(rows).toHaveLength(2);
    const first = rows[0] as HTMLElement;
    expect(first).toHaveTextContent(/net inflow raw 900/i);
    expect(within(first).getByRole("link")).toHaveAttribute("href", expect.stringContaining(SYNTHETIC_ADDR));
  });

  it("links evidence and addresses to the explorer for well formed identifiers only", async () => {
    renderDiscovery("/signals/1", {
      signals: [makeSignal({ evidence: [{ kind: "transaction", chain: "ethereum", ref: SYNTHETIC_TX }, { kind: "transaction", chain: "ethereum", ref: "not-a-hash" }] })],
    });
    await heading("SYNTHETIC unusually large transfer");
    const list = screen.getByRole("list", { name: "Supporting records" });
    const link = within(list).getByRole("link", { name: SYNTHETIC_TX });
    expect(link).toHaveAttribute("href", `https://etherscan.io/tx/${SYNTHETIC_TX}`);
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
    expect(within(list).queryByRole("link", { name: "not-a-hash" })).toBeNull();
    expect(within(list).getByText("not-a-hash")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: SYNTHETIC_ADDR_2 })[0]).toHaveAttribute("href", `https://etherscan.io/address/${SYNTHETIC_ADDR_2}`);
  });

  it("says Unattributed, never a negative, when no reviewed label exists", async () => {
    renderDiscovery("/signals/1", { signals: [makeSignal()] });
    await heading("SYNTHETIC unusually large transfer");
    expect(screen.getByText(/Unattributed: no reviewed label/)).toBeInTheDocument();
    expect(screen.getByText(/does not mean the address is not an exchange/)).toBeInTheDocument();
  });

  it("shows approved labels with reviewer and evidence; only http(s) evidence becomes a link", async () => {
    renderDiscovery("/signals/1", { signals: [detail()], labels: [makeLabel({ status: "approved" })] });
    await heading("SYNTHETIC unusually large transfer");
    const panel = screen.getByRole("heading", { level: 2, name: "Attribution" }).closest("section");
    if (!panel) throw new Error("no attribution panel");
    expect(within(panel).getByText("SYNTHETIC Exchange")).toBeInTheDocument();
    expect(within(panel).getByText(/Approved by reviewer-b/)).toBeInTheDocument();
    expect(within(panel).queryByText(/Unattributed/)).toBeNull();
    const ok = within(panel).getByRole("link", { name: "https://example.org/proof" });
    expect(ok).toHaveAttribute("rel", "noopener noreferrer");
    expect(ok).toHaveAttribute("target", "_blank");
    expect(within(panel).queryByRole("link", { name: /javascript/ })).toBeNull();
    expect(within(panel).getByText("javascript:alert(1)")).toBeInTheDocument();
  });

  it("shows a missing signal as an error, not an empty page", async () => {
    renderDiscovery("/signals/99", { signals: [] });
    expect(await screen.findByText(/signal 99 not found/)).toBeInTheDocument();
  });

  it("dismisses an open signal with a note (analyst)", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/signals/1", { signals: [makeSignal()] }, "analyst");
    await heading("SYNTHETIC unusually large transfer");
    const form = await screen.findByRole("form", { name: "Dismiss signal" });
    await user.type(within(form).getByLabelText("Note (optional)"), "SYNTHETIC reason: routine treasury move");
    await user.click(within(form).getByRole("button", { name: "Dismiss signal" }));
    expect(await screen.findByText("Signal dismissed.")).toBeInTheDocument();
    const post = backend.posts("/signals/1/dismiss");
    expect(post).toHaveLength(1);
    expect(post[0]?.body).toEqual({ note: "SYNTHETIC reason: routine treasury move" });
    expect(await screen.findByText("dismissed", { selector: ".pill" })).toBeInTheDocument();
    expect(within(screen.getByRole("form", { name: "Dismiss signal" })).getByRole("button", { name: "Dismiss signal" })).toBeDisabled();
  });

  it("promotes to a watchlist, marking the signal promoted", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/signals/1", { signals: [makeSignal()] }, "analyst");
    backend.watchlists.set(3, { id: 3, name: "SYNTHETIC list", description: null, entries: [] });
    await heading("SYNTHETIC unusually large transfer");
    const form = await screen.findByRole("form", { name: "Promote signal" });
    await user.click(within(form).getByRole("button", { name: "Promote signal" }));
    expect(await screen.findByText(/Subject added to the watchlist/)).toBeInTheDocument();
    expect(backend.posts("/signals/1/promote")[0]?.body).toEqual({ target: "watchlist", watchlist_id: 3, note: null });
    expect(await screen.findByText("promoted", { selector: ".pill" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Watchlist 3" })).toBeInTheDocument();
  });

  it("explains an empty promote target instead of offering an empty list", async () => {
    const user = userEvent.setup();
    renderDiscovery("/signals/1", { signals: [makeSignal()] }, "analyst");
    await heading("SYNTHETIC unusually large transfer");
    const form = await screen.findByRole("form", { name: "Promote signal" });
    expect(await within(form).findByText(/There is no watchlist to promote into yet/)).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Promote signal" })).toBeDisabled();
    await user.selectOptions(within(form).getByLabelText("Promote to"), "investigation");
    expect(await within(form).findByText(/There is no investigation to promote into yet/)).toBeInTheDocument();
  });

  it("promotes into an investigation without a watchlist note", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/signals/1", { signals: [makeSignal()] }, "analyst");
    backend.cases.push({ id: 5, title: "SYNTHETIC case", description: null, status: "open", subject_chain: null, subject_address: null, created_at: "2026-11-01T00:00:00Z", updated_at: "2026-11-01T00:00:00Z", findings: [] });
    await heading("SYNTHETIC unusually large transfer");
    const form = await screen.findByRole("form", { name: "Promote signal" });
    await user.selectOptions(within(form).getByLabelText("Promote to"), "investigation");
    await user.click(await within(form).findByRole("button", { name: "Promote signal" }));
    expect(await screen.findByText(/Added to the investigation as an unverified finding/)).toBeInTheDocument();
    expect(backend.posts("/signals/1/promote")[0]?.body).toEqual({ target: "investigation", investigation_id: 5 });
  });

  it("cannot promote a native-asset signal to a watchlist", async () => {
    renderDiscovery("/signals/1", { signals: [makeSignal({ subject: { kind: "asset", ref: "ETH" } })] }, "analyst");
    await heading("SYNTHETIC unusually large transfer");
    const form = await screen.findByRole("form", { name: "Promote signal" });
    expect(within(form).getByText(/no address to watch/)).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Promote signal" })).toBeDisabled();
  });

  it("disables analyst actions for a viewer and says why", async () => {
    const backend = renderDiscovery("/signals/1", { signals: [makeSignal()] }, "viewer");
    await heading("SYNTHETIC unusually large transfer");
    const dismiss = await screen.findByRole("button", { name: "Dismiss signal" });
    expect(dismiss).toBeDisabled();
    expect(dismiss).toHaveAttribute("title", "Needs the analyst role. You are signed in as viewer.");
    expect(screen.getByRole("button", { name: "Promote signal" })).toBeDisabled();
    expect(screen.getAllByText("Needs the analyst role. You are signed in as viewer.").length).toBeGreaterThanOrEqual(2);
    expect(backend.state.calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("shows a server refusal of an action", async () => {
    const user = userEvent.setup();
    renderDiscovery("/signals/1", { signals: [makeSignal()], failures: { "POST /signals/1/dismiss": { status: 422, code: "invalid_input", message: "SYNTHETIC: refused" } } }, "analyst");
    await heading("SYNTHETIC unusually large transfer");
    await user.click(await screen.findByRole("button", { name: "Dismiss signal" }));
    expect(await screen.findByText("SYNTHETIC: refused")).toBeInTheDocument();
  });

  it("does not offer actions on a dismissed signal", async () => {
    renderDiscovery("/signals/1", { signals: [makeSignal({ status: "dismissed", status_note: "SYNTHETIC note" })] }, "analyst");
    await heading("SYNTHETIC unusually large transfer");
    expect(await screen.findByText(/can no longer be dismissed or promoted/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dismiss signal" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Promote signal" })).toBeDisabled();
  });
});
