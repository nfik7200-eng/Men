# Watchtower — AI Shopping & Market Intelligence (MVP)

**Strategy: free, permitted web scraping first. No paid APIs, proxies or data contracts.** Real merchants today: Adafruit and SparkFun (see `docs/SOURCE_DISCOVERY.md`); demo catalogues are test fixtures only.

Collects public product listings, remembers every price it sees, matches products across stores, scores deals with
an explainable method, and answers shopping questions from its own database (no LLM required).

> **Data honesty:** two real merchants (Adafruit, SparkFun — `kind=live`) plus two scraping-practice sites (`kind=demo`, never mixed into real results). The UI badges every listing as *Real retailer*, *Demo catalogue* or *API sandbox*. `docs/SOURCE_DISCOVERY.md` lists every source evaluated, approved, rejected or unverified and why.

## Quick start
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export WATCHTOWER_CONTACT="contact@your-org.net"        # identifies the crawler to site owners
python -m watchtower.cli collect --max-pages 6      # real collection (≈30 s, polite 1.5 s delay)
uvicorn main:app --port 8000 --no-proxy-headers   # open http://localhost:8000 (never trust X-Forwarded-For unless WATCHTOWER_TRUST_PROXY=1 behind your own proxy)
python -m pytest                                    # 143 tests, no network needed
```
Run `collect` again later (cron/daily): each run appends observations; price changes, restocks and removals become events.

## Configuration (env vars)
`WATCHTOWER_DB_URL` (default SQLite `data/watchtower.db`) · `WATCHTOWER_CONTACT` · `WATCHTOWER_MIN_DELAY` · `WATCHTOWER_ADMIN_TOKEN`
(**required** to use /admin, ≥20 chars; admin is disabled without it) · `WATCHTOWER_TRUST_PROXY=1` (only behind your own proxy) · `WATCHTOWER_COOKIE_SECURE=1` · `WATCHTOWER_RATE_LIMIT` (per IP/min, default 120) · `OLLAMA_URL`/`OLLAMA_MODEL` (optional) · `AFFILIATE_CONFIG` (see `shopping/affiliate.py`)
· `IMPORT_RULES` (default `data/import_rules.json`; shipped rates are unverified placeholders).

## Layout
`watchtower/` shared engine · `shopping/` consumer product + web UI · `tests/` (fixtures are real captured pages) · `docs/`.
JSON API: `/api/v1/listings`, `/listings/{id}`, `/listings/{id}/history`, `/deals`, `/events`, `/sources`, `/research?q=`.

## Deploy (cheap)
One small VPS or free-tier VM: `pip install -r requirements.txt`, run `uvicorn main:app --host 127.0.0.1 --no-proxy-headers` behind Caddy/nginx (TLS),
a systemd timer or cron for `python -m watchtower.cli collect`, and a daily copy of the SQLite file. Set `WATCHTOWER_ADMIN_TOKEN`.
Watchlists are per-browser (anonymous cookie) until real accounts exist. Read `docs/AUDIT_REPORT.md` §5 (open risks) before exposing publicly; run behind TLS and set `WATCHTOWER_ADMIN_TOKEN`.

## Not built yet
Real user accounts, alert delivery (email/push), billing, business-monitoring UI, Alembic migrations, ML prediction, any real retailer data (eBay adapter is scaffolding only).

## Admin, sources and rates (CLI)
```bash
python -m watchtower.cli status                      # sources, enabled/configured state, observation count
python -m watchtower.cli source disable books_toscrape
python -m watchtower.cli fx set USD UGX 3700 --as-of 2026-11-06 --source "<who published it>" --url <https://...>
export WATCHTOWER_ADMIN_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"   # then sign in at /admin/login
```
## eBay (not live)
Set `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` (env only), `EBAY_ENV=sandbox|production`, `EBAY_MARKETPLACE`, optional `EBAY_CAMPAIGN_ID`, `EBAY_QUERIES`.
Without credentials the source reports "not configured" and fetches nothing. Read `docs/DATA_SOURCE_RESEARCH.md` §4 before activating.

## Docs
`docs/AUDIT_REPORT.md` · `DATA_SOURCE_RESEARCH.md` · `COMMERCIAL_ROADMAP.md` · `TECHNICAL_DECISIONS.md` · `ARCHITECTURE.md` · `PROJECT_PROGRESS.md`

## Real-source collection (mission 003)
```bash
export WATCHTOWER_CONTACT="contact@your-org.net"   # REQUIRED for live sources: identifies the crawler; a placeholder is refused
python -m watchtower.cli policy                # which sources are approved (compliance gate)
python -m watchtower.cli collect --source adafruit --max-pages 100
python -m watchtower.cli collect --source sparkfun --max-pages 60   # honours SparkFun's Crawl-delay: 10 s (~1 page / 10 s)
python -m watchtower.cli health                # per-source success rate, freshness, throughput
python -m watchtower.cli rematch               # re-run cross-store matching after matcher/identifier changes
```
Re-running is incremental: each URL is re-checked on its own schedule (conditional GET; HTTP 304 still records an observation), so repeat runs never duplicate listings. A source is only collected if its policy in `data/source_policies.json` is `approved`, its review is < 90 days old, and robots.txt still allows the required paths (checked every run; a newly disallowed path auto-rejects the source). Descriptions and images are never stored.
