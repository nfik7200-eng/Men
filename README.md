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
python -m pytest                                    # 46 tests, no network needed
```
Run `collect` again later (cron/daily): each run appends observations; price changes, restocks and removals become events.

## Configuration (env vars)
`WATCHTOWER_DB_URL` (default SQLite `data/watchtower.db`) · `WATCHTOWER_CONTACT` · `WATCHTOWER_MIN_DELAY` · `WATCHTOWER_ADMIN_TOKEN`
(required for /admin if not on loopback) · `OLLAMA_URL`/`OLLAMA_MODEL` (optional) · `AFFILIATE_CONFIG` (see `shopping/affiliate.py`)
· `IMPORT_RULES` (default `data/import_rules.json`; shipped rates are unverified placeholders).

## Layout
`watchtower/` shared engine · `shopping/` consumer product + web UI · `tests/` (fixtures are real captured pages) · `docs/`.
JSON API: `/api/v1/listings`, `/listings/{id}`, `/listings/{id}/history`, `/deals`, `/events`, `/sources`, `/research?q=`.

## Deploy (cheap)
One small VPS or free-tier VM: `pip install -r requirements.txt`, run `uvicorn main:app --host 127.0.0.1` behind Caddy/nginx (TLS),
a systemd timer or cron for `python -m watchtower.cli collect`, and a daily copy of the SQLite file. Set `WATCHTOWER_ADMIN_TOKEN`.
Do not expose publicly before accounts exist: the watchlist is a single shared "local" owner.

## Not built yet
Accounts/auth, alert delivery (email/push), billing, business-monitoring UI, Alembic migrations, ML prediction, any real retailer source.
