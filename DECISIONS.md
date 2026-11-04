# Architecture Decision Records

Status values: **Proposed** (recommended, awaiting owner approval), **Accepted**, **Superseded**, **Rejected**.
All ADRs below were written on 2026-11-03 and are **Proposed**. None has been approved by the owner yet. "Implemented provisionally" means code follows the proposal, but it can still be changed if the owner decides otherwise.

| ADR | Decision | Status |
|---|---|---|
| 001 | Scope-based acquisition, not a full-chain replica | Proposed |
| 002 | Lakehouse: Parquet+zstd lake (Bronze/Silver/Gold) + PostgreSQL for operational/high-value data | Proposed |
| 003 | DuckDB + Polars for processing; defer ClickHouse | Proposed |
| 004 | Python 3.12, uv, httpx/asyncio, pydantic; ruff, pyright, pytest | Proposed; implemented provisionally in Stage 1 (httpx not yet added) |
| 005 | All external access via the Provider Intelligence Router; registry-driven capabilities | Proposed |
| 006 | Postgres as job/lease queue (`SKIP LOCKED`) and event outbox; Redis only for rate-limit buckets and cache from Stage 3b | Proposed |
| 007 | Baseline provider set (see PROVIDER_REGISTRY §5) | Proposed |
| 008 | Finality-aware statuses and never-delete reorg handling | Proposed |
| 009 | AI out of ingestion path; read-only typed tool API; evidence-cited claims | Proposed |
| 010 | Thin httpx web fetcher; defer Scrapy/Playwright | Proposed |
| 011 | Relocatable storage root with disk guard; separate storage required before scale | Proposed |
| 012 | No self-hosted ETH/SOL nodes in Phase 1 | Proposed |
| 013 | Docker Compose with resource limits for deployment; no Kubernetes | Proposed |
| 014 | Stage 1 package layout and configuration conventions | Proposed; implemented provisionally in Stage 1 |
| 015 | SQLite (via SQLAlchemy Core) as the BIP 0.1 operational store; PostgreSQL deferred | Proposed; implemented provisionally in Stage 1b |
| 016 | FastAPI + React/Vite/TypeScript application moved forward to Stage 1b (was Stages 13/16, Next.js) | Proposed; implemented provisionally in Stage 1b |
| 017 | Provider registry as TOML (stdlib tomllib) instead of YAML; read-only method allow-list; host allow-lists | Proposed; implemented provisionally in Stage 2 |
| 018 | Stdlib HTTPS transport with IP pinning, no redirects and size caps (no httpx/web3/solana SDK) | Proposed; implemented provisionally in Stage 2 |
| 019 | Bounded synchronous ingestion with a single-run lock instead of a job queue | Proposed; implemented provisionally in Stage 2 |
| 020 | Ethereum wallet discovery via Transfer logs over a bounded block window; Solana via signature lists | Proposed; implemented provisionally in Stage 2 |

---

## ADR-001 Scope-based acquisition
**Context.** A full ETH archive is about 2–3 TB on Reth/Erigon. Solana's full ledger is over 400 TB. Free quotas are millions of requests per month, and the VPS has about 8 GB free.
**Decision.** Collect by declarative scopes: contracts, programs, tokens, wallets, event signatures, depth. Chain-wide collection is limited to cheap Tier-0 datasets: headers and slot metadata, plus thresholded transfers.
**Consequences.** Coverage is honest and bounded. Chain-wide analytics, such as total holders for an arbitrary token, are unavailable unless that token is in scope, and reports must state this.
**Alternatives.** Full node (rejected for now: disk). BigQuery full export (rejected: scan quota, paid egress).

## ADR-002 Lakehouse storage
**Decision.** Immutable Bronze Parquet with a provenance envelope. Rebuildable Silver and Gold. PostgreSQL holds checkpoints, coverage, registry state, entities, aggregated edges and findings. Postgres never holds per-transaction chain-wide rows.
**Trade-off.** Parquet needs compaction and manifest discipline. In exchange, storage is cheap and compressed and scales to billions of rows.

## ADR-003 Processing engines
**Decision.** DuckDB (SQL over Parquet) and Polars. ClickHouse is deferred until there is a need for sub-second interactive queries over more than about 10B rows, or for concurrent multi-user analytics.

## ADR-004 Language and tooling
**Decision.** Python 3.12+, uv, httpx (async JSON-RPC), pydantic v2, ruff, pyright, pytest. Web3.py and solders are used only for encoding/decoding and types, not for transport.

