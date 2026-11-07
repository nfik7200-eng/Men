# Acquisition intelligence (mission 005)

**Problem.** Discovery is cheap (a sitemap/category page); fetching is the rate-limited step. Adafruit and SparkFun run with a
per-run page budget (60 / 40, 60 % reserved for re-checks of known listings), so most discovered URLs wait. On the verified
snapshot (sha256 `7f9419c3…`): Adafruit 195 crawl rows linked to 192 listings of 1,169 discovered URLs; SparkFun 90 of 357.
The legacy order was (adapter tier, alphabetical URL): nothing used what another merchant already sells.

**Design** (`watchtower/acquisition.py`). Never-fetched URLs are scored; the best fill the discovery slots:

| component | points | evidence |
|---|---|---|
| tier | 40 / 20 / 5 | adapter hint of centrality (core boards, same-brand accessories, rest) |
| cross_merchant | 40 exact, 28 strong, 10 family | the URL's *name* (slug or listing-card text) vs active listings of other live merchants, after the matcher's hard vetoes (`matching.title_conflict`: variant, product type, kit/bundle, generation, colour, units). Strong = one extra real word; a differing number or a 1–2 letter suffix is family only |
| new_coverage | 10 | no collected listing here already has that name |
| retry | 5 | previously failed, never acquired, backoff elapsed; rejected after `MAX_FAILS` = 5 |
| aging | +3 per lost run, cap 15 | bounded starvation guard (lower tiers wait for the finite higher-tier pool) |

Name hints are **priority-only**: they never write `product_id`/`match_status` and never create review candidates; matching
still happens on the fetched page's real data. No affiliate data is read. Ties: score then URL, so the order is reproducible.
Every decision is stored per `crawl_urls` row: `selection_state` (selected/deferred/rejected/skipped), `selection_reason`
(`budget: rank 3 of 267 …`, `not_due`, `gone`, `disallowed`, `retry_limit`, `not_in_discovery`), `priority_score`,
`priority_reasons`, `defer_count`, `name_hint`, `last_considered_at`, and what happened when tried: `last_outcome`
(ok, not_modified, no_product, parse_error, ingest_invalid, alias, disallowed, fetch_error, gone, blocked).

**Reliability.** `FetchDisallowed` (one URL: robots/allowlist/scheme/IP) is recorded and skipped (7-day re-check); a server
401/403/451 still stops the whole run. Budgets: `WATCHTOWER_BUDGET_<KEY>` (positive int, ceiling 500, else the adapter
default); `--max-pages` is capped too. Pacing stays per host, one request at a time, ≥ 2 s and the site's Crawl-delay.
`python -m watchtower.cli plan --db COPY --source K [--explain URL]` shows the next selection **offline** (no network).

**Coverage** (`watchtower/coverage.py`, `cli coverage --db COPY`, `/coverage`, `/api/v1/coverage`): discovered, rejected
(gone/disallowed/retry_limit), duplicates (several URLs of one listing count once), non-product pages, eligible =
discovered − those, acquired, fetched_ok, `eligible_coverage = acquired / eligible`, `extraction_success_rate = acquired /
fetched_ok`, freshness, URLs waiting that already have a counterpart elsewhere. A discovered URL is never an acquired product.

**Append-only history.** An ORM guard (`Database` `before_flush`) refuses to update or delete an `Observation`. A DB-level
trigger would be a migration on the live database and is deliberately not applied.

**Known limits.** Adafruit's sitemap/category links carry no names until a discovery run stores the card text, so offline
planning cannot rank Adafruit's 974 unfetched URLs by counterpart. Live collection is blocked until a real contact address
is configured (the policy gate refuses a placeholder); the numbers in PROJECT_PROGRESS are therefore *planned*, not fetched.
