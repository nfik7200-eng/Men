// Sign-in end-to-end journeys: real backend started with BIP_AUTH__MODE=required on a THROWAWAY data
// root, the built frontend, and Chromium at phone, tablet and desktop sizes. No mocks, no provider
// contact (the only provider-facing control the journey touches is refused by the server).
//
// Standalone:  npm run build && npm run e2e:auth     (also run at the end of npm run e2e)
//
// SYNTHETIC CREDENTIALS: the usernames and the password below exist only in this script, are
// written only into the throwaway database it creates, and must never be reused anywhere else.
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { measurePage } from "./layout.mjs";

const require = createRequire(import.meta.url);

const repoRoot = resolve(import.meta.dirname, "..", "..");
const API_PORT = 8767;
const WEB = "http://127.0.0.1:4173";
const API = `http://127.0.0.1:${API_PORT}/api/v1`;

const SYNTHETIC_PASSWORD = "SYNTHETIC-e2e-only-9f3k-not-a-real-secret";
const SYNTHETIC_NEW_PASSWORD = "SYNTHETIC-e2e-changed-4m2q-not-a-real-secret";
const SYNTHETIC_WRONG = "SYNTHETIC-wrong-password-0000";
const USERS = [
  { name: "e2e-analyst", role: "analyst" },
  { name: "e2e-viewer", role: "viewer" },
  { name: "e2e-admin", role: "admin" },
  { name: "e2e-pwchange", role: "analyst" },
];

const VIEWPORTS = [
  { name: "phone 390x844", viewport: { width: 390, height: 844 }, touch: true, minTarget: 44 },
  { name: "tablet 820x1180", viewport: { width: 820, height: 1180 }, touch: true, minTarget: 44 },
  { name: "desktop 1366x850", viewport: { width: 1366, height: 850 }, touch: false, minTarget: 32 },
];

const children = [];

function start(cmd, args, opts) {
  const child = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"], detached: true, ...opts });
  let log = "";
  child.stdout.on("data", (d) => (log += d));
  child.stderr.on("data", (d) => (log += d));
  child.getLog = () => log;
  children.push(child);
  return child;
}

async function stop(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise((r) => child.once("exit", r));
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch {
    child.kill("SIGTERM");
  }
  await exited;
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

function backendEnv(dataRoot) {
  return {
    ...process.env,
    BIP_ENV: "test",
    BIP_DATA_ROOT: dataRoot,
    BIP_MIN_FREE_BYTES: "0",
    BIP_MIN_FREE_RATIO: "0",
    BIP_API__PORT: String(API_PORT),
    BIP_API__CORS_ORIGINS: WEB,
    BIP_LOG_FORMAT: "json",
    BIP_AUTH__MODE: "required",
    BIP_AUTH__COOKIE_SECURE: "false", // plain-HTTP localhost test server
    BIP_AUTH__RATE_LIMIT_PER_MINUTE: "0", // three viewports from one address
    BIP_AUTH__LOGIN_RATE_LIMIT_PER_MINUTE: "1000",
  };
}

function seedUser(dataRoot, user) {
  return new Promise((resolveSeed, reject) => {
    const child = spawn("uv", ["run", "python", "-m", "bip.tools.users", "create", user.name, "--role", user.role], {
      cwd: repoRoot,
      env: { ...backendEnv(dataRoot), BIP_NEW_PASSWORD: SYNTHETIC_PASSWORD },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (out += d));
    child.on("exit", (code) => (code === 0 ? resolveSeed() : reject(new Error(`seeding ${user.name} failed: ${out}`))));
  });
}

async function signIn(page, username, password) {
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

/** Use the main navigation; on narrow screens it sits behind the Menu button. */
async function navigate(page, name) {
  const menu = page.getByRole("button", { name: "Menu", exact: true });
  if ((await menu.isVisible()) && (await menu.getAttribute("aria-expanded")) === "false") await menu.click();
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name, exact: true }).click();
}

async function signOut(page) {
  await page.getByRole("button", { name: /^Account menu/ }).click();
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.getByRole("heading", { level: 1, name: "Sign in" }).waitFor();
}

/** Fetch from inside the page (same origin, cookies included), optionally with the CSRF header. */
async function apiFromPage(page, method, path, { body, csrf } = {}) {
  return page.evaluate(
    async ({ method, path, body, csrf }) => {
      const r = await fetch(`/api/v1${path}`, {
        method,
        credentials: "same-origin",
        headers: { Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}), ...(csrf ? { "X-CSRF-Token": csrf } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      let json = null;
      try {
        json = await r.json();
      } catch {
        /* no body */
      }
      return { status: r.status, json };
    },
    { method, path, body, csrf },
  );
}

async function csrfFromPage(page) {
  const me = await apiFromPage(page, "GET", "/auth/me");
  return me.json?.csrf_token ?? null;
}

async function layoutOk(page, vp, label, check, prefix) {
  const m = await measurePage(page, vp.minTarget);
  check(`${prefix} ${label}: no horizontal overflow`, m.overflow <= 0, `overflow=${m.overflow}px`);
  check(
    `${prefix} ${label}: tap targets >= ${vp.minTarget}px${vp.touch ? " (touch)" : ""}`,
    m.smallCount === 0,
    m.smallCount ? JSON.stringify(m.small) : "",
  );
}

async function inViewport(page, selector) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.left >= 0 && r.right <= window.innerWidth + 0.5 && r.top >= 0 && r.bottom <= window.innerHeight + 0.5;
  }, selector);
}

