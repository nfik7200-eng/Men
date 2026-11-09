const { chromium } = require('playwright');
// Reflow check (WCAG 1.4.10): no horizontal page scroll at 320 CSS px. Usage: E2E_BASE=http://127.0.0.1:8042 E2E_ADMIN_TOKEN=... node e2e/reflow-check.js
// Scroll containers that scroll on purpose (tabindex=0 .scroll) are excluded; the page itself must not scroll.
const BASE = process.env.E2E_BASE || 'http://127.0.0.1:8042';
const TOKEN = process.env.E2E_ADMIN_TOKEN || 'e2e-admin-token-0123456789-abc';
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 320, height: 640 } });
  const page = await ctx.newPage();
  const api = await (await ctx.request.get(BASE + '/api/v1/listings?q=Raspberry+Pi+5&per_page=100&data=real')).json();
  const lid = api.items[0].id; const pid = api.items.find(i => i.product_id).product_id;
  await page.goto(BASE + '/admin/login'); await page.fill('input[name=token]', TOKEN);
  await Promise.all([page.waitForNavigation(), page.click('button')]);
  const paths = ['/', '/search?q=raspberry', '/listing/' + lid, '/product/' + pid, '/deals', '/coverage', '/research?q=raspberry+pi+5+8gb', '/watchlist', '/notifications', '/admin/login', '/admin'];
  for (const p of paths) {
    await page.goto(BASE + p, { waitUntil: 'networkidle' });
    await page.waitForTimeout(300);
    const over = await page.evaluate(() => {
      const w = document.documentElement.clientWidth;
      const offenders = [...document.querySelectorAll('body *')].filter(e => {
        const r = e.getBoundingClientRect();
        if (r.width === 0) return false;
        // ignore content inside a deliberate scroll container (tabindex=0 .scroll), which scrolls by design
        if (e.closest('.scroll')) return false;
        return r.right > w + 1;
      }).slice(0, 3).map(e => (e.tagName + '.' + (e.className || '')).toString().slice(0, 60));
      return { scroll: document.documentElement.scrollWidth - w, offenders };
    });
    console.log(p.padEnd(34), 'overflow px:', over.scroll, over.offenders.length ? over.offenders.join(' | ') : '');
  }
  await browser.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
