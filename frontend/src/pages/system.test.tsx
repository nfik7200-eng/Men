import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { SystemHealth } from "../api/types";
import { defaultSystem } from "../test/discoveryBackend";
import { renderDiscovery } from "../test/renderDiscovery";

const heading = () => screen.findByRole("heading", { level: 1, name: "System" });
const system = (patch: (s: SystemHealth) => SystemHealth) => patch(defaultSystem());

describe("system", () => {
  it("labels the page as demo data when the backend says its records are fixtures", async () => {
    renderDiscovery("/system", { origin: "offline_fixture" });
    await heading();
    expect(await screen.findByText(/Demo data: offline fixtures/)).toBeInTheDocument();
  });

  it("shows no demo banner for stored observations", async () => {
    renderDiscovery("/system");
    await heading();
    await screen.findByText("No warnings.");
    expect(screen.queryByText(/Demo data: offline fixtures/)).toBeNull();
  });

  it("shows an ok server, with no warnings, and states that no backup exists", async () => {
    renderDiscovery("/system");
    await heading();
    expect(await screen.findByText("No warnings.")).toBeInTheDocument();
    expect(screen.getByText("ok", { selector: ".pill" })).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument(); // schema version
    expect(screen.getByText("5.0 MiB")).toBeInTheDocument();
    expect(screen.getByText(/There is no backup/)).toBeInTheDocument();
    expect(screen.getByText("none yet")).toBeInTheDocument();
    expect(screen.getByText(/The scheduler is not enabled on this server/)).toBeInTheDocument();
    expect(screen.getByText("No ingestion run has been recorded.")).toBeInTheDocument();
  });

  it("shows warnings with their codes, jobs with last results, and a degraded status", async () => {
    renderDiscovery("/system", {
      system: system((s) => ({
        ...s,
        status: "degraded",
        warnings: [{ code: "job_failing", message: "Job chain_scan has failed 2 time(s)." }, { code: "backup_stale", message: "The newest backup is missing or older than 7 days." }],
        workers: {
          enabled: true, running: true,
          jobs: [
            { job: "chain_scan", description: "Scan finalized blocks", interval_seconds: 3600, contacts_providers: true, running: false, consecutive_failures: 2, next_due: "2026-11-10T09:00:00+00:00", last_run: { status: "failed", started_at: "2026-11-10T08:00:00+00:00", finished_at: "2026-11-10T08:00:09+00:00", detail: "SYNTHETIC job detail" } },
            { job: "backup", description: "Back up", interval_seconds: 86400, contacts_providers: false, running: false, consecutive_failures: 0, next_due: "2026-11-11T00:00:00+00:00", last_run: null },
          ],
        },
        ingestion: { recent_runs: [{ id: 3, chain: "ethereum", status: "failed", started_at: "2026-11-10T07:00:00+00:00", rpc_calls: 9, error_count: 2 }], failed_runs_24h: 1, errors_24h: [{ code: "upstream_failed", count: 2 }] },
        backups: { enabled: true, count: 2, latest: { name: "b", size_bytes: 2_097_152, created_at: "2026-11-01T00:00:00+00:00" } },
      })),
    });
    const warnings = await screen.findByRole("list", { name: "Warnings" });
    expect(within(warnings).getByText("job_failing")).toBeInTheDocument();
    expect(within(warnings).getByText("backup_stale")).toBeInTheDocument();
    expect(screen.getByText("degraded", { selector: ".pill" })).toBeInTheDocument();
    const jobs = screen.getByRole("table", { name: "Scheduled jobs" });
    expect(within(jobs).getByText("SYNTHETIC job detail")).toBeInTheDocument();
    expect(within(jobs).getByText("never run")).toBeInTheDocument();
    expect(within(jobs).getByText("every 1 hour")).toBeInTheDocument();
    expect(screen.getByText(/1 failed run in the last 24 hours/)).toBeInTheDocument();
    expect(screen.getByText(/upstream_failed \(2\)/)).toBeInTheDocument();
    expect(screen.getByText(/2\.0 MiB/)).toBeInTheDocument();
  });

  it("shows a critical status and an unmeasurable disk as unknown, not as fine", async () => {
    renderDiscovery("/system", {
      system: system((s) => ({ ...s, status: "critical", warnings: [{ code: "storage_unmeasurable", message: "SYNTHETIC cannot measure" }], disk: { writes: "unknown", error: "SYNTHETIC cannot measure" } })),
    });
    expect(await screen.findByText("critical", { selector: ".pill" })).toBeInTheDocument();
    expect(screen.getByText("unknown", { selector: ".pill" })).toBeInTheDocument();
    expect(screen.getByText(/Disk space could not be measured, so it is not reported as fine/)).toBeInTheDocument();
  });

  it("shows an error state with a retry", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/system", { failures: { "GET /system/health": { status: 500, code: "internal_error", message: "SYNTHETIC health failure" } } });
    expect(await screen.findByText("SYNTHETIC health failure")).toBeInTheDocument();
    delete backend.state.failures["GET /system/health"];
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("No warnings.")).toBeInTheDocument();
  });

  it("lets a viewer read but not create a backup, and says why", async () => {
    const backend = renderDiscovery("/system", {}, "viewer");
    await screen.findByText("No warnings.");
    const button = screen.getByRole("button", { name: "Create backup" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "Needs the admin role. You are signed in as viewer.");
    expect(screen.getByText("Needs the admin role. You are signed in as viewer.")).toBeInTheDocument();
    expect(backend.state.calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("asks before creating a backup, creates exactly one on confirm and shows it", async () => {
    const user = userEvent.setup();
    const backend = renderDiscovery("/system", {}, "admin");
    await user.click(await screen.findByRole("button", { name: "Create backup" }));
    const dialog = await screen.findByRole("dialog", { name: "Create a backup now?" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(backend.posts("/system/backups")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Create backup" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Create backup" }));
    expect(await screen.findByText(/Backup created \(2\.0 MiB/)).toBeInTheDocument();
    expect(backend.posts("/system/backups")).toHaveLength(1);
    expect(await screen.findByText("1")).toBeInTheDocument();
  });
});