async function journey(browser, vp, check) {
  const prefix = `[auth e2e] ${vp.name}:`;
  const ctx = await browser.newContext({
    viewport: vp.viewport,
    hasTouch: vp.touch,
    isMobile: vp.touch,
  });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));
  const heading = (name, level = 1) => page.getByRole("heading", { level, name, exact: true });
  const unique = `${vp.viewport.width}`;

  // 1. anonymous visit: only the sign-in screen
  await page.goto(`${WEB}/`);
  await heading("Sign in").waitFor();
  const navCount = await page.getByRole("navigation", { name: "Main" }).count();
  check(`${prefix} anonymous visit shows only the sign-in screen`, navCount === 0 && (await heading("Overview").count()) === 0);
  const labelled = (await page.getByLabel("Username").getAttribute("autocomplete")) === "username" &&
    (await page.getByLabel("Password").getAttribute("autocomplete")) === "current-password";
  check(`${prefix} sign-in fields are labelled with username / current-password autocomplete`, labelled);
  await layoutOk(page, vp, "sign-in screen", check, prefix);
  const direct = await apiFromPage(page, "GET", "/watchlists");
  check(`${prefix} protected API refuses an anonymous caller (401 unauthenticated)`, direct.status === 401 && direct.json?.error?.code === "unauthenticated", `status=${direct.status}`);

  // 2. wrong password
  await signIn(page, "e2e-analyst", SYNTHETIC_WRONG);
  await page.getByRole("alert").filter({ hasText: "Invalid username or password" }).waitFor();
  check(`${prefix} wrong password shows an error and stays on sign-in`, (await heading("Sign in").count()) === 1);
  check(`${prefix} password field is cleared after a failed attempt`, (await page.getByLabel("Password").inputValue()) === "");

  // 3. correct login lands on the dashboard
  await page.getByLabel("Password").fill(SYNTHETIC_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await heading("Overview").waitFor();
  const menuName = await page.getByRole("button", { name: /^Account menu/ }).getAttribute("aria-label");
  check(`${prefix} correct login shows the dashboard with the user menu`, menuName === "Account menu, e2e-analyst, analyst", `${menuName}`);
  check(`${prefix} user menu button is inside the viewport`, await inViewport(page, ".user-menu-button"));
  await layoutOk(page, vp, "dashboard", check, prefix);
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
  const csrf = await csrfFromPage(page);
  check(`${prefix} CSRF token is not kept in web storage`, !!csrf && !stored.includes(csrf));
  const cookies = await ctx.cookies();
  const session = cookies.find((c) => c.name === "bip_session");
  check(`${prefix} session cookie is HttpOnly and SameSite=Strict`, !!session && session.httpOnly && session.sameSite === "Strict");

  // 4. user menu + change-password dialog fit the screen
  await page.getByRole("button", { name: /^Account menu/ }).click();
  await page.getByRole("group", { name: "Account" }).waitFor();
  check(`${prefix} open user menu is inside the viewport`, await inViewport(page, ".user-menu-panel"));
  await layoutOk(page, vp, "user menu open", check, prefix);
  await page.getByRole("button", { name: "Change password" }).click();
  await page.getByRole("dialog", { name: "Change password" }).waitFor();
  check(`${prefix} change-password dialog is inside the viewport`, await inViewport(page, ".modal"));
  await layoutOk(page, vp, "change-password dialog", check, prefix);
  await page.keyboard.press("Escape");
  await page.getByRole("dialog").waitFor({ state: "detached" });

  // 5. reload keeps the session, and a protected write still works (CSRF recovered)
  await page.reload();
  await heading("Overview").waitFor();
  check(`${prefix} reload keeps the session`, (await page.getByRole("heading", { level: 1, name: "Sign in" }).count()) === 0);
  await navigate(page, "Watchlists");
  await heading("Watchlists").waitFor();
  const listName = `E2E list ${unique}`;
  await page.getByLabel("Name", { exact: true }).fill(listName);
  await page.getByRole("button", { name: "Create watchlist" }).click();
  await page.getByRole("heading", { level: 2, name: listName }).waitFor();
  check(`${prefix} protected write works after reload (CSRF header sent)`, true);
  await layoutOk(page, vp, "watchlists (analyst)", check, prefix);
  const noCsrf = await apiFromPage(page, "POST", "/watchlists", { body: { name: "no token", description: null } });
  check(`${prefix} write without the CSRF token is refused (403 csrf_failed)`, noCsrf.status === 403 && noCsrf.json?.error?.code === "csrf_failed", `status=${noCsrf.status}`);
  const adminOnly = await apiFromPage(page, "PATCH", "/providers/publicnode-eth", { body: { enabled: false }, csrf: await csrfFromPage(page) });
  check(`${prefix} analyst is refused an admin action (403 forbidden)`, adminOnly.status === 403 && adminOnly.json?.error?.code === "forbidden", `status=${adminOnly.status}`);

  // 6. sign out returns to sign-in
  await signOut(page);
  check(`${prefix} sign out returns to the sign-in screen`, (await page.getByText("You have signed out.").count()) === 1);
  const afterOut = await apiFromPage(page, "GET", "/watchlists");
  check(`${prefix} the session is gone server-side after sign out`, afterOut.status === 401, `status=${afterOut.status}`);
  await page.reload();
  await heading("Sign in").waitFor();
  check(`${prefix} reload after sign out stays on sign-in`, true);

  // 7. viewer: analyst actions are disabled with a reason; the server still says no
  await signIn(page, "e2e-viewer", SYNTHETIC_PASSWORD);
  // the URL is kept across sign-out and reload, so the viewer lands back on Watchlists
  await heading("Watchlists").waitFor();
  const create = page.getByRole("button", { name: "Create watchlist" });
  check(`${prefix} viewer sees "Create watchlist" disabled`, await create.isDisabled());
  const reason = await page.getByText("Needs the analyst role. You are signed in as viewer.").first().isVisible();
  check(`${prefix} viewer sees why (needs the analyst role)`, reason);
  await layoutOk(page, vp, "watchlists (viewer)", check, prefix);
  const viewerCsrf = await csrfFromPage(page);
  const viewerWrite = await apiFromPage(page, "POST", "/watchlists", { body: { name: "viewer write", description: null }, csrf: viewerCsrf });
  check(`${prefix} viewer write with a valid CSRF token is refused (403 forbidden)`, viewerWrite.status === 403 && viewerWrite.json?.error?.code === "forbidden", `status=${viewerWrite.status}`);
  // force an admin-only control on (as a hostile or stale UI would) and see the server's refusal rendered
  await navigate(page, "Providers");
  await heading("Providers").waitFor();
  const registry = page.getByRole("table", { name: "Provider registry" });
  await registry.getByRole("button", { name: "Details" }).first().click();
  const toggle = registry.getByRole("button", { name: /^(Disable|Enable)$/ }).first();
  await toggle.waitFor();
  check(`${prefix} viewer sees the admin-only provider switch disabled`, await toggle.isDisabled());
  // React ignores clicks on a button whose `disabled` prop is true, so flip the prop as well as the
  // attribute: this simulates a stale or tampered UI offering an action the server refuses.
  await toggle.evaluate((el) => {
    el.removeAttribute("disabled");
    const key = Object.keys(el).find((k) => k.startsWith("__reactProps"));
    if (key) el[key].disabled = false;
  });
  await toggle.click();
  await page.getByRole("alert").filter({ hasText: "Your role does not allow this. This action needs the admin role." }).first().waitFor();
  check(`${prefix} viewer who forces the action sees the forbidden message`, true);
  check(`${prefix} a 403 does not sign the user out`, (await page.getByRole("button", { name: /^Account menu/ }).count()) === 1);
  // coverage: provider-contacting collector runs are admin-only, and a viewer is told so
  await navigate(page, "Coverage");
  await heading("Coverage").waitFor();
  const runButtons = page.getByRole("button", { name: /^Run (Ethereum|Solana) collector once$/ });
  await runButtons.first().waitFor();
  check(`${prefix} viewer sees both collector "Run once" buttons disabled`, (await runButtons.count()) === 2 && (await runButtons.nth(0).isDisabled()) && (await runButtons.nth(1).isDisabled()));
  check(`${prefix} viewer sees why (needs the admin role)`, await page.getByText("Needs the admin role. You are signed in as viewer.").first().isVisible());
  await layoutOk(page, vp, "coverage (viewer)", check, prefix);
  await signOut(page);

  check(`${prefix} no uncaught page errors`, pageErrors.length === 0, pageErrors.join(" | "));
  await ctx.close();
}

