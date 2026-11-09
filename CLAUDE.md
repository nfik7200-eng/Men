# CLAUDE.md — Watchtower Shopping & Market Intelligence

Modular monolith (Python 3.11+): `watchtower/` = shared engine (sources, fetcher, ingest, matching, deals, monitor);
`shopping/` = consumer product (search, assistant, import cost, affiliate, plans, web UI). `watchtower` must NEVER import `shopping`.

## Commands
- Install: `pip install -r requirements.txt`
- Collect real data: `WATCHTOWER_CONTACT=contact@your-org.net python -m watchtower.cli collect [--source K] [--max-pages N]`
- Run app: `uvicorn main:app --port 8000 --no-proxy-headers`   · Tests: `python -m pytest`
- Snapshot DB (WAL-safe; never `cp` the .db): `python -m watchtower.cli backup [--out PATH]` · Integrity: `python -m watchtower.cli verify --db PATH`
- Acquisition: offline selection preview `python -m watchtower.cli plan --db COPY --source K [--explain URL]` · coverage `python -m watchtower.cli coverage --db COPY` · per-source budget `WATCHTOWER_BUDGET_<KEY>` (ceiling 500)
- Scheduler is DISABLED by default (`data/schedule.json` + `WATCHTOWER_SCHEDULER_ENABLED=1`); never install cron/services without the owner's approval (docs/SCHEDULING.md)
- Quality gates: `python -m pytest` · `ruff check watchtower shopping tests scripts main.py` (ruff.toml) · `pyright watchtower shopping` · browser journeys `e2e/run.sh` (Playwright on a DISPOSABLE copy) · scale benchmark `python scripts/perf_bench.py` (synthetic temp DB)
- Optional ML (advisory only, never feeds matching): `.venv-ml` + docs/ML.md · production cleanup preview: `python -m watchtower.cli cleanup-preview --db SNAPSHOT` (docs/DB_CLEANUP_RUNBOOK.md) · deployment package: deploy/ + docs/DEPLOYMENT.md (not installed anywhere)
- Improvement loop: docs/LOOP_ENGINEERING.md + skill `improvement-loop`; experiment only on snapshot copies, bench with `python scripts/loop_bench.py SNAPSHOT`

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
- Live sources run ONLY through `watchtower/policy.py` (approved + current + robots-compliant + real contact). Never add a source without a reviewed entry in `data/source_policies.json`; robots.txt alone is not permission (read the terms). Never store descriptions/images. Demo/sandbox data never enters real-data views, deal ranking or peer statistics.
- Matching: hard-conflict vetoes apply at every stage including identifier matches; the merchant SKU is never an identifier; ambiguous candidates go to review.
- Name hints (URL slug / card text) are acquisition-priority evidence ONLY: never write product_id/match_status from them. Observations are append-only (ORM guard). Discovered URL != acquired product.
- Security baseline (independent review, completion programme): signed expiring admin cookie, bounded decoded response size (16 MB pages, 512 KiB robots), robots matching without regex, no credentials on redirect to another host or https->http, per-client limits on /watch and /go, API docs off. Keep tests/test_security_fixes.py and tests/test_fetcher_limits.py green.
- Product-page crawls only sample: never infer removals from absence; a 304 must still record an observation.
- Docs: SPEC.md, ARCHITECTURE.md, TECHNICAL_DECISIONS.md, COMMERCIAL_ROADMAP.md, PROJECT_PROGRESS.md, LOOP_ENGINEERING.md — update when decisions change.