## ADR-005 Router as the only egress for chain data
**Decision.** Every chain, dataset and indexer request goes through the router. The router applies capability gates, quotas, breakers, verification and accounting. Intelligence code depends only on chain-agnostic client interfaces.

## ADR-006 Queues
**Decision.** Postgres `FOR UPDATE SKIP LOCKED` for range tasks and leases, so they are transactional with checkpoints. A Postgres outbox table for intelligence events. Redis is introduced at Stage 3b only for shared token buckets and caching.
**Alternative.** Redis Streams for queues. This is deferred because it would split transactional state.

## ADR-007 Baseline providers
**Decision.** As listed in `docs/PROVIDER_REGISTRY.md` §5. It requires the owner to create free accounts. No paid plans.

## ADR-008 Reorgs
**Decision.** Records carry `observed → finalized | orphaned`. Orphaned data is retained. Historical ingestion runs only up to finalized.

## ADR-009 AI boundary
**Decision.** Detectors emit events. Agents consume curated evidence through a read-only typed tool API. Claims must cite evidence IDs. A verifier gates the output. Ollama comes first, through an adapter interface.

## ADR-010 Web fetching
**Decision.** Official APIs and feeds first. A thin httpx fetcher with robots/per-domain limits and change detection. Playwright is used only for allow-listed sites. Scrapy is deferred.

## ADR-011 Storage location
**Decision.** All data lives under a configurable `BIP_DATA_ROOT` (local path or S3-compatible URI). There is a hard BIP byte quota and a free-space guard. The prototype stays at ≤ 5 GB on the VPS. Any larger target requires owner approval. Candidates: an attached volume, a second disk or home server running MinIO, or R2/B2 (paid beyond about 10 GB).

## ADR-012 No self-hosted nodes in Phase 1
**Decision.** No ETH or Solana nodes. Revisit when separate storage of 3 TB+ NVMe exists: a Reth/Erigon archive node would remove most RPC quota constraints for Ethereum.

## ADR-013 Deployment
**Decision.** Docker Compose with explicit CPU and memory limits so existing VPS workloads are protected. Kubernetes is not used.

## ADR-014 Stage 1 package layout and configuration conventions
**Context.** Build Prompt 02 suggested `bip/{config,core,contracts,security,storage}`. The roadmap's Stage 1 row listed `router`, `chains`, `ingest`, `quality`, `process` and `intel` packages as well.
**Decision.**
- Create only packages that contain working code: `config`, `core`, `contracts`, `security` and `storage`. Later packages are added with their stage, never as empty placeholders.
- Settings come only from `BIP_*` environment variables (nested with `__`). `.env` is never read implicitly; local use is `uv run --env-file .env`.
- Contracts are immutable pydantic models with `extra="forbid"` and strict integer types (no bool or string coercion).
- EVM and Solana each get their own record models, sharing only chain-agnostic references: `TxRef`, `ChainPosition`, `AddressRef`, `Provenance`.
- New chains are added as one entry in `KNOWN_CHAINS`.
**Consequences.**
- Unknown `BIP_*` variables are ignored, because provider secret references will share the prefix. This should be revisited in Stage 2.
- Adapters must convert RPC hex and string values to Python ints themselves, since contracts never coerce.

## ADR-015 SQLite as the BIP 0.1 operational store
**Context.** ADR-002 puts operational data in PostgreSQL. Build Prompt "BIP 0.1" asked for a locally runnable app with SQLite and no PostgreSQL/Redis.
**Decision.**
- Use SQLite through **SQLAlchemy Core** (not the ORM). The file lives at `<data_root>/operational/bip.sqlite3`.
- Run with WAL mode and foreign keys on.
- Schema changes go through versioned in-code migrations.
- Core expressions keep SQL dialect-portable, so moving to PostgreSQL later changes the engine URL and migrations rather than every query.
**Consequences.**
- Fine for a single local user. Not for concurrent multi-process writers, or for chain-wide data, which still belongs in the Parquet lake (ADR-002).
- PostgreSQL returns when Stage 3b needs shared state across workers.
**Alternatives.** Stdlib `sqlite3` with hand-written SQL: fewer dependencies, but not portable. Alembic: deferred until there is more than one migration.

## ADR-016 Application stack moved forward
**Context.** The roadmap had the API at Stage 13 and a Next.js dashboard at Stage 16. The owner asked for an integrated, runnable prototype now (BIP 0.1).
**Decision.**
- FastAPI for the API, under `bip/api` (inside the existing package, not a parallel `backend/`).
- React 19, TypeScript and Vite for the UI under `frontend/`, as a static SPA with no server-side rendering.
- Vite's dev and preview servers proxy `/api`, so the browser uses one origin.
**Consequences.**
- No Node server in production: the SPA build can be served as static files.
- Next.js is dropped unless SSR becomes necessary.
- The UI shows real backend state only. Sections whose engines do not exist (flows, opportunities) are clearly marked placeholders.

