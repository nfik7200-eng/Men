// End-to-end smoke test: real backend + built frontend + Chromium. No mocks.
// Usage (from frontend/): npm run build && npm run e2e
// Requires: uv, a Playwright-compatible Chromium (require("playwright") must resolve).
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { checkLayout } from "./layout.mjs";
import { join, resolve } from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const repoRoot = resolve(import.meta.dirname, "..", "..");
const dataRoot = mkdtempSync(join(tmpdir(), "bip-e2e-"));
const API_PORT = 8765;
const FIXTURE_PORT = 8766;
const WEB = "http://127.0.0.1:4173";
const children = [];
const results = [];
const realNetwork = { healthCheck: "not run" };

function start(cmd, args, opts) {
  // detached: each child leads its own process group, so stop() can kill npx/uv AND their children.
  const child = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"], detached: true, ...opts });
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
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise((r) => child.once("exit", r));
  try {
    process.kill(-child.pid, "SIGTERM"); // whole process group
  } catch {
    child.kill("SIGTERM");
  }
  await exited;
}

async function waitForPortFree(url, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      await fetch(url);
    } catch {
      return;
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`${url} is still serving after stop`);
}

function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

let backend;
let preview;
try {
  backend = startBackend();
  await waitFor(`http://127.0.0.1:${API_PORT}/api/v1/health`, "backend");
  preview = start("npx", ["vite", "preview"], {
    cwd: resolve(repoRoot, "frontend"),
    env: { ...process.env, BIP_API_URL: `http://127.0.0.1:${API_PORT}` },
  });
  await waitFor(WEB, "frontend preview");

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
  await page.getByRole("heading", { level: 1, name: "Chains" }).waitFor();
  await page.getByText("Adapter ready, not connected").first().waitFor();
  const notConnected = await page.getByText("Adapter ready, not connected").count();
  check("chains page: adapters present, not connected before any request", notConnected === 2, `count=${notConnected}`);

  // 3. Watchlist created and populated through the UI.
  await page.getByRole("link", { name: "Watchlists" }).click();
  await page.getByLabel("Name", { exact: true }).fill("E2E whales");
  await page.getByRole("button", { name: "Create watchlist" }).click();
  await page.getByRole("heading", { level: 2, name: "E2E whales" }).waitFor();
  const address = "0x" + "c0ffee".repeat(6) + "abcd";
  await page.getByLabel("Address", { exact: true }).fill(address.toUpperCase().replace("0X", "0x"));
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await page.getByText(address).waitFor();
  check("watchlist entry added via UI (address normalized)", true);
  await page.getByLabel("Address", { exact: true }).fill(address);
  await page.getByRole("button", { name: "Add", exact: true }).click();
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

  // 7. Provider health check against the REAL network (outcome depends on network access).
  await page.goto(`${WEB}/providers`);
  await page.getByRole("button", { name: "Health check" }).first().waitFor();
  await page.getByRole("button", { name: "Health check" }).first().click();
  const verdict = page.getByText(/Check passed|Check failed/).first();
  await verdict.waitFor({ timeout: 90000 });
  const outcome = (await verdict.textContent())?.trim();
  check("health-check result is shown in the UI (real network)", true, `observed: ${outcome}`);
  const lifecycle = await page.getByRole("table", { name: "Provider registry" }).locator("tbody tr").first().locator(".pill").first().textContent();
  check("provider status reflects the recorded outcome", /Healthy|Unavailable|Degraded/.test(lifecycle ?? ""), `status=${lifecycle}`);
  realNetwork.healthCheck = outcome ?? "unknown";

  // 8. Mobile layout renders without horizontal overflow.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${WEB}/providers`);
  await page.getByRole("heading", { level: 1, name: "Providers" }).waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check("no horizontal page overflow at 390px", !overflow);
  await page.screenshot({ path: join(dataRoot, "mobile-providers.png") });

  // The deliberate duplicate submission above produces exactly one expected 409 resource error,
  // and a failed real-network health check may log a 502/503 resource error.
  const unexpected = consoleErrors.filter((m) => !/status of (409|502|503)/.test(m));
  check("no unexpected browser console errors", unexpected.length === 0, unexpected.join(" | "));

  // ---------- Phase B: same UI against the OFFLINE FIXTURE backend (fake RPC nodes) ----------
  await stop(backend);
  await stop(preview);
  await waitForPortFree(WEB);
  const fixtureRoot = mkdtempSync(join(tmpdir(), "bip-e2e-fixture-"));
  const fixture = start("uv", ["run", "python", "-m", "tests.stage2.serve_offline", String(FIXTURE_PORT)], {
    cwd: repoRoot,
    env: { ...process.env, BIP_DATA_ROOT: fixtureRoot },
  });
  await waitFor(`http://127.0.0.1:${FIXTURE_PORT}/api/v1/health`, "offline fixture backend");
  const wallets = /eth_wallet=(\S+) sol_wallet=(\S+)/.exec(fixture.getLog());
  const preview2 = start("npx", ["vite", "preview"], {
    cwd: resolve(repoRoot, "frontend"),
    env: { ...process.env, BIP_API_URL: `http://127.0.0.1:${FIXTURE_PORT}` },
  });
  await waitFor(WEB, "frontend preview (fixture)");
  await page.setViewportSize({ width: 1360, height: 900 });
  for (const [chain, address] of [["ethereum", wallets?.[1]], ["solana", wallets?.[2]]]) {
    await page.goto(`${WEB}/wallets/${chain}/${address}`);
    await page.getByRole("button", { name: "Retrieve recent activity" }).click();
    await page.getByText(/Run \d+: (succeeded|partial)/).waitFor({ timeout: 60000 });
    // stored observations live in the wallet workspace's Activity tab
    await page.getByRole("navigation", { name: "Wallet sections" }).getByRole("link", { name: "Activity", exact: true }).click();
    await page.getByText(/^Transactions \([1-9]\d*\)$/).waitFor();
    const rows = await page.locator("table").first().locator("tbody tr").count();
    check(`[offline fixture] ${chain} wallet retrieval stores and displays transactions`, rows > 0, `rows=${rows}`);
    if (chain === "solana") await page.screenshot({ path: join(dataRoot, "fixture-solana-wallet.png"), fullPage: true });
  }
  await page.getByRole("button", { name: "Retrieve recent activity" }).click();
  await page.getByText(/Run \d+: /).first().waitFor();
  const txCount = await page.getByText(/^Transactions \(\d+\)$/).textContent();
  check("[offline fixture] repeated retrieval does not duplicate rows", txCount === "Transactions (3)", `${txCount}`);
  await page.goto(`${WEB}/chains`);
  await page.getByRole("button", { name: "Fetch latest block" }).click();
  await page.getByText("20000000").first().waitFor();
  check("[offline fixture] latest block fetched and displayed", true);

  // ---------- Stage 3 intelligence on the same fixture data ----------
  const ethW = wallets?.[1];
  await page.goto(`${WEB}/wallets/ethereum/${ethW}/evidence`);
  await page.getByText(/Demo data: offline fixtures/).first().waitFor();
  check("[offline fixture] fixture data carries an explicit demo label", true);
  await page.getByText("2 transactions observed in retrieved dataset").or(page.getByText("transactions observed in retrieved dataset")).first().waitFor();
  check("[offline fixture] metrics are scope-labelled (retrieved dataset)", true);
  await page.getByRole("button", { name: "Run analysis" }).click();
  await page.getByText(/Analysis run \d+ completed|previous analysis was reused/).waitFor({ timeout: 30000 });
  check("[offline fixture] analysis run via UI", true);
  await page.getByText("Transaction frequency spike").first().waitFor();
  check("[offline fixture] detector results shown with explicit status", (await page.getByText("not enough data").count()) > 0);
  await page.getByRole("button", { name: "Corroborate recent" }).click();
  const verifyMsg = await page.getByText(/Checked \d+ of \d+:|Nothing left/).textContent({ timeout: 30000 });
  check("[offline fixture] corroboration without an independent provider is reported, not faked",
    /could not run/.test(verifyMsg ?? ""), `${verifyMsg}`);
  await page.goto(`${WEB}/flows?chain=ethereum&address=${ethW}`);
  await page.getByText("Per asset: in, out and net").waitFor();
  await page.getByRole("img", { name: /Daily flows/ }).waitFor();
  check("[offline fixture] capital flows page shows per-asset flows and chart", true);
  await page.screenshot({ path: join(dataRoot, "fixture-flows.png"), fullPage: true });
  await page.goto(`${WEB}/graph?chain=ethereum&address=${ethW}`);
  const graphLabel = await page.getByRole("img", { name: /Relationship graph/ }).getAttribute("aria-label");
  check("[offline fixture] relationship graph rendered from evidence", /[1-9]\d* edges/.test(graphLabel ?? ""), `${graphLabel}`);
  await page.getByRole("button", { name: "Zoom in" }).click();
  await page.getByRole("complementary", { name: "Selection details" }).locator("button.linklike").first().click();
  await page.getByText("Evidence", { exact: true }).waitFor();
  check("[offline fixture] graph edge shows its evidence", true);
  await page.screenshot({ path: join(dataRoot, "fixture-graph.png"), fullPage: true });
  await page.goto(`${WEB}/opportunities`);
  await page.getByRole("button", { name: "Evaluate Ethereum" }).click();
  await page.getByText(/insufficient data|evaluated/).first().waitFor();
  check("[offline fixture] opportunity evaluation returns no candidate on insufficient data",
    (await page.getByText("No research leads").count()) === 1);
  // ---------- Stage 4 provider intelligence on the fixture backend (fake RPC nodes) ----------
  await page.goto(`${WEB}/providers`);
  const profilesTable = page.getByRole("table", { name: "Provider capability profiles" });
  await profilesTable.waitFor();
  check("[offline fixture] provider intelligence page opens", true);
  check("[offline fixture] provider page is labelled as fixture data",
    (await page.getByText(/Demo data: offline fixtures/).count()) > 0);
  const rowFor = (id) => profilesTable.locator("tr", { hasText: id }).first();
  await rowFor("publicnode-eth").getByRole("button", { name: "Capabilities" }).click();
  await page.getByRole("table", { name: /Capability matrix for PublicNode Ethereum/ }).waitFor();
  check("[offline fixture] Ethereum provider capability matrix shown", true);
  await rowFor("solana-public").getByRole("button", { name: "Capabilities" }).click();
  const solMatrix = page.getByRole("table", { name: /Capability matrix for Solana/ });
  await solMatrix.waitFor();
  check("[offline fixture] Solana provider capability matrix shown, untested before any run",
    (await solMatrix.getByText("Never probed.").count()) > 0);
  await page.getByRole("button", { name: "Preview budget" }).click();
  const previewBox = page.getByLabel("Probe preview");
  await previewBox.waitFor();
  const previewText = (await previewBox.textContent()) ?? "";
  check("[offline fixture] probe budget preview shows bounded requests", /At most \d+ requests/.test(previewText) && /0 retries/.test(previewText), previewText.slice(0, 80));
  await page.getByRole("button", { name: "Run bounded provider check" }).click();
  const resultBox = page.getByLabel("Probe run result");
  await resultBox.waitFor({ timeout: 90000 });
  const resultText = (await resultBox.textContent()) ?? "";
  check("[offline fixture] simulated probe run completed with results", /Run (completed|partial)/.test(resultText) && /requests sent/.test(resultText), resultText.slice(0, 90));
  check("[offline fixture] probe results explain failures (skipped Solana slot)",
    /does not have the requested data/.test(resultText));
  await rowFor("publicnode-eth").getByRole("button", { name: "Capabilities" }).click();
  await page.getByText("Reliability observations").first().waitFor();
  check("[offline fixture] reliability shows insufficient data after one run",
    (await page.getByText(/Insufficient data/).count()) > 0);
  await page.screenshot({ path: join(dataRoot, "fixture-providers.png"), fullPage: true });
  // ---------- Stage 5 ML classification + AI research on the fixture backend ----------
  await page.goto(`${WEB}/research`);
  await page.getByRole("tab", { name: "Models and AI provider" }).click();
  await page.getByText("No model trained").waitFor();
  check("[offline fixture] model registry starts empty (rules only)", true);
  await page.getByRole("button", { name: "Train Ethereum on reviewed labels" }).click();
  const refusal = (await page.getByText(/insufficient data: \d+ distinct labelled wallet/).textContent({ timeout: 30000 })) ?? "";
  check("[offline fixture] training on real labels refuses without enough reviewed labels", /No model was trained/.test(refusal), refusal.slice(0, 80));
  await page.getByRole("button", { name: "Train synthetic demo (Ethereum)" }).click();
  await page.getByRole("table", { name: "Model registry" }).waitFor({ timeout: 60000 });
  check("[offline fixture] synthetic demo model is labelled experimental, not real-world",
    (await page.getByText("experimental synthetic").count()) > 0 && (await page.getByText("not real-world performance").count()) > 0);
  await page.getByText(/No AI provider is configured/).first().waitFor();
  check("[offline fixture] AI provider absence is stated (deterministic mode)", true);
  await page.goto(`${WEB}/research?chain=solana&address=${wallets?.[2]}`);
  await page.getByRole("button", { name: "Classify wallet" }).click();
  const cls = page.getByLabel("Classification result");
  await cls.waitFor({ timeout: 30000 });
  const clsText = (await cls.textContent()) ?? "";
  check("[offline fixture] wallet classified with rules and labelled limitations",
    /wallet account/.test(clsText) && /Demo data: offline fixtures/.test(clsText), clsText.slice(0, 60));
  await page.getByRole("button", { name: "Preview research plan" }).click();
  await page.getByLabel("Research plan").waitFor();
  await page.getByRole("button", { name: "Start investigation" }).click();
  const report = page.getByLabel("Research report");
  await report.waitFor({ timeout: 90000 });
  const reportText = (await report.textContent()) ?? "";
  check("[offline fixture] investigation completes with an evidence-labelled report",
    /Observed blockchain data/.test(reportText) && /No AI model available/.test(reportText), reportText.slice(0, 80));
  check("[offline fixture] report never presents predictions as verified facts", !/\bverified fact\b(?! )/.test(reportText.replace(/not a verified fact/g, "")));
  await page.screenshot({ path: join(dataRoot, "fixture-research.png"), fullPage: true });

  // ---------- UI integration (Milestones B-D) on the fixture backend ----------
  const solW = wallets?.[2] ?? "";
  await page.goto(`${WEB}/`);
  await page.getByLabel("Search wallet address").fill(` ${solW} `);
  await page.getByLabel("Search wallet address").press("Enter");
  await page.getByRole("navigation", { name: "Wallet sections" }).waitFor();
  check("[offline fixture] global search opens the wallet workspace", page.url().endsWith(`/wallets/solana/${solW}`), page.url());
  await page.goto(`${WEB}/wallets/ethereum/0x${"12".repeat(20)}`);
  await page.getByText("Nothing has been retrieved for this address yet").waitFor();
  check("[offline fixture] unstored wallet shows an honest first-visit state", (await page.getByText("Request failed").count()) === 0);
  await page.goto(`${WEB}/wallets/solana/${solW}/research`);
  const runLink = page.getByRole("link", { name: "What kind of wallet is this, and what does the evidence support?" }).first();
  await runLink.waitFor({ timeout: 30000 });
  await runLink.click();
  const prov = page.getByRole("region", { name: "Provenance" });
  await prov.waitFor({ timeout: 30000 });
  const provText = (await prov.textContent()) ?? "";
  check("[offline fixture] report provenance records report version, rules and feature digest",
    /1\.1\.0/.test(provText) && /bip-rules/.test(provText) && /Feature digest[0-9a-f]{8}…/.test(provText), provText.slice(0, 120));
  const runUrl = page.url();
  await page.goto(`${WEB}/investigations`);
  await page.getByLabel("Title", { exact: true }).fill("E2E case");
  await page.getByRole("button", { name: "Create investigation" }).click();
  await page.getByText("E2E case", { exact: true }).click();
  await page.waitForURL(/\/investigations\/\d+$/);
  const caseUrl = page.url();
  await page.goto(runUrl);
  await page.getByLabel("Case", { exact: true }).selectOption({ label: "E2E case" });
  await page.getByRole("button", { name: "Add to case" }).click();
  await page.getByText(/Added to E2E case/).waitFor();
  await page.goto(caseUrl);
  await page.getByRole("region", { name: "Research runs" }).getByRole("link").first().waitFor();
  check("[offline fixture] research run linked to a case and listed there", true);

  // persistence across a backend restart: stop the fixture server, start it on the same data
  await stop(fixture);
  const fixtureAgain = start("uv", ["run", "python", "-m", "tests.stage2.serve_offline", String(FIXTURE_PORT)], {
    cwd: repoRoot,
    env: { ...process.env, BIP_DATA_ROOT: fixtureRoot },
  });
  await waitFor(`http://127.0.0.1:${FIXTURE_PORT}/api/v1/health`, "offline fixture backend (restarted)");
  await page.goto(runUrl);
  await page.getByRole("region", { name: "Provenance" }).waitFor({ timeout: 30000 });
  await page.goto(caseUrl);
  await page.getByRole("region", { name: "Research runs" }).getByRole("link").first().waitFor({ timeout: 30000 });
  check("[offline fixture] run, report and case link survive a backend restart", true);

  // layout at the Galaxy S24 FE size (412x915, touch) and desktop, every route, with data present
  const layoutFailures = await checkLayout(browser, WEB, { screenshotDir: dataRoot });
  check("[offline fixture] layout: no overflow, tap targets >= 44px touch / 32px desktop, compact header",
    layoutFailures.length === 0, layoutFailures.slice(0, 3).join(" | "));
  void preview2;
  void fixtureAgain;
  await browser.close();
} catch (err) {
  check("e2e run completed", false, String(err));
  for (const c of children) console.error(c.getLog().slice(-2000));
} finally {
  for (const c of children) await stop(c);
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed. Artifacts: ${dataRoot}`);
  console.log(`Real-network provider health check observed: ${realNetwork.healthCheck}`);
  if (failed === 0 && !process.env.BIP_E2E_KEEP) rmSync(dataRoot, { recursive: true, force: true });
  process.exit(failed === 0 ? 0 : 1);
}
