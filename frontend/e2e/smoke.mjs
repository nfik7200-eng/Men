// End-to-end smoke test: real backend + built frontend + Chromium. No mocks.
// Usage (from frontend/): npm run build && npm run e2e
// Requires: uv, a Playwright-compatible Chromium (require("playwright") must resolve).
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const repoRoot = resolve(import.meta.dirname, "..", "..");
const dataRoot = mkdtempSync(join(tmpdir(), "bip-e2e-"));
const API_PORT = 8765;
const WEB = "http://127.0.0.1:4173";
const children = [];
const results = [];

function start(cmd, args, opts) {
  const child = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"], ...opts });
  let log = "";
  child.stdout.on("data", (d) => (log += d));
  child.stderr.on("data", (d) => (log += d));
  child.getLog = () => log;
  children.push(child);
  return child;
}

async function waitFor(url, label, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(url);
      if (r.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`${label} did not start at ${url}`);
}

function startBackend() {
  return start("uv", ["run", "python", "-m", "bip.api"], {
    cwd: repoRoot,
    env: {
      ...process.env,
      BIP_ENV: "test",
      BIP_DATA_ROOT: dataRoot,
      BIP_MIN_FREE_BYTES: "0",
      BIP_MIN_FREE_RATIO: "0",
      BIP_API__PORT: String(API_PORT),
      BIP_API__CORS_ORIGINS: WEB,
      BIP_LOG_FORMAT: "json",
    },
  });
}

async function stop(child) {
  if (child.exitCode !== null) return;
  child.kill("SIGTERM");
  await new Promise((r) => child.once("exit", r));
}

function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

let backend;
try {
  backend = startBackend();
  await waitFor(`http://127.0.0.1:${API_PORT}/api/v1/health`, "backend");
  const preview = start("npx", ["vite", "preview"], {
    cwd: resolve(repoRoot, "frontend"),
    env: { ...process.env, BIP_API_URL: `http://127.0.0.1:${API_PORT}` },
  });
  await waitFor(WEB, "frontend preview");
  void preview;

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
  const consoleErrors = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));

  // 1. Overview shows real backend state.
  await page.goto(WEB);
  await page.getByText("API ok").first().waitFor();
  check("frontend reaches backend health through the proxy", true);
  await page.getByText(/No blockchain data has been ingested/).waitFor();
  const txTile = await page.locator(".stat", { hasText: "Transactions stored" }).locator(".stat-value").textContent();
  check("empty database shows zero transactions", txTile?.trim() === "0", `tile=${txTile}`);
  await page.screenshot({ path: join(dataRoot, "overview.png") });

  // 2. Chains are configured but not connected.
  await page.getByRole("link", { name: "Chains" }).click();
  await page.getByRole("heading", { level: 2, name: "Ethereum" }).waitFor();
  const notConnected = await page.getByText("Configured, no adapter").count();
  check("chains page reports both chains without adapters", notConnected === 2, `count=${notConnected}`);

  // 3. Watchlist created and populated through the UI.
  await page.getByRole("link", { name: "Watchlists" }).click();
  await page.getByLabel("Name", { exact: true }).fill("E2E whales");
  await page.getByRole("button", { name: "Create watchlist" }).click();
  await page.getByRole("heading", { level: 2, name: "E2E whales" }).waitFor();
  const address = "0x" + "c0ffee".repeat(6) + "abcd";
  await page.getByLabel("Address", { exact: true }).fill(address.toUpperCase().replace("0X", "0x"));
  await page.getByRole("button", { name: "Add" }).click();
  await page.getByText(address).waitFor();
  check("watchlist entry added via UI (address normalized)", true);
  await page.getByLabel("Address", { exact: true }).fill(address);
  await page.getByRole("button", { name: "Add" }).click();
  await page.getByRole("alert").filter({ hasText: "already on this watchlist" }).waitFor();
  check("duplicate entry rejected by backend and shown", true);

  // 4. Investigation created through the UI.
  await page.getByRole("link", { name: "Investigations" }).click();
  await page.getByLabel("Title", { exact: true }).fill("E2E funding trace");
  await page.getByRole("button", { name: "Create investigation" }).click();
  await page.getByText("E2E funding trace").waitFor();
  await page.getByText("E2E funding trace").click();
  await page.getByRole("button", { name: "in progress" }).click();
  await page.locator(".pill", { hasText: "in progress" }).first().waitFor();
  check("investigation created and status updated via UI", true);

  // 5. Restart the backend: data must persist in SQLite.
  await stop(backend);
  const dbFile = join(dataRoot, "operational", "bip.sqlite3");
  check("SQLite file created under the data root", existsSync(dbFile));
  backend = startBackend();
  await waitFor(`http://127.0.0.1:${API_PORT}/api/v1/health`, "backend (restart)");
  await page.getByRole("link", { name: "Watchlists" }).click();
  await page.getByRole("button", { name: /E2E whales/ }).click();
  await page.getByText(address).waitFor();
  check("watchlist and entry persist across a backend restart", true);
  await page.getByRole("link", { name: "Overview" }).click();
  await page.getByText(/Opened investigation 'E2E funding trace'/).waitFor();
  const open = await page.locator(".stat", { hasText: "Open investigations" }).locator(".stat-value").textContent();
  check("dashboard counts reflect persisted records", open?.trim() === "1", `open=${open}`);

  // 6. Direct API checks against the running server.
  const api = (p, init) => fetch(`http://127.0.0.1:${API_PORT}/api/v1${p}`, init);
  const bad = await api("/wallets/ethereum/0x123");
  check("invalid address returns 422", bad.status === 422);
  const missing = await api(`/wallets/ethereum/${address}`);
  check("unknown wallet returns honest 404", missing.status === 404);
  const settings = await (await api("/settings")).text();
  check("settings expose no filesystem paths", !settings.includes(dataRoot));
  const cors = await api("/health", { headers: { Origin: "https://evil.example.net" } });
  check("CORS does not allow foreign origins", !cors.headers.get("access-control-allow-origin"));

  // 7. Mobile layout renders without horizontal overflow.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${WEB}/providers`);
  await page.getByRole("heading", { level: 1, name: "Providers" }).waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check("no horizontal page overflow at 390px", !overflow);
  await page.screenshot({ path: join(dataRoot, "mobile-providers.png") });

  // The deliberate duplicate submission above produces exactly one expected 409 resource error.
  const unexpected = consoleErrors.filter((m) => !m.includes("status of 409"));
  check("no unexpected browser console errors", unexpected.length === 0, unexpected.join(" | "));
  await browser.close();
} catch (err) {
  check("e2e run completed", false, String(err));
  for (const c of children) console.error(c.getLog().slice(-2000));
} finally {
  for (const c of children) await stop(c);
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed. Artifacts: ${dataRoot}`);
  if (failed === 0 && !process.env.BIP_E2E_KEEP) rmSync(dataRoot, { recursive: true, force: true });
  process.exit(failed === 0 ? 0 : 1);
}
