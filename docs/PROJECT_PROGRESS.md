# Project progress — updated 2026-11-06 (mission 002)

## Verified now (commands run in this session)
| Claim | Evidence |
|---|---|
| Tests | `python -m pytest` → **143 passed** (was 46). 97 new tests are regression tests for audit findings, eBay adapter (mocked), landed cost/FX, affiliate |
| Previous claims re-verified | 186 listings / 372 observations existed; PR #3 draft/open, no checks/reviews |
| Real collection on migrated DB | 3rd run (2026-11-06): 66 + 120 items, 0 invalid, 0 new; **558 observations**, every listing has history on 2 distinct days; eBay skipped as "not configured; missing EBAY_CLIENT_ID, EBAY_CLIENT_SECRET" |
| Schema migration | `sync_columns` added 5 listing columns to a copy of the real DB with data intact |
| UI | Screenshots inspected at 1280×900 and Pixel 7 (home, search, listing+chart+landed cost, research incl. ambiguity, deals, watchlist, 404/422 pages, login, admin); 0 px overflow; real-browser watch + admin login flows work (CSRF check does not block genuine browsers) |
| Performance | `top_deals` @5,000 listings: 7.9 s/5,002 queries (audit) → **0.26 s/3 queries** |

## Not true / not done (read before relying on anything)
* **The eBay integration does not work against eBay.** It is a contract + scaffolding validated with mocked transport and synthetic fixtures. Activation blockers: no credentials, no approval, environment allowlist lacks `api.sandbox.ebay.com`/`api.ebay.com`, and **the eBay API License Agreement (data storage/price history rights) could not be read (HTTP 403)**.
* No real retailer data; deal labels are "estimate" only — no verified deal can exist before ≥3 collections spanning ≥7 days (nothing here has that yet).
* Affiliate: nothing joined/enabled; EPN country eligibility and disclosure wording unverified; Amazon needs ≥10 sales/30 days (primary source).
* Landed-cost rates are placeholders (marked "assumed"); no sourced FX rate is recorded (the UI says so).
* Ollama still untested with a real model. No Context7 / GitHub-MCP (proxy 403) — not used.
* Security: see `AUDIT_REPORT.md` §5 open items. Not production-ready.
* PR #3 remains **draft**; not marked ready, not merged.

## Model usage (honest)
Independent audit: a read-only subagent launched with `model: opus` (I cannot independently verify the serving model). Everything else (implementation, tests, docs, research) was done by this Sonnet session. Recommended manual Opus checkpoints still open: matching/deal threshold review on *real* data; formal security review before any public deploy; business-model review after gate 1.

## Next milestone
Resolve gates 1–2 (read the eBay license; sandbox keys + allowlist), run the adapter against the eBay sandbox, fix parser mismatches, then start daily collection.
