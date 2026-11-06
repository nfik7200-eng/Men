# Watchtower — AI Shopping & Market Intelligence (MVP)

Collects public product listings, remembers every price it sees, matches products across stores, scores deals with
an explainable method, and answers shopping questions from its own database (no LLM required).

> **Data honesty:** the only sources collected today are two public *scraping-practice* sites (`webscraper.io` test site,
> `books.toscrape.com`). Pipeline is real; prices are demo data. The UI says so. See `docs/SPEC.md`.

## Quick start
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export WATCHTOWER_CONTACT="you@example.com"        # identifies the crawler to site owners
python -m watchtower.cli collect --max-pages 6      # real collection (≈30 s, polite 1.5 s delay)
uvicorn main:app --port 8000                        # open http://localhost:8000
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
One small VPS or free-tier VM: `pip install -r requirements.txt`, run `uvicorn main:app --host 127.0.0.1` behind Caddy/nginx (TLS),
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
