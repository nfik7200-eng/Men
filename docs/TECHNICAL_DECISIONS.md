# Technical decisions (date · decision · why · revisit when)
- 2026-11-05 · HTTPX+BeautifulSoup, no Scrapy/Playwright · sources are static HTML, one process, fewer moving parts · a source needs JS or >20 sources.
- 2026-11-05 · SQLite + SQLAlchemy 2, `create_all` (no Alembic yet) · $0, schema still moving · first deployment with data worth keeping → add Alembic + Postgres.
- 2026-11-05 · Server-rendered Jinja UI instead of Next.js · no Node toolchain, tested with TestClient, fast on Android · need rich client interactivity/auth flows.
- 2026-11-05 · Integer minor units, NULL = unavailable · prevents "unavailable == free" bugs.
- 2026-11-05 · Own matching (identifiers → model codes + spec agreement → review queue) instead of embeddings/LLM · deterministic, explainable; LLM only if review queue grows.
- 2026-11-05 · Assistant = retrieval + rules; Ollama optional & UNTESTED against a real model (not installed) · must work with no LLM.
- 2026-11-05 · Sandbox sources only for MVP demo: only egress-permitted, explicitly scrape-friendly sites available; flagged `sandbox` everywhere.
- 2026-11-05 · Egress allowlist for this environment extended (user-approved) with webscraper.io, books.toscrape.com.
