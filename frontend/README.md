# Watchtower client (frontend)

Vite + TypeScript islands that enhance the server-rendered Watchtower pages. They are built into
`shopping/web/static/app/` and loaded only on pages that mark an island with `data-island="<name>"`.
The server pages stay complete and usable without JavaScript.

## Commands
- `npm install` — install dev tooling (there are **no runtime dependencies**; the client ships as one module)
- `npm run typecheck` — strict TypeScript (`strict`, `noUncheckedIndexedAccess`)
- `npm test` — unit tests (Vitest, jsdom), then a build, then the stale-bundle guard
- `npm run build` — writes the bundle the FastAPI app serves at `/static/app/assets/main.js`
- `npm run dev` — Vite dev server with `/api` proxied to `127.0.0.1:8000` (run the app there first)

## Built islands (verified in a browser, desktop and Pixel 7, against live data)
| Island | Page | Reads | Honest behaviour |
|---|---|---|---|
| `listing-chart` | `/listing/<id>` | `GET /api/v1/listings/<id>/history` | draws only real priced observations; states flat prices; no line for one collection |
| `research` | `/research` | `GET /api/v1/research?q=` | answers from the backend; each pick links its exact listing; unconfirmed matches labelled |
| `coverage` | `/coverage` | `GET /api/v1/coverage` | discovered ≠ collected; unknown rates shown as unknown, never 0% |
| `deals` | `/deals` | `GET /api/v1/deals` | server score, label and evidence shown as sent; a missing price is "Price not stated"; unscored is "Not scored" |
| `discovery` | `/search` | `GET /api/v1/listings` with `data=real\|demo\|all` | real retailers by default; demo rows labelled and shown only when asked; URL holds the search state; prices never converted |

The JSON listings endpoint takes `data`. Omitting it keeps the old behaviour (every source), so existing API callers
are unchanged. The island always sends it, so a real-data view never shows demo or sandbox rows.

## Not built yet (placeholders that say "not built yet")
Each needs backend work first. None is faked with fixtures.

| Interface | Needed backend | Why it is blocked today |
|---|---|---|
| Watchlists (create/manage, changes) | Server page `/watchlist` exists (HTML forms). **JSON endpoints also exist:** `GET /api/v1/watchlist`, `POST /api/v1/watchlist` (`listing_id`, `target_price` in the listing's currency), `DELETE /api/v1/watchlist/{listing_id}`. They need the browser's owner cookie (open any page first; otherwise 401). Origin-checked for writes. | Island not built (server page works) |
| Product matching review (approve/reject) | Server queue on `/admin` exists (HTML forms). **JSON endpoints also exist:** `GET /api/v1/match-candidates`, `POST /api/v1/match-candidates/{id}/decision` (`accept` or `reject`). Admin token required. | Island not built (server page works) |
| Admin settings / source status | Server page `/admin` exists. **JSON endpoints exist:** `GET /api/v1/admin/status` (sources, recent runs, review backlog; read-only) and `GET /api/v1/admin/settings` (sources with enabled flag, configured state and page budget; scheduler reported, never changed). `POST /api/v1/admin/sources/{key}/enabled` with `{"enabled": true\|false}` (strict boolean) switches one source. Admin token or admin session; Origin-checked. | Island not built (server page works). There is no JSON route for the scheduler: it stays disabled unless enabled on the server. |
| Merchant comparison as its own page | none for data (`GET /api/v1/listings?...` + product grouping); needs a product endpoint | product page is server-rendered and already works |

## Design decisions
- **No React.** The brief lists React; the existing app is server-rendered and the islands are small. Adding React would
  add a runtime dependency to every page for no current need. Revisit if a page needs client state that islands cannot hold.
- **Islands, not a SPA.** The security model (server-side escaping, Origin checks, signed admin cookie, same-site forms)
  stays as it was reviewed; the client only adds interactivity on top.
- **Dependency pins.** Vite 6.4.5 and TypeScript 5.9.3 are the newest releases at least two weeks old at pinning time.
  Vitest 4.1.12 clears three critical advisories in Vitest 3.x. `--legacy-peer-deps` was needed to install vitest 4
  because npm 10 fails on an optional canvas peer this project does not use.
