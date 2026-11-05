# Project progress — 2026-11-05 (single focused day)

## Acceptance criteria — evidence
| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | App starts | ✅ | `uvicorn main:app` served all pages; `/health` ok |
| 2 | Real permitted source collected | ✅ (sandbox sources) | 2 live runs: 66 + 120 listings, 0 invalid, robots honoured |
| 3 | Products persisted | ✅ | 186 listings in SQLite, idempotent re-run created 0 duplicates |
| 4 | Timestamped observations | ✅ | 372 observations after 2 runs |
| 5 | Comparisons | ⚠️ partly | Same-product-elsewhere view + matching proven by tests; **no live cross-store overlap** (sandbox catalogues don't overlap) |
| 6 | Product details | ✅ | /listing/{id}, screenshots reviewed desktop + Pixel 7 |
| 7 | History shown when sufficient | ✅ | chart appears at ≥2 obs; real listing #18 has 2 obs |
| 8 | Price change detection | ⚠️ tests only | drop/rise/restock/removed/description events tested; live data had **0 changes** (static sandbox) |
| 9 | Assistant uses real data | ✅ | answers cite listing ids; budget/gap/no-result behaviours tested |
| 10 | Desktop + Android layout | ✅ | 0 px horizontal overflow on 5 pages × 2 viewports; screenshots inspected (home/search/deals Android not individually eyeballed) |
| 11 | Tests | ✅ | 46 pass (also from a clean venv) |
| 12 | Reproducible | ✅ | README quick start run in clean venv |
| 13 | Roadmap | ✅ | docs/COMMERCIAL_ROADMAP.md |

## Incomplete / not verified (be skeptical of anything not listed as ✅)
- No real retailer source; no live verified deals (needs ≥7 days of history). Deal labels today are "estimate" only.
- Ollama integration untested against a real model (not installed); mocked-transport test only.
- Affiliate programme facts come from third-party blogs, not official terms; no programme joined.
- Import-cost rates are placeholders (`verified:false`). No FX conversion.
- No accounts, alert delivery, billing, Alembic, business-monitoring UI. Admin POST/watchlist have no CSRF protection.
- Not production-ready. No security review beyond own code reading + XSS/admin tests.

## Model checkpoints (honest)
This session ran on a single model; **no Opus advisor review occurred**. Recommended manual Opus checkpoints: (1) review `matching.py` thresholds
and `deals.py` scoring before trusting live deals, (2) security review before any public deploy, (3) business-model review once real data exists.
Plugins: Playwright (via local Chromium script) used for screenshots; Context7/GitHub-MCP failed to connect (proxy); frontend-design/superpowers skills not otherwise used.
