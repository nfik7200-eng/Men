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
