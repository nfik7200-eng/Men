# Technical decisions (date · decision · why · revisit when)
- 2026-11-05 · HTTPX+BeautifulSoup, no Scrapy/Playwright · sources are static HTML, one process, fewer moving parts · a source needs JS or >20 sources.
- 2026-11-05 · SQLite + SQLAlchemy 2, `create_all` (no Alembic yet) · $0, schema still moving · first deployment with data worth keeping → add Alembic + Postgres.
- 2026-11-05 · Server-rendered Jinja UI instead of Next.js · no Node toolchain, tested with TestClient, fast on Android · need rich client interactivity/auth flows.
- 2026-11-05 · Integer minor units, NULL = unavailable · prevents "unavailable == free" bugs.
- 2026-11-05 · Own matching (identifiers → model codes + spec agreement → review queue) instead of embeddings/LLM · deterministic, explainable; LLM only if review queue grows.
- 2026-11-05 · Assistant = retrieval + rules; Ollama optional & UNTESTED against a real model (not installed) · must work with no LLM.
- 2026-11-05 · Sandbox sources only for MVP demo: only egress-permitted, explicitly scrape-friendly sites available; flagged `sandbox` everywhere.
- 2026-11-05 · Egress allowlist for this environment extended (user-approved) with webscraper.io, books.toscrape.com.

## Mission 002 (2026-11-06)
- **Deal evidence** · a "verified drop" requires the current price to be *below every earlier price*, a reference ("usual") price held ≥3 days (time-weighted mode, not the median), ≥3 distinct collections over ≥7 days; observations <6 h apart count once · a median over few points let a spike-then-normal sequence score 80/"verified" (F2/F3) · tune when real data exists.
- **Matching vetoes** · variant words, condition, bundle/pack, colour and generation differences veto auto-merge; suffix/region variants → review; MPN needs ≥5 chars + same brand; GTIN ≥8 digits · false merges corrupt price history, which is worse than missed merges.
- **Colour variants are separate products** · different SKUs/prices; revisit if real data shows retailers list one price across colours.
- **Admin fails closed** · no token (or <20 chars) ⇒ admin disabled; login sets an HttpOnly SameSite=Strict cookie holding an HMAC-derived value; token never in URLs · loopback-trust is spoofable behind proxies (F6).
- **CSRF via Origin/Referer check + SameSite** rather than form tokens · all state-changing routes are same-site form posts; simpler and testable. Revisit if cross-origin API clients with cookies are added.
- **Per-browser anonymous watchlist (random cookie)** until real accounts exist · a shared "local" owner was a public data-exposure bug.
- **Fetcher is SSRF-safe by allowlist, not by DNS inspection** · hosts must be on the source's list; private IP literals refused; redirects re-validated per hop. Robots is skipped only for authenticated official APIs (their agreement governs).
- **Forward-only column sync (`Database.sync_columns`)** instead of Alembic · adds missing NULLABLE columns only; verified on a copy of the real DB (186 listings/372 observations preserved). Adopt Alembic before Postgres or any destructive change.
- **Run integrity** · exceptions fail the run; zero-item start URL ⇒ incomplete; mass-removal guard (>50 % and >5 listings) ⇒ nothing removed; `Source.enabled` honoured; one live run per source (DB-level; stale after 45 min).
- **API sources** · adapters declare `official_api`, `configured()`, `request_headers()`; unconfigured sources raise `SourceNotConfigured` and fetch nothing; sandbox env is the default and is flagged `sandbox` in the UI.
- **eBay first (conditionally)** · see `DATA_SOURCE_RESEARCH.md`; availability stored as `unknown` because search summaries do not state stock.
- **FX only from recorded, attributed rates** · no live fetching, no triangulation; stale rates flagged. **Landed cost** separates item/shipping/insurance/duty/VAT/handling with evidence labels; a rule is "verified" only with `verified` + `source` + `retrieved`.
- **Assistant** · budget needs a money cue; currency inferred only when unambiguous (else it asks); brands are hard filters, other words soft and disclosed when unmatched; LLM output rejected if it contains numbers absent from the retrieved facts.

