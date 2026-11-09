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
