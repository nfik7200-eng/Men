# Frontend UX audit (automated, desktop 1280x900 and Pixel 7)

Method: a Playwright script visited 16 pages at both widths (32 page views) on a disposable copy of the snapshot, with
the rate limit off for that copy. Checks: HTTP status, one top-level heading, `lang`, images without alt text, form
controls without a name, buttons and links without a name, horizontal overflow, interactive targets smaller than
24x24 CSS px (WCAG 2.2 criterion 2.5.8), and console errors. Screenshots of search, listing, home, alerts and the
price-limit notice were inspected by eye.

## Findings and fixes
| Finding | Where | Fix |
|---|---|---|
| A price limit without a currency reached the browse island, the API refused it (422), and the island showed an error | `/search?max_price=` | The island drops the limit and says so (same rule as the server). Test added. |
| No currency control in the browse island, so a limit could not be scoped | `/search` | A currency select lists the currencies found in the results. Tests added. |
| "In stock only" checkbox was about 100 px wide and centred, away from its label | `/search` on phones | A global `input { width:100% }` rule was overridden for this form only; the box is 24 px square. |
| Undersized targets: "← Browse" back links, "Compare merchants" links, "See how each score is built", card title links | listing, product, home, search | Links meet 24 px minimum height. |

## Expected console errors (not defects)
The 404 pages return HTTP 404 on purpose, so the browser logs a failed resource.

## Contrast, measured (light and dark themes)
A measurement script computed WCAG contrast for every visible text element on nine pages (about 520 elements per theme),
using the element's computed colour against the nearest opaque background, at the AA thresholds (4.5 normal, 3 large).
Light and dark themes: 0 failing elements. The dark theme is defined by `prefers-color-scheme: dark` in `style.css`.
This is an automated approximation: it does not model text over images or gradients, and it is not an axe audit.

## Not measured by this audit
- Keyboard focus order beyond the skip link (checked earlier by the e2e journeys only).
- Screen-reader output (no screen reader in this environment).
- Loading states are checked only where the page renders them (island placeholders); empty and error states are covered by tests.

## Automated accessibility check (axe-core, measured)

Tool: axe-core 4.10.2 (default rule set: WCAG 2.x A/AA and best-practice rules), run by `e2e/axe-check.js` in
Chromium against a migrated disposable copy of the snapshot, at desktop (1280×900) and phone (Pixel 7) widths.
Pages: home, search, listing, product, deals, coverage, research, watchlist, alerts, admin sign-in and admin
(signed in). 22 page/width combinations.

Before this pass: 1 serious finding (`scrollable-region-focusable`: the phone-width table scroll containers could not be
reached by keyboard) and 3 minor findings (`empty-table-header` on the home and admin tables;
`aria-allowed-role`: `role="listitem"` on `<article>` in the browse and deal lists).

After this pass: 0 violations on all 22 combinations. Fixes: the scroll containers are focusable (`tabindex="0"`);
the empty table headers are labelled ("Compare", "Actions"); the browse and deal grids no longer claim list roles
their children do not have.

Limits, stated plainly: axe finds about a third to a half of accessibility problems. It does not test screen-reader
announcements, focus order across the islands, reading order, colour-only meaning under user preferences, or
behaviour with zoom at 200%. Those remain manual checks and are not measured here.

A second run blocks the island bundle (`NO_ISLANDS=1`) to audit the server-rendered fallback pages, which the
islands replace when they run. That run found one moderate finding (heading order on search results: an `h3`
directly under the page `h1`); the card heading level is now a macro parameter, and search uses level 2. Both
modes: 0 violations on 22 page/width combinations after the fix. The deal-radar fallback table had been missed
by the first pass (a keyboard user at phone width could not scroll it); it is now focusable too.

Reproduce (axe-core is not a project dependency; install it outside the repo):

```bash
npm i axe-core@4.10.2 --prefix /path/to/scratch
AXE_PATH=/path/to/scratch/node_modules/axe-core/axe.min.js E2E_BASE=http://127.0.0.1:8042 \
  E2E_ADMIN_TOKEN=... node e2e/axe-check.js
```

The check injects axe into a test context with `bypassCSP`. The application's Content-Security-Policy is unchanged; the
policy correctly blocks inline script in the page itself.
