const { chromium, devices } = require('playwright');
const fs = require('fs');
// Usage: AXE_PATH=/path/to/axe.min.js E2E_BASE=http://127.0.0.1:8042 E2E_ADMIN_TOKEN=... node e2e/axe-check.js
// NO_ISLANDS=1 audits the no-JavaScript fallback pages instead of the islands.
// axe-core is NOT a project dependency: install it outside the repo (npm i axe-core@4.10.2 in a scratch folder).
const AXE = process.env.AXE_PATH;
if (!AXE) { console.error('set AXE_PATH to axe.min.js'); process.exit(2); }
const BASE = process.env.E2E_BASE || 'http://127.0.0.1:8042';
const TOKEN = process.env.E2E_ADMIN_TOKEN || 'e2e-admin-token-0123456789-abc';
(async () => {
  const browser = await chromium.launch();
  const api = await (await browser.newContext()).request.get(BASE + '/api/v1/listings?q=Raspberry+Pi+5&per_page=100&data=real');
  const items = (await api.json()).items || [];
  const lid = items[0] && items[0].id;
  const pid = items.find(i => i.product_id) && items.find(i => i.product_id).product_id;
  const pages = ['/', '/search?q=raspberry', lid ? '/listing/' + lid : null, pid ? '/product/' + pid : null, '/deals', '/coverage',
    '/research?q=raspberry+pi+5+8gb', '/watchlist', '/notifications', '/admin/login'].filter(Boolean);
  const out = {};
  for (const [label, opts] of [['desktop', { viewport: { width: 1280, height: 900 } }], ['mobile', { ...devices['Pixel 7'] }]]) {
    const ctx = await browser.newContext({ ...opts, bypassCSP: true });  // test harness only: lets axe be injected; the app's CSP is unchanged
    const page = await ctx.newPage();
    // NO_ISLANDS=1 audits the server-rendered fallback: the island bundle is blocked, so no page script replaces the server copy.
    if (process.env.NO_ISLANDS === '1') await page.route('**/static/app/assets/main.js', r => r.abort());
    const paths = pages.slice();
    await page.goto(BASE + '/admin/login');
    await page.fill('input[name=token]', TOKEN);
    await Promise.all([page.waitForNavigation(), page.click('button')]);
    paths.push('/admin');
    for (const p of paths) {
      await page.goto(BASE + p, { waitUntil: 'networkidle' });
      await page.addScriptTag({ path: AXE });
      const res = await page.evaluate(async () => {
        const r = await window.axe.run(document, { resultTypes: ['violations'] });
        return r.violations.map(v => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length,
          sample: (v.nodes[0] && v.nodes[0].target.join(' ')) || '' }));
      });
      out[label + ' ' + p] = res;
    }
    await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(process.env.AXE_OUT || 'axe-results.json', JSON.stringify(out, null, 2));
  for (const [k, v] of Object.entries(out)) {
    const serious = v.filter(x => x.impact === 'serious' || x.impact === 'critical');
    console.log(k.padEnd(28), 'violations:', v.length, ' serious+critical:', serious.length, serious.map(x => x.id).join(','));
  }
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
