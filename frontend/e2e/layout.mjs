// Layout checks against a running BIP frontend (dev server or preview). No mocks.
// Usage (from frontend/): node e2e/layout.mjs [baseUrl] [--shots dir]
// Checks every route at the Galaxy S24 FE viewport (412x915, touch) and at desktop (1360x900):
//   - no page-level horizontal overflow
//   - tap targets >= 44 px on touch, >= 32 px on desktop
//   - compact mobile header (the top bar must not take a large empty band)
//   - panel headings in sentence case (no uppercase transform)
import { createRequire } from "node:module";
import { join } from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const args = process.argv.slice(2);
const base = args.find((a) => a.startsWith("http")) ?? "http://127.0.0.1:5173";
const shotsIdx = args.indexOf("--shots");
const shots = shotsIdx >= 0 ? args[shotsIdx + 1] : null;
const ETH = "0x00000000000000000000000000000000000000aa";

export const ROUTES = [
  "/",
  "/chains",
  "/providers",
  "/wallets",
  `/wallets/ethereum/${ETH}`,
  "/flows",
  "/graph",
  "/research",
  "/opportunities",
  "/investigations",
  "/watchlists",
  "/settings",
];

const VIEWPORTS = [
  { name: "s24fe", viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true, minTarget: 44 },
  { name: "desktop", viewport: { width: 1360, height: 900 }, hasTouch: false, isMobile: false, minTarget: 32 },
];

/** Retrieve the fixture wallet first so pages are checked with data (an empty DB hides overflow). */
async function seed(baseUrl) {
  try {
    await fetch(`${baseUrl}/api/v1/ingestion/wallet/ethereum/${ETH}?limit=20`, { method: "POST" });
  } catch {
    /* backend not running: pages are checked in their offline state */
  }
}

export async function checkLayout(browser, baseUrl, { screenshotDir } = {}) {
  const failures = [];
  await seed(baseUrl);
  for (const vp of VIEWPORTS) {
    const ctx = await browser.newContext({ viewport: vp.viewport, hasTouch: vp.hasTouch, isMobile: vp.isMobile });
    const page = await ctx.newPage();
    for (const route of ROUTES) {
      await page.goto(baseUrl + route);
      await page.waitForLoadState("networkidle");
      const m = await page.evaluate((minTarget) => {
        const vw = window.innerWidth;
        const visible = (e) => {
          const r = e.getBoundingClientRect();
          const s = getComputedStyle(e);
          return r.width > 0 && r.height > 0 && s.visibility !== "hidden";
        };
        const targets = [...document.querySelectorAll("button, a[href], input:not([type=hidden]), select, textarea, summary")]
          .filter(visible)
          .filter((e) => !e.closest(".graph-svg"))
          // WCAG 2.5.8 exempts links inside a sentence or block of text
          .filter((e) => !(e.tagName === "A" && getComputedStyle(e).display === "inline" && (e.parentElement?.textContent ?? "").trim().length > (e.textContent ?? "").trim().length + 3))
          .map((e) => {
            // a checkbox or radio inside a label counts by its label's box
            const box = (e.matches("input[type=checkbox], input[type=radio]") && e.closest("label")) || e;
            const r = box.getBoundingClientRect();
            return { h: r.height, w: r.width, text: (e.textContent || e.getAttribute("aria-label") || e.tagName).trim().slice(0, 30) };
          })
          .filter((t) => t.h < minTarget - 0.5);
        const header = document.querySelector(".sidebar");
        const h2 = [...document.querySelectorAll("main h2")].filter((e) => getComputedStyle(e).textTransform === "uppercase");
        return {
          overflow: document.documentElement.scrollWidth - vw,
          small: targets.slice(0, 5),
          smallCount: targets.length,
          headerHeight: header ? Math.round(header.getBoundingClientRect().height) : 0,
          uppercaseH2: h2.length,
        };
      }, vp.minTarget);
      const where = `${vp.name} ${route.slice(0, 32)}`;
      if (m.overflow > 0) failures.push(`${where}: horizontal overflow ${m.overflow}px`);
      if (m.smallCount > 0) failures.push(`${where}: ${m.smallCount} tap targets < ${vp.minTarget}px, e.g. ${JSON.stringify(m.small)}`);
      if (vp.isMobile && m.headerHeight > 96) failures.push(`${where}: mobile header ${m.headerHeight}px tall (max 96)`);
      if (m.uppercaseH2 > 0) failures.push(`${where}: ${m.uppercaseH2} uppercase panel headings`);
      if (screenshotDir) {
        const name = `${vp.name}${route.split("?")[0].replace(/\//g, "_")}`.slice(0, 48);
        await page.screenshot({ path: join(screenshotDir, `${name}.png`), fullPage: vp.name === "desktop" });
      }
    }
    await ctx.close();
  }
  return failures;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const browser = await chromium.launch();
  const failures = await checkLayout(browser, base, { screenshotDir: shots });
  await browser.close();
  for (const f of failures) console.log("FAIL", f);
  console.log(failures.length === 0 ? "layout: all checks passed" : `layout: ${failures.length} failures`);
  process.exit(failures.length === 0 ? 0 : 1);
}