## Mission 003 (2026-11-06) — free-source scraping engine
Decisions marked **[Opus consult]** adopted advice from the architecture consult (a read-only subagent launched with `model: opus`; NOT the built-in Advisor feature, which is not active in this session).
- **Scrape-first, no paid APIs** · real merchants via sitemap/category discovery + schema.org JSON-LD over plain HTTP; Playwright cut **[Opus]**, microdata postponed. eBay stays an optional, unconfigured scaffold.
- **Compliance is enforced in code, not in docs** · `watchtower/policy.py`: non-demo sources need an `approved` policy, a current review (90 days), robots.txt still allowing every required path class (re-checked every run), and a real `WATCHTOWER_CONTACT`. Policies live in `data/source_policies.json` (versioned evidence: robots hash, ToS hash, finding, reviewer). A runtime auto-rejection gets a newer timestamp, so the file can never silently re-approve it. **"approved" = "no prohibition found", not legal permission** **[Opus]**.
- **robots.txt is necessary, not sufficient** · The Pi Hut is rejected although robots allows crawling, because its terms forbid scraping. Cool Components is unverified because its `agents.md` demands Shopify Web Bot Auth identification we cannot provide.
- **Facts only: no descriptions, no images** · copyright; we store name/price/currency/availability/identifiers + a description hash for change detection **[Opus]**.
- **Incremental crawl state per URL (`crawl_urls`)** · conditional GET; an HTTP 304 still writes a `not_modified` observation (otherwise removals, staleness and deal history break) **[Opus]**; due-ordering by discovery priority then staleness.
- **Sampling is never "complete"** · product-page crawls never infer removal from absence; a listing is removed only when its own URL answers 404/410 **[Opus]**. Listing-page crawls keep the complete-run + removal-guard rules.
- **Crawl-delay is honoured** (SparkFun: 10 s) and stored with the policy **[Opus]**.
- **Identifier hygiene** · GTINs are check-digit validated, zero-padded to 14, restricted/in-store prefixes rejected; the merchant SKU is never an identifier; an `mpn` equal to the SKU (SparkFun) is discarded **[Opus]**.
- **Staged matching** · GTIN → MPN(+brand) → brand + normalised **name signature** → specs → fuzzy (supporting only). Hard-conflict vetoes (variant words, condition, bundle, colour, generation, RAM/storage) run at *every* stage including identifier matches, because merchants copy identifiers across variants **[Opus]**. Differing bare numbers in a name (Zero W vs Zero 2 W, Pi 4 vs Pi 5) veto; a model code on one side only → review. Brand compared canonically (`Raspberry Pi Ltd` = `Raspberry Pi`); category vetoes ignore the generic `maker-electronics` class **[Opus]**.
- **Data classes** · `Source.kind` ∈ demo | sandbox | live, default **demo** (fail closed) **[Opus]**. Deal ranking, peer medians, default search and the assistant use live data only once any exists; demo/sandbox are opt-in views and always badged.
- **Observation provenance** · extraction method, offer-tuple hash (not raw HTML, which carries nonces) and HTTP status on every observation; run metrics (duration, bytes, pages, unchanged, failed, extraction counts).
- **No cross-currency ranking** · merchants in different currencies are shown side by side; "lowest" is per currency.
- **Interim migrations** · `sync_columns` now also creates indexes declared with `index=True` (it did not before) **[Opus]**; verified on a copy of the real DB.
- **Postponed** · Open Food Facts (ODbL share-alike, not our niche), stored volatility series (computed on read), cross-domain parallel crawling and a single-writer queue (3–5 merchants run sequentially) **[Opus]**.

