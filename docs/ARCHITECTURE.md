# Architecture

```
 adapters (pure parse)  ->  Fetcher (robots, delay, retry)  ->  collector.run_source
        watchtower/sources            watchtower/fetcher               |
                                                                       v
                    ingest.validate -> upsert Listing -> Observation (append-only) -> Event (change feed)
                                          |                                  \
                                 matching.attach_or_queue            monitor.evaluate (watches)
                                          v
   SQLite (Postgres-ready via SQLAlchemy URL)  <-  deals.score_listing (explainable)  <-  shopping.* / web
```
**Shared engine (`watchtower/`)**: Source, CollectionRun, Product, Listing, Observation, Event, MatchCandidate, WatchItem. Generic: a
competitor-monitoring product (B) is "a Watch on listings of a competitor Source + a report over Events" — no new infrastructure.
**Consumer app (`shopping/`)**: search, assistant (retrieval → rules → optional Ollama), import cost, affiliate links/clicks, plan limits,
server-rendered UI (Jinja) + JSON API `/api/v1/*` (the seed of the future paid API; same service functions).
**Data quality**: price_status verified|stale|missing; deal evidence verified|estimated|unverified|none.
**Scheduling**: CLI `collect` is idempotent; run from cron / systemd timer / GitHub Actions (daily is enough for MVP).
**Business monitoring (not built)**: add `Competitor` Source rows, a `report` module over Events, API keys + rate limits on /api/v1.
**Known limits**: single-writer SQLite; one tenant ("local" owner); no auth; admin is loopback-only or token-protected.

## Mission 002 additions
* `shopping/web/security.py`: fail-closed admin auth, Origin-based CSRF check, per-browser owner cookie, rate limiting, security headers (installed as one middleware).
* API-source contract on `SourceAdapter`: `official_api`, `configured()`, `request_headers()`, `allowed_hosts()`, `listing_hosts()`; the collector refuses unconfigured/disabled/busy sources and fails runs on exceptions.
* `watchtower/sources/ebay_browse.py` (scaffold) · `shopping/fx.py` (sourced FX) · `shopping/importcost.py` v2 (landed cost) · `Database.sync_columns` (interim migrations).

## Mission 003 — free-source scraping engine
```
 policy.gate (approved? review current? robots re-checked? real contact?)        data/source_policies.json (versioned evidence)
        │
 adapter.discover ──► crawl_urls (per-URL state: etag, lastmod, next_check_at, gone, content hash)
        │                    │  due ordering: re-checks 60% of budget, then discovery by priority tier
        ▼                    ▼
 Fetcher (allowlist, per-hop robots via watchtower/robots.py, Crawl-delay, ≥2 s live floor, conditional GET, 304)
        │
 adapter.parse_product ──► structured.py (JSON-LD, strict prices, AggregateOffer≠price) ──► ingest_listing / reconfirm
        │                                                                                      │
        └─ facts only (no description/image)                              Observation (+method, offer hash, http status)
                                                                                               ▼
        matching.attach_or_queue (GTIN→MPN→brand+name signature→specs→fuzzy; vetoes at every stage; one listing per
        merchant per product; ties/ambiguity → review) ──► deals (same-product peers, live-only) ──► web (product page)
```
* **Product page (`/product/{id}`)** compares merchants side by side per currency (never converted); every row shows data class, last-checked time, stock, history stats and the matching rationale.
* **Health** (`watchtower/health.py`): success rate, throughput, freshness and state per source; shown in admin with policy status.
* **Where Business monitoring fits**: a competitor is just another `live` source with its own policy; Events/WatchItem/health are reused unchanged.

## Missions 004-005 and the completion programme — current module map (2026-11-07)

```
SOURCE DISCOVERY   data/source_policies.json  ── policy.py (gate: approved + current + robots + real contact)
                   sources/*.py adapters       ── discover(): sitemap / category pages -> SitemapEntry(loc, priority, hint)
ACQUISITION        acquisition.py  rank_fresh / plan_run  (explainable score; decisions stored on crawl_urls)
                   collector.py    _crawl_products (budgeted, per-URL outcomes, two-step 404, STOP on 401/403/451)
                   fetcher.py      allowlist on every hop, robots (own RFC 9309 parser), Crawl-delay, conditional GET
                   schedule.py     disabled-by-default double opt-in scheduler (library + CLI; installs nothing)
EXTRACTION         structured.py (JSON-LD/sitemaps/price parsing), specs.py, identifiers.py
STORAGE            models.py + db.py (SQLite WAL, forward-only sync_columns, append-only Observation guard, snapshot())
                   integrity.py    read-only invariants + metrics (cli verify);  cleanup.py  rematch preview on a temp copy
MATCHING           matching.py     staged scorer + hard vetoes (title_conflict), review queue, audited decisions
                   ml/ (optional)  advisory pair features, evaluation harness, anomaly baseline — NEVER feeds matching/ranking
PRICE INTELLIGENCE deals.py        explainable score; verified drops need 3+ collections over 7+ days; peers must be buyable
                   coverage.py     discovered != eligible != fetched != acquired metrics
ASSISTANT          shopping/assistant.py  deterministic retrieval; discloses uncollected pages, unverified matches, history depth;
                   market questions (new / price drops / history gaps) answered only from stored evidence; LLM optional
FRONTEND           shopping/web/app.py + templates (server-rendered): search, product, listing (+provenance), deals, research,
                   watchlist, /coverage, admin (health, budgets/schedule, side-by-side review, decision log)
OPERATIONS         cli.py: collect status health policy rematch reparse recheck fx source backup verify plan coverage schedule cleanup-preview
                   deploy/ (Contabo package, not installed), docs/DEPLOYMENT.md, docs/SCHEDULING.md, docs/DB_CLEANUP_RUNBOOK.md
```

Dependency rules: `watchtower` never imports `shopping`; `watchtower/ml` is imported by nothing in the engine; model output is advisory only.
Shared-module changes (models, matching, collector, app) go through one integrator; parallel agents own disjoint paths (docs/MASTER_PLAN.md).

## Frontend islands (current)
Server-rendered pages mark an island with `data-island`. Mounted islands: listing-chart, research, coverage, deals, discovery (browse). Each keeps a server fallback that is visible without JavaScript and hidden only once the island runs. Islands read only the JSON API and never construct money or labels the server did not send.