## ADR-017 Provider registry format and safety rules
**Context.** `PROVIDER_REGISTRY.md` §6 sketched a YAML registry, but YAML would add a dependency.
**Decision.**
- Use `bip/providers/registry.toml`, loaded with the stdlib `tomllib`. Entries are validated by pydantic at startup.
- **Methods:** only methods in a code-level read-only allow-list may be listed. Write and signing methods are rejected.
- **Endpoints:** a built-in URL is allowed only for keyless public providers. Credentialed URLs come from `BIP_PROVIDER_*` environment variables, and their host must match that provider's `allowed_hosts`.
- **Unknowns:** undocumented capabilities and limits are left out, so they load as unknown.
- **Throttle:** `client_rps` is BIP's own politeness throttle and is labelled as such.
- **Consequence:** adding a provider is a data change, and no ingestion code changes.

## ADR-018 Outbound transport
**Decision.**
- A small transport built on the standard-library `http.client`: HTTPS only, the resolved address validated and pinned, TLS checked against the hostname, redirects refused, responses capped by size, timeouts on every request.
- Optional `HTTPS_PROXY` mode for restricted networks. The proxy resolves hosts, so pinning is not possible in that mode, and this is documented.
- JSON-RPC envelopes are validated strictly.
- No web3.py or Solana SDK. Parsing is done by BIP's own pure functions over BIP contracts.
**Trade-off.** The code and its tests are BIP's to maintain. In return it has exact control over the SSRF and size controls, and no new dependency.

## ADR-019 Synchronous bounded ingestion
**Decision.**
- `POST /ingestion/wallet/{chain}/{address}` runs one bounded run inside the request: at most 50 transactions, 150 RPC calls and 90 s by default.
- A process-wide lock allows one run at a time. A second request gets 409.
- Runs, errors and cursors are persisted, so an interrupted run resumes.
**Consequences.**
- The request can take tens of seconds on rate-limited public endpoints.
- A queue (ADR-006, Postgres `SKIP LOCKED`) replaces this when background or bulk ingestion is needed.

## ADR-020 Wallet discovery without an address index
**Context.** Standard Ethereum JSON-RPC has no "transactions by address" method.
**Decision.**
- **Ethereum:** discover activity through `eth_getLogs` for the ERC-20/721 `Transfer` topic with the wallet as sender or recipient, over a bounded recent block window (default 2,000 blocks, chunked and split adaptively). Then fetch each transaction, its receipt and its block.
- **Solana:** use `getSignaturesForAddress`, bounded, newest first, then `getTransaction`.
- Coverage gaps are stored with each run and shown in the UI. On Ethereum these are plain ETH transfers without logs, internal transfers, and activity older than the window.
**Alternatives.**
- Explorer or indexer APIs: need keys, and are planned for Stage 10.
- Block scanning: too costly on free tiers.

## ADR-021 Provider independence by infrastructure group (Proposed)
**Context.** Corroboration is only meaningful if the providers do not share infrastructure. Different provider ids can front the same operator (for example, PublicNode Ethereum and Solana are both Allnodes).
**Decision.**
- Every RPC provider in `registry.toml` must declare `infrastructure = "<group>"`.
- Two providers are independent only when their groups differ.
- Corroboration requires at least two independent groups reporting identical normalized fields.
- The original source counts as one group when its group is declared.
**Consequences.**
- With today's configuration (one keyless provider per chain), corroboration reports `could_not_run`.
- A second, independent provider (free account, owner approval) is required to corroborate anything.

## ADR-022 Intelligence computed on demand from SQLite; no graph database or Parquet yet (Proposed)
**Context.** Stage 3 needs profiles, flows, graphs and detectors. Data volumes are tiny: bounded runs of ≤ 50 transactions.
**Decision.**
- Compute on demand from the operational SQLite tables in pure Python, bounded by `BIP_INTEL__*` limits.
- Persist only analysis snapshots, detector outcomes, findings and leads.
- No NetworkX, Neo4j, DuckDB or Parquet.
**When to revisit (measured triggers).**
- A single wallet analysis exceeds 2 s.
- The graph routinely hits its node or edge limits.
- Stored transfers exceed about 1 million rows.
At that point move analytical reads to Parquet + DuckDB (ADR-005) and keep SQLite for state.