/** The password-change journey needs its own user because it changes the credentials. */
async function passwordJourney(browser, check) {
  const prefix = "[auth e2e] desktop 1366x850:";
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 850 } });
  const page = await ctx.newPage();
  await page.goto(`${WEB}/`);
  await signIn(page, "e2e-pwchange", SYNTHETIC_PASSWORD);
  await page.getByRole("heading", { level: 1, name: "Overview", exact: true }).waitFor();
  await page.getByRole("button", { name: /^Account menu/ }).click();
  await page.getByRole("button", { name: "Change password" }).click();
  const dialog = page.getByRole("dialog", { name: "Change password" });
  await dialog.getByLabel("Current password").fill(SYNTHETIC_PASSWORD);
  await dialog.getByLabel(/^New password/).fill(SYNTHETIC_NEW_PASSWORD);
  await dialog.getByLabel("Repeat new password").fill(SYNTHETIC_NEW_PASSWORD);
  await dialog.getByRole("button", { name: "Change password" }).click();
  await page.getByRole("heading", { level: 1, name: "Sign in", exact: true }).waitFor();
  check(`${prefix} changing the password signs the user out with a notice`, (await page.getByText(/Password changed/).count()) === 1);
  await signIn(page, "e2e-pwchange", SYNTHETIC_PASSWORD);
  await page.getByRole("alert").filter({ hasText: "Invalid username or password" }).waitFor();
  check(`${prefix} the old password no longer works`, true);
  await page.getByLabel("Password").fill(SYNTHETIC_NEW_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("heading", { level: 1, name: "Overview", exact: true }).waitFor();
  check(`${prefix} the new password works`, true);
  await ctx.close();
}

