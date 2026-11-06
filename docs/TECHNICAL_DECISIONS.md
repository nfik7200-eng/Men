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