## ADR-023 Reproducible analysis runs keyed by input hash (Proposed)
**Decision.**
- An analysis run is keyed by sha256 over:
  - every input record's identity and effective verification;
  - all intelligence settings;
  - the `as_of` date;
  - the analyzer version.
- Identical inputs reuse the earlier run.
- Detector outcomes are stored for every status.
- Findings get deterministic ids from detector, wallet and supporting evidence, so a refresh never duplicates them.
- Source observations are read-only for the intelligence layer.

## ADR-024 Research leads, not recommendations (Proposed)
**Decision.**
- "Opportunities" are research leads from a published rule (`rising_participation` 1.0.0) with transparent feature contributions.
- They require minimum data, or return no candidate.
- They are labelled "unverified research lead" unless every evidence transaction is corroborated.
- They always carry a "not investment advice" disclaimer.
- No price, return or buy/sell signal is produced.

## ADR-025 Budgeted, manual capability prober built on the router (Proposed)
**Context.** BIP needs evidence about what each provider actually serves before routing work to it, without stressing providers, spending quota or creating accounts.
**Decision.**
- One prober (`bip/providers/prober.py`) reuses the registry, the router (`ProviderRouter.probe`: one request, pinned provider, no retries or failover) and the hardened transport.
- Hard budget ceilings: 8 requests per provider, 48 per run, 90 s, 2 concurrent providers, 5 s timeout, 0 retries, 1 MB per response, ≥ 1 s spacing per provider, one run at a time. Configuration and API callers can only lower them.
- Runs are manual only (API, dashboard button after a preview, `make probe`). There is no scheduler.
- Lookups use hashes and signatures from the provider's own live answers.
- Capability status (supported / unsupported / inconclusive / not_tested) is kept separate from connectivity. Only −32601 means "unsupported". DNS and proxy failures are environment restrictions.
**Consequences.**
- A run tests at most 8 of the 12–14 catalogue capabilities per provider. The rest are explicitly `not_tested` until a targeted run.
- There is no automatic discovery of rate limits.

## ADR-026 Provider reliability needs minimum samples and excludes environment failures (Proposed)
**Decision.**
- Reliability states require ≥ 20 attempts across ≥ 3 runs. Below that the state is `insufficient_data`.
- Environment-blocked attempts are excluded, and a provider with only blocked attempts is `unknown`.
- Median latency needs ≥ 5 samples and p95 needs ≥ 20.
- There is no score and no ranking.
- Consistency checks are stored separately from evidence verification and never change it. Independence follows ADR-021.
**When to revisit.** When real observations exist, calibrate the 0.95 / 0.80 thresholds and decide whether routing should consume these states (Stage 5).

## ADR-027 scikit-learn for wallet classification; rules baseline always on (Proposed)
**Decision.**
- Add `scikit-learn` and `numpy` (CPU-only; about 125 MB in the venv including SciPy) for Random Forest, Isolation Forest, HDBSCAN and calibration.
- Not added: LightGBM, XGBoost, CatBoost or deep learning. These are heavier, and there is no labelled data that could show they add value.
- The deterministic `bip-rules` baseline always runs.
- Models are trained only on request, only past an honest data gate, and are versioned with dataset hashes.
- Synthetic training is opt-in (`BIP_ML__ALLOW_SYNTHETIC_TRAINING`), and its models are always labelled `experimental_synthetic`.
**Risk.**
- Model artifacts are joblib (pickle). They are written and read only inside `<data_root>/ml/models`, and loaded only when the sha256 matches the registry. Files from elsewhere are never loaded.
- scikit-learn has no type stubs, so `bip/ml/backend.py` runs in pyright `basic` mode. All other code stays strict.

## ADR-028 Deterministic research planner; LLM only synthesizes over curated claims (Proposed)
**Decision.**
- Investigations use a fixed (specialist, tool) plan and typed read-only tools scoped to the subject wallet.
- The LLM receives claims as data and may only return interpretations or hypotheses that cite existing claim ids. These pass a screen for instructions, verification claims, identity and financial advice.
- Statuses are assigned by BIP and never by the model.
- Ollama is loopback-only. An OpenAI-compatible provider is optional, and none is the default.
**Consequences.**
- The design is strongly resistant to prompt injection.
- The AI cannot ask for more data mid-run. LLM-driven tool selection would need a new ADR.

## ADR-029 Asynchronous, bounded research runs in-process (Proposed)
**Decision.**
- At most 2 concurrent investigations run in a thread pool inside the API process.
- Every state change is persisted, and cancellation is checked between steps.
- Runs left unfinished at restart are marked failed.
- No queue service is used (ADR-006 still governs any future queue).