## Mission 003 — second independent review + defects found on real data
- **Own RFC 9309 robots parser** (`watchtower/robots.py`) · the stdlib parser's blank-line/wildcard handling varies by Python version, which would silently disable the compliance re-check on 3.11/3.12 (we claim 3.11+; verified only on 3.13 here). Tested against the real Adafruit/SparkFun/Pi Hut files **[Opus review]**.
- **Policy gate hardening** · a policy entry without `reviewed_at` is `unverified` (cannot "never expire"); placeholder contacts (`example.com`, `localhost`, …) are refused; a transient robots 5xx is retried and, if persistent, **stops the run without changing the stored policy** (not a prohibition); a robots change that still allows our paths keeps running but shortens the review fuse to 7 days and is recorded as a decision newer than the file; live sources have a 2 s delay floor **[Opus review]**.
- **Matcher: units are identity** · capacities, volts, amps, watts, GHz, mAh, inches and bare version numbers are compared (`32GB` ≠ `64GB`, `3A` ≠ `5A`, `Camera 1.3` ≠ `2.1`); hardware options (WiFi/wireless/headers/PoE/no/non) are variant words; third-party accessories ("Case for Raspberry Pi 4") are not branded as the platform; fuzzy similarity can never auto-merge without a known equal brand; one-word names with no agreeing spec go to review **[Opus review; 9 false merges reproduced]**.
- **Matcher: structural invariants** · a product holds at most one listing per merchant; equally good candidates are ambiguous → review; a new listing must not conflict with any existing member of the target product; merchants listing several identically named items (same name **and** same unit values) cannot hold auto-matches (`demote_ambiguous`); human rejections are never re-queued; deleting a product first closes candidates pointing at it (an accept used to break the foreign key) **[Opus review]**.
- **Deal peers = same product elsewhere** · found on the real home page (a USB cable "below typical price" against boards). Broad buckets (`maker-electronics`) have no category peers; a single other merchant is valid but weaker evidence (points capped at 25).
- **Facts-only also applies to the repo** · committed fixtures are reduced captures (descriptions/images removed) **[Opus review]**.
- **Real defect: SparkFun stock came from a related-products card** · the first `div.stock` on the page belongs to another product; all 62 listings had the wrong availability (`backorder`). Fixed by reading only `div.product-info-main div.product-info-stock-sku div.stock`, `unknown` if absent; the fixture now preserves the real layout (related card first); wrong stored values were reset to `unknown` and re-collected. Lesson: extraction must be validated against real pages, and plausibility checks (0 of 62 in stock) are worth running after every first collection.
- **Crawl budget split** · re-checks of known listings get 60% of each run's budget (price history is the asset), discovery the rest.


## Mission 004 — loop engineering (2026-11-06)
- **Backups use SQLite's online backup API** (`watchtower.cli backup`), never a file copy: the DB runs in WAL mode and a
  copy of `watchtower.db` alone lost 28 listings / 70 observations / 1 run on the real data. Snapshots are verified
  (integrity_check + per-table counts) and never overwrite.
- **`watchtower.cli verify`** is the shared measurement for every loop; it opens the DB read-only. Invariants are
  hard (expected 0); metrics are baselines.
- **Review queue: one question per listing pair.** A decision applies to its mirror; confirmed/auto listings are never
  downgraded by an unrelated rejection.
- **Assistant relevance is unit-normalised** ('8GB' == '8 GB'; digit tokens match whole values); parsed spec
  requirements are minimums, not keywords. **A named model is not answered with a different product class** (maker
  electronics only, only when the user names no product type, always with a caveat listing what was left out).
- Experiments run only on snapshot copies; the real DB was not modified in mission 004 (sha256 checked before/after).


## Mission 005 — acquisition intelligence (2026-11-07)
- **Prioritise, don't just raise budgets.** Never-fetched URLs are ranked by an explainable score (docs/ACQUISITION.md); decisions are stored on
  `crawl_urls`. Re-checks keep 60 % of the budget (price history is the asset); no global cross-merchant allocator is built: runs are
  per source with their own budget, which is already fair; `schedule run-due` orders sources by longest wait.
- **Name hints are priority-only evidence** (never write product_id/match_status, never queue candidates) and go through the matcher's hard vetoes.
- **FetchDisallowed (per URL) vs FetchBlocked (server refusal)**: only the latter stops a run (CLAUDE.md STOP rule).
- **Retry limit = cooldown** (flat 7 days after 5 failures), never permanent, never unbounded.
- **Append-only observations via an ORM guard**, not a trigger (a trigger would migrate the live DB; bulk SQL bypasses the guard: documented limit).
- **Scheduler**: library + CLI only, disabled by default, double opt-in, policy gate unchanged, no installation.
- **Coverage** metrics deliberately separate discovered / eligible / fetched_ok / acquired; several URLs of one listing count once.
- New nullable `crawl_urls` columns are added by the forward-only `sync_columns` the first time the DB is opened normally: take a `backup` first.
