# CLAUDE.md — Watchtower Shopping & Market Intelligence

Modular monolith (Python 3.11+): `watchtower/` = shared engine (sources, fetcher, ingest, matching, deals, monitor);
`shopping/` = consumer product (search, assistant, import cost, affiliate, plans, web UI). `watchtower` must NEVER import `shopping`.

## Commands
- Install: `pip install -r requirements.txt`
- Collect real data: `WATCHTOWER_CONTACT=you@example.com python -m watchtower.cli collect [--source K] [--max-pages N]`
- Run app: `uvicorn main:app --port 8000`   · Tests: `python -m pytest`

## Rules that must not be broken
- Missing price is NULL, never 0. Prices are integer minor units + currency; never convert currencies without a sourced FX feed.
- Never fabricate data. Every figure is verified / estimated / missing / stale. Fixtures in tests/fixtures are real captured pages.
- Fetcher: honour robots.txt, delay, backoff; on 401/403/451 STOP (no bypass). New source = new adapter in watchtower/sources + terms note.
- Removals are inferred only after a COMPLETE crawl. "Verified" deals need history (3+ obs over 7+ days).
- Never auto-merge products on weak evidence (see matching.py thresholds); uncertain → review queue.
- Sponsored placements and affiliate data must never influence ranking code (a test greps for it).
- Admin is fail-closed (token ≥20 chars required). Never trust `client.host`/XFF for auth. State-changing routes rely on the Origin check + SameSite cookies — keep them same-site forms.
- Fetcher: every URL (start, next, redirect) must be on the source's host allowlist; adapters never fetch directly. API adapters must expose `configured()` and never log credentials.
- Deal labels: 'Verified' needs the evidence rules in `watchtower/deals.py` docstring; stale data never ranks. Currency is never converted without a recorded, attributed FX rate.
- eBay adapter is scaffolding (mock-tested). Do not describe it as live until it has run against the eBay sandbox/production and the API License Agreement has been read.
- Docs: SPEC.md, ARCHITECTURE.md, TECHNICAL_DECISIONS.md, COMMERCIAL_ROADMAP.md, PROJECT_PROGRESS.md — update when decisions change.
