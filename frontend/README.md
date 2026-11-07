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

## Not built yet (placeholders that say "not built yet")
Each needs backend work first. None is faked with fixtures.

| Interface | Needed backend | Why it is blocked today |
|---|---|---|
| Product discovery (filters, sort, pagination) | none: `GET /api/v1/listings` already supports them | could be built next; placeholder remains on `/search` |
| Deals intelligence | none for the list (`GET /api/v1/deals`); evidence per deal is already in the payload | could be built next |
| Watchlists (create/manage, changes) | JSON endpoints: list/add/remove watch items and their triggered reasons (`monitor.evaluate` exists server-side) | watch actions are HTML form POSTs with redirects |
| Product matching review (approve/reject/defer) | JSON endpoints: list pending candidates with both sides' attributes; POST decision with CSRF/Origin protection | admin review is an HTML form (`/admin/match/...`) |
| Admin settings / source configuration | JSON endpoints behind the admin token; policy and budget read model | admin is HTML and token-gated |
| Merchant comparison as its own page | none for data (`GET /api/v1/listings?...` + product grouping); needs a product endpoint | product page is server-rendered and already works |

## Design decisions
- **No React.** The brief lists React; the existing app is server-rendered and the islands are small. Adding React would
  add a runtime dependency to every page for no current need. Revisit if a page needs client state that islands cannot hold.
- **Islands, not a SPA.** The security model (server-side escaping, Origin checks, signed admin cookie, same-site forms)
  stays as it was reviewed; the client only adds interactivity on top.
- **Dependency pins.** Vite 6.4.5 and TypeScript 5.9.3 are the newest releases at least two weeks old at pinning time.
  Vitest 4.1.12 clears three critical advisories in Vitest 3.x. `--legacy-peer-deps` was needed to install vitest 4
  because npm 10 fails on an optional canvas peer this project does not use.
