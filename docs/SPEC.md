# Improved Specification & One-Day MVP Scope (Stage 1)

Status: written 2026-11-05. Supersedes the raw mission brief where they differ.

## 1. Business, restated

**Watchtower** is a reusable *observe → normalise → remember → explain* engine for public web commerce data.
Two products sit on top; they share the engine but not each other:

| | Product A – Shopping Intelligence | Product B – Business Intelligence |
|---|---|---|
| Buyer | Consumers | Retailers / importers |
| Value | "Is this a real deal? Where is it cheapest? What will it really cost me?" | "What did my competitors change this week?" |
| Day-1 status | **Built (MVP)** | Architecture + data model only (Events/Watch already generic) |

The defensible asset is not the scraper (commodity) — it is the **accumulated, provenance-tagged price history** and the **honest deal/matching logic**. Everything is designed so every figure says *where it came from and when* (verified / estimated / missing / stale).

## 2. Critical assessment of the mission

**Over-engineering to avoid on day one:** Scrapy (HTTPX+BS4 is enough for ≤10 sources and keeps one process), Playwright (no JS-only source needed), Next.js (a build toolchain + second runtime for a data-dense, SEO-friendly read-mostly UI; server-rendered pages are faster to ship and test — revisit when we need rich interactivity), Alembic (schema still moving; `create_all` + documented migration step once the schema stabilises), Postgres (SQLite is fine to ~millions of observations at one writer), ML prediction, billing, payments.

**Missing requirements added:** collector politeness policy + honest User-Agent; per-source legal/terms note stored in DB; data retention (observations are append-only, compact later); admin protection for collection endpoints; currency handling without invented FX rates (no cross-currency comparison until a *sourced* rate feed exists); disclosure labelling for affiliate links; "removed listing" semantics only after a *complete* crawl.

**Hard truths (risks):**
1. **Data access is the business risk, not code.** Big retailers (Amazon etc.) forbid scraping and their official feeds require affiliate approval. A price-comparison product without compliant data is not viable. Compliant routes: official APIs (eBay Browse API, Amazon PA-API — both need approved accounts), affiliate-network product feeds, retailer-granted feeds, Shopify-style public endpoints *per store terms*, and sites that explicitly permit crawling.
2. **History takes calendar time.** "Genuine discount" claims need weeks of observations. Day-one history is necessarily 1 observation per listing. The UI says so instead of faking a chart.
3. **Cross-store overlap.** The two permitted sources available today (below) are *scraping-practice sandboxes* whose catalogues do not overlap. Cross-store matching is therefore proven by tests/fixtures, **not** by live data, until a second overlapping real source is added.
4. **Affiliate approval is not guaranteed** (see COMMERCIAL_ROADMAP).

## 3. Sources used in the MVP (permitted, verified 2026-11-05)

| Source | Why permitted | Currency | Note |
|---|---|---|---|
| `webscraper.io/test-sites/e-commerce/static` (laptops, tablets, phones) | Published site purpose = scraping practice; `robots.txt` disallows only `/test-sites/product/`, `/pagination/`, `/scroll/`, `/load-more/` — we use none of those | USD | **Demo catalogue, not a real shop.** Prices are static. |
| `books.toscrape.com` | Scraping-practice sandbox; no `robots.txt` (404 = no published restrictions) | GBP | **Demo catalogue, not a real shop.** |

Both are flagged `kind=sandbox` in the DB and in the UI. They prove the pipeline; they are *not* evidence of real market prices. The environment's egress allowlist was extended to exactly these two hosts (user-approved prompt).

**First market recommendation:** build source-adapters per *retailer*, not per country. Pick the launch market by the first retailer set that grants compliant data access. Initial leaning: **US/UK electronics via eBay Partner Network + official eBay Browse API** (documented API, affiliate programme, buyers worldwide incl. import-cost use case), with East-Africa import-cost estimation (e.g. Uganda) as the differentiated feature for users who buy abroad. Selection criteria, scored per candidate: data-access legality (×3), data quality/IDs (×2), affiliate programme availability (×2), competition (×1), ops cost (×1). Not decided by us until accounts are approved.

## 4. Revenue priority (what we target first)

1. **Affiliate** — zero price to users, aligns incentives with traffic, needs only traffic + approval. Infrastructure shipped: provider-agnostic link builder + click log + disclosure.
2. Consumer Pro ($5–10 hypothesis) – gate by watchlist size/alert speed (plan limits shipped, no billing).
3. Business monitoring ($29–199 hypothesis) — reuses Watchtower events.
4. API/data. 5. Sponsored (clearly labelled, never affects ranking — ranking code has no sponsor input by construction).

## 5. MVP scope for today

**In (P0, all built):** source registry + 2 adapters; polite fetcher (robots, delay, retry/backoff, no bypass); validation; persistence; observations; change events; deterministic+spec matching with confidence and review queue; explainable deal score; search/filter; listing detail + price history; research assistant over DB (LLM optional); watchlist with target price & restock detection; responsive UI; tests; docs.
**In (cheap P1):** import-cost estimator (assumption-flagged), affiliate infra, plan limits, admin source status/collect button, JSON API.
**Out:** accounts/auth, email/push alerts delivery, billing, ML, Playwright, business UI, Postgres/Alembic, cross-currency conversion.

## 6. Acceptance criteria → see `docs/PROJECT_PROGRESS.md` for the evidence checklist.
