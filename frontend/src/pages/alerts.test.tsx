import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { makeAlert, makeRule, makeSignal } from "../test/discoveryBackend";
import { renderDiscovery } from "../test/renderDiscovery";

const heading = () => screen.findByRole("heading", { level: 1, name: "Alerts" });

describe("alerts", () => {
  it("shows honest empty states for alerts and rules, and the delivery channels", async () => {
    renderDiscovery("/alerts");
    await heading();
    expect(await screen.findByText("No alerts yet")).toBeInTheDocument();
    expect(await screen.findByText("No alert rules")).toBeInTheDocument();
    expect(await screen.findByText(/Delivered to: in app/)).toBeInTheDocument();
    expect(screen.getByText("not configured: needs an approved mail service")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("lists SYNTHETIC alerts with read state and links each to its signal", async () => {
    renderDiscovery("/alerts", {
      signals: [makeSignal({ id: 4 })],
      alerts: [makeAlert({ id: 1, signal_id: 4, title: "SYNTHETIC unread alert" }), makeAlert({ id: 2, signal_id: 4, title: "SYNTHETIC read alert", read_at: "2026-11-09T14:00:00+00:00" })],
    });
    const table = await screen.findByRole("table", { name: "Alerts" });
    expect(within(table).getByRole("link", { name: "SYNTHETIC unread alert" })).toHaveAttribute("href", "/signals/4");
    expect(within(table).getByText("unread", { selector: ".pill" })).toBeInTheDocument();
    expect(within(table).getByText("read", { selector: ".pill" })).toBeInTheDocument();
    expect(screen.getByText("1 unread of 2")).toBeInTheDocument();
    expect(within(table).getAllByRole("button", { name: /Mark alert \d+ as read/ })).toHaveLength(1);
  });

  it("marks one alert read and then all alerts read", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/alerts", { alerts: [makeAlert({ id: 1 }), makeAlert({ id: 2 }), makeAlert({ id: 3 })] }, "analyst");
    await screen.findByRole("table", { name: "Alerts" });
    await user.click(screen.getByRole("button", { name: "Mark alert 2 as read" }));
    expect(await screen.findByText("Alert marked read.")).toBeInTheDocument();
    expect(backend.posts("/alerts/2/read")).toHaveLength(1);
    await screen.findByText("2 unread of 3");
    await user.click(screen.getByRole("button", { name: "Mark all read" }));
    expect(await screen.findByText("Marked 2 alerts read.")).toBeInTheDocument();
    expect(backend.posts("/alerts/read-all")).toHaveLength(1);
    expect(await screen.findByText("0 unread of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mark all read" })).toBeDisabled();
  });

  it("filters to unread alerts and shows a distinct empty state", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/alerts", { alerts: [makeAlert({ id: 1, read_at: "2026-11-09T14:00:00+00:00" })] });
    await screen.findByRole("table", { name: "Alerts" });
    await user.click(screen.getByRole("checkbox", { name: "Unread only" }));
    expect(await screen.findByText("No unread alerts")).toBeInTheDocument();
    expect(backend.state.calls.some((c) => c.path.startsWith("/alerts?") && c.path.includes("unread=true"))).toBe(true);
  });

  it("shows an error state for the alert list without hiding the rules", async () => {
    renderDiscovery("/alerts", { rules: [makeRule()], failures: { "GET /alerts": { status: 500, code: "internal_error", message: "SYNTHETIC alerts failure" } } });
    expect(await screen.findByText("SYNTHETIC alerts failure")).toBeInTheDocument();
    expect(await screen.findByText("SYNTHETIC high severity")).toBeInTheDocument();
  });

  it("lists rules, disables and enables one", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/alerts", { rules: [makeRule({ id: 1 })] }, "analyst");
    const table = await screen.findByRole("table", { name: "Alert rules" });
    expect(within(table).getByText("enabled")).toBeInTheDocument();
    await user.click(within(table).getByRole("button", { name: "Disable rule SYNTHETIC high severity" }));
    expect(await within(table).findByText("disabled")).toBeInTheDocument();
    expect(backend.state.calls.filter((c) => c.method === "PATCH").map((c) => c.body)).toEqual([{ enabled: false }]);
    await user.click(within(table).getByRole("button", { name: "Enable rule SYNTHETIC high severity" }));
    expect(await within(table).findByText("enabled")).toBeInTheDocument();
  });

  it("creates a rule with the chosen families and chains, and shows a duplicate refusal", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/alerts", { rules: [makeRule({ name: "SYNTHETIC existing" })] }, "analyst");
    const form = await screen.findByRole("form", { name: "New alert rule" });
    await user.click(within(form).getByRole("button", { name: "Create rule" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("Give the rule a name.");
    await user.type(within(form).getByLabelText("Name"), "SYNTHETIC pools");
    await user.selectOptions(within(form).getByLabelText("Minimum severity"), "medium");
    await user.clear(within(form).getByLabelText("Minimum score"));
    await user.type(within(form).getByLabelText("Minimum score"), "55");
    await user.click(await within(form).findByRole("checkbox", { name: "pools" }));
    await user.click(within(form).getByRole("checkbox", { name: "Solana" }));
    await user.click(within(form).getByRole("button", { name: "Create rule" }));
    await screen.findByText("SYNTHETIC pools");
    expect(backend.posts("/alert-rules")[0]?.body).toEqual({ name: "SYNTHETIC pools", min_severity: "medium", min_score: 55, families: ["pools"], chains: ["solana"], enabled: true });
    await user.type(within(form).getByLabelText("Name"), "SYNTHETIC existing");
    await user.click(within(form).getByRole("button", { name: "Create rule" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("already exists");
  });

  it("validates the minimum score before calling the API", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/alerts", {}, "analyst");
    const form = await screen.findByRole("form", { name: "New alert rule" });
    await user.type(within(form).getByLabelText("Name"), "SYNTHETIC bad score");
    await user.clear(within(form).getByLabelText("Minimum score"));
    await user.type(within(form).getByLabelText("Minimum score"), "250");
    await user.click(within(form).getByRole("button", { name: "Create rule" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("from 0 to 100");
    expect(backend.posts("/alert-rules")).toHaveLength(0);
  });

  it("lets a viewer read but not change anything, and says why", async () => {
    const backend = renderDiscovery("/alerts", { alerts: [makeAlert()], rules: [makeRule()] }, "viewer");
    await screen.findByRole("table", { name: "Alerts" });
    expect(screen.getByRole("button", { name: "Mark all read" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Mark alert 1 as read" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Disable rule SYNTHETIC high severity" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Create rule" })).toBeDisabled();
    expect(screen.getAllByText("Needs the analyst role. You are signed in as viewer.").length).toBeGreaterThanOrEqual(3);
    expect(backend.state.calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });

  it("shows the unread count as a navigation badge without changing the link's name", async () => {
    renderDiscovery("/", { alerts: [makeAlert({ id: 1 }), makeAlert({ id: 2 })] });
    const nav = await screen.findByRole("navigation", { name: "Main" });
    const link = within(nav).getByRole("link", { name: "Alerts" });
    await waitFor(() => expect(link).toHaveAccessibleDescription("2 unread alerts"));
    expect(link.parentElement?.querySelector(".nav-badge")).toHaveTextContent("2");
  });

  it("shows no badge when nothing is unread", async () => {
    renderDiscovery("/", { alerts: [makeAlert({ read_at: "2026-11-09T14:00:00+00:00" })] });
    const nav = await screen.findByRole("navigation", { name: "Main" });
    const link = within(nav).getByRole("link", { name: "Alerts" });
    await screen.findByText(/BIP 0\.1\.0/);
    expect(link.parentElement?.querySelector(".nav-badge")).toBeNull();
    expect(link).not.toHaveAttribute("aria-describedby");
  });

  it("shows no badge and no error when the alert count cannot be loaded", async () => {
    renderDiscovery("/", { alerts: [makeAlert()], failures: { "GET /alerts": { status: 500, code: "internal_error", message: "SYNTHETIC" } } });
    const nav = await screen.findByRole("navigation", { name: "Main" });
    await screen.findByText(/BIP 0\.1\.0/);
    expect(nav.querySelector(".nav-badge")).toBeNull();
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
  });
});
