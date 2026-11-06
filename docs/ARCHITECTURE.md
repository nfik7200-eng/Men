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