/**
 * Run all journeys. `browser` is an open Playwright browser; `check(name, ok, detail)` records a
 * result. Starts and stops its own backend and preview server (ports 8767 and 4173), so the
 * caller must have stopped anything on 4173.
 */
export async function runAuthJourneys(browser, check) {
  const dataRoot = mkdtempSync(join(tmpdir(), "bip-e2e-auth-"));
  let backend;
  let preview;
  try {
    for (const user of USERS) await seedUser(dataRoot, user);
    backend = start("uv", ["run", "python", "-m", "bip.api"], { cwd: repoRoot, env: backendEnv(dataRoot) });
    await waitFor(`${API}/health`, "auth-mode backend");
    const status = await (await fetch(`${API}/auth/status`)).json();
    check("[auth e2e] backend runs with sign-in required", status.mode === "required" && status.authenticated === false, JSON.stringify(status));
    const anon = await fetch(`${API}/watchlists`);
    check("[auth e2e] backend refuses an anonymous request (401)", anon.status === 401, `status=${anon.status}`);
    preview = start("npx", ["vite", "preview"], {
      cwd: resolve(repoRoot, "frontend"),
      env: { ...process.env, BIP_API_URL: `http://127.0.0.1:${API_PORT}` },
    });
    await waitFor(WEB, "frontend preview (auth)");
    for (const vp of VIEWPORTS) await journey(browser, vp, check);
    await passwordJourney(browser, check);
  } catch (err) {
    check("[auth e2e] journeys completed", false, String(err));
    for (const c of children) console.error(c.getLog().slice(-1500));
  } finally {
    await stop(preview);
    await stop(backend);
    try {
      await waitForPortFree(WEB);
    } catch {
      /* reported by the caller's own cleanup */
    }
    rmSync(dataRoot, { recursive: true, force: true });
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const { chromium } = require("playwright");
  const results = [];
  const check = (name, ok, detail = "") => {
    results.push(ok);
    console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
  };
  const browser = await chromium.launch();
  await runAuthJourneys(browser, check);
  await browser.close();
  const failed = results.filter((ok) => !ok).length;
  console.log(`\n${results.length - failed}/${results.length} auth checks passed.`);
  process.exit(failed === 0 ? 0 : 1);
}
