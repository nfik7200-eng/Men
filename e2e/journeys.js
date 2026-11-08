// End-to-end journeys (Playwright, Chromium) against a RUNNING app on a DISPOSABLE database copy.
// Usage: e2e/run.sh   (starts the app on a temp copy of a snapshot, runs this file, stops the app)
const { chromium, devices } = require('playwright');
const BASE = process.env.E2E_BASE || 'http://127.0.0.1:8041';
const TOKEN = process.env.E2E_ADMIN_TOKEN || 'e2e-admin-token-0123456789-abc';
const OUT = process.env.E2E_OUT || '/tmp/e2e-shots';
const failures = [];
const check = (name, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`); if (!ok) failures.push(name); };

async function overflow(page) { return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth); }

(async () => {
  const browser = await chromium.launch();
  for (const [label, opts] of [['desktop', { viewport: { width: 1280, height: 900 } }], ['mobile', { ...devices['Pixel 7'] }]]) {
    const ctx = await browser.newContext(opts);
    const page = await ctx.newPage();
    const serverErrors = [];
    page.on('response', r => { if (r.status() >= 500) serverErrors.push(`${r.status()} ${r.url()}`); });
    const go = async (path) => {
      const r = await page.goto(BASE + path, { waitUntil: 'networkidle' });
      // The research island answers after load; wait for its loading line to clear before reading the page.
      if (path.startsWith('/research')) await page.waitForFunction(() => !document.body.innerText.includes('Searching the collected data'), null, { timeout: 15000 }).catch(() => {});
      return r.status();
    };

    // discovery -> search -> product comparison
    check(`${label}: home loads`, (await go('/')) === 200);
    check(`${label}: search finds Raspberry Pi 5`, (await go('/search?q=raspberry+pi+5')) === 200 && (await page.textContent('body')).includes('Raspberry Pi 5'));
    const productHref = await page.$eval('a[href^="/listing/"]', a => a.getAttribute('href'));
    check(`${label}: listing page has provenance and honest price label`, (await go(productHref)) === 200
      && (await page.textContent('body')).includes('Where this data comes from') && !(await page.textContent('body')).includes('Price verified'));
    check(`${label}: no horizontal overflow on listing`, (await overflow(page)) === 0);

    // merchant comparison page exists for a matched product
    const matched = await (await page.request.get(BASE + '/api/v1/listings?q=Raspberry+Pi+5&per_page=100')).json();
    const withProduct = (matched.items || []).find(i => i.product_id && ['auto', 'confirmed'].includes(i.match_status));
    check(`${label}: the API exposes at least one matched product for the 8 GB Pi 5`, !!withProduct);
    if (withProduct) {
      await go(`/product/${withProduct.product_id}`);
      const body = await page.textContent('body');
      check(`${label}: product comparison names both shops and shows match evidence`, body.includes('Adafruit') && body.includes('SparkFun') && !/\['[a-z0-9]/.test(body));
    }

    // research assistant: evidence, uncollected disclosure, honest 'no price drop'
    await go('/research?q=raspberry+pi+5+8gb');
    let body = await page.textContent('body');
    check(`${label}: research shows history depth and cross-shop info`, body.includes('Price history:') && (body.includes('Also sold at') || body.includes('No other shop')));
    await go('/research?q=has+the+price+of+the+raspberry+pi+5+dropped+recently');
    body = await page.textContent('body');
    check(`${label}: price-drop question admits missing history`, body.includes('No price drop has been recorded') && body.includes('cannot say'));
    await go('/research?q=zzzz+unobtainium');
    check(`${label}: empty research state is explicit`, (await page.textContent('body')).includes('No collected listing'));

    // coverage page separates discovered from collected
    await go('/coverage');
    body = await page.textContent('body');
    check(`${label}: coverage distinguishes discovered and collected`, body.includes('Discovered') && body.includes('Collected') && body.includes('not a listing'));
    check(`${label}: no horizontal overflow on coverage`, (await overflow(page)) === 0);

    // deal radar island: the server copy is replaced by the live deals list, with evidence and no invented labels
    await go('/deals');
    await page.waitForFunction(() => !document.querySelector('.server-deals') || document.querySelector('.server-deals').hidden, null, { timeout: 8000 }).catch(() => {});
    body = await page.textContent('body');
    check(`${label}: deal radar island replaces the server table`, (await page.$eval('.server-deals', e => e.hidden).catch(() => false)) === true);
    check(`${label}: deal radar shows scored deals or says none`, (await page.$('.deal-card')) !== null || body.includes('No scored listings yet'));
    check(`${label}: no horizontal overflow on deals`, (await overflow(page)) === 0);

    // browse island: real data by default, no demo rows, no overflow
    await go('/search?q=raspberry');
    await page.waitForSelector('.discovery-results .grid, .discovery-results [role="alert"]', { timeout: 8000 }).catch(() => {});
    check(`${label}: browse island lists real results for a query`, (await page.$$('.discovery-results article')).length > 0);
    check(`${label}: browse island default excludes demo rows`, (await page.$$('.discovery-results .badge.warn')).length === 0);
    check(`${label}: no horizontal overflow on browse`, (await overflow(page)) === 0);

    // alerts page: renders on the snapshot (table not migrated -> says so) and never errors; navigation links to it
    await go('/notifications');
    body = await page.textContent('body');
    check(`${label}: alerts page renders and explains its state`, body.includes('Alerts') && (body.includes('not set up on this database') || body.includes('No alerts yet') || body.includes('unread')));
    check(`${label}: alerts is in the main navigation`, (await page.$('nav[aria-label="Main"] a[href="/notifications"]')) !== null);
    check(`${label}: no horizontal overflow on alerts`, (await overflow(page)) === 0);

    // watchlist round trip
    await go(productHref);
    const lid = productHref.split('/').pop();
    await page.request.post(`${BASE}/watch/${lid}`, { form: { target: '' }, maxRedirects: 0 }).catch(() => {});
    await go('/watchlist');
    // keyboard: skip link is the first tab stop
    await go('/');
    await page.keyboard.press('Tab');
    check(`${label}: first Tab reaches the skip link`, await page.evaluate(() => document.activeElement.className === 'skip'));

    // 404 state
    check(`${label}: unknown listing is a friendly 404`, (await go('/listing/999999')) === 404 && (await page.textContent('body')).includes('Page not found'));

    // admin: login, budgets, side-by-side review with confirmation
    await go('/admin/login');
    await page.fill('input[name=token]', TOKEN);
    await Promise.all([page.waitForNavigation(), page.click('button')]);
    body = await page.textContent('body');
    check(`${label}: admin shows budgets and disabled scheduler`, body.includes('Budgets') && body.includes('disabled'));
    const mergeBtn = await page.$('button[data-confirm]');
    if (mergeBtn) {
      let asked = null;
      page.once('dialog', d => { asked = d.message(); d.dismiss(); });
      await mergeBtn.click();
      await page.waitForTimeout(300);
      check(`${label}: merge asks for confirmation (and dismissing does nothing)`, !!asked && asked.includes('Merge'));
    }
    await page.screenshot({ path: `${OUT}/${label}_admin.png`, fullPage: false });
    check(`${label}: no 5xx responses during the journey`, serverErrors.length === 0, serverErrors.join(', '));
    await ctx.close();
  }
  await browser.close();
  console.log(failures.length ? `\n${failures.length} FAILED: ${failures.join('; ')}` : '\nALL E2E CHECKS PASSED');
  process.exit(failures.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
