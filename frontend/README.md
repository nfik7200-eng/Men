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
| `watchlist` | `/watchlist` | `GET`/`POST`/`DELETE /api/v1/watchlist` | owner-cookie scoped; targets in major units of the listing's currency; a failed save says so |
| `match-review` | `/admin` | `GET /api/v1/match-candidates`, `POST .../decision` | admin token or session; a merge asks first; signed-out visitors are told to sign in |
| `admin-settings` | `/admin` (beside the server table) | `GET /api/v1/admin/settings`, `POST /api/v1/admin/sources/{key}/enabled` | strict boolean switch; the state shown is the one the server stored; scheduled collection is read-only; the server table keeps the collection actions |

The JSON listings endpoint takes `data`. Omitting it keeps the old behaviour (every source), so existing API callers
are unchanged. The island always sends it, so a real-data view never shows demo or sandbox rows.

## Not built yet (placeholders that say "not built yet")
Each needs backend work first. None is faked with fixtures.

| Interface | Needed backend | Why it is blocked today |
|---|---|---|
| Merchant comparison as its own page | none for data (`GET /api/v1/listings?...` + product grouping); needs a product endpoint | not built on purpose: the product page is server-rendered, populated-state tested, and already works |
| Admin source status panel (read-only) | `GET /api/v1/admin/status` exists (sources, recent runs, review backlog) | not built on purpose: the server table on `/admin` already shows the same facts; add an island only if the page needs live refresh |

## Design decisions
- **No React.** The brief lists React; the existing app is server-rendered and the islands are small. Adding React would
  add a runtime dependency to every page for no current need. Revisit if a page needs client state that islands cannot hold.
- **Islands, not a SPA.** The security model (server-side escaping, Origin checks, signed admin cookie, same-site forms)
  stays as it was reviewed; the client only adds interactivity on top.
- **Dependency pins.** Vite 6.4.5 and TypeScript 5.9.3 are the newest releases at least two weeks old at pinning time.
  Vitest 4.1.12 clears three critical advisories in Vitest 3.x. `--legacy-peer-deps` was needed to install vitest 4
  because npm 10 fails on an optional canvas peer this project does not use.
