# Security Architecture

Version 0.1 (2026-11-03). Status: **Draft, awaiting approval.**

## 1. Threat model (summary)

| Asset | Threats |
|---|---|
| API keys (RPC, Etherscan, GitHub, GCP) | Leak via repo, logs, error messages, AI prompts; quota theft |
| Host (shared VPS with other projects) | Resource exhaustion (disk, RAM, CPU); compromise via SSRF or a malicious dependency |
| Data integrity | Malicious or buggy provider responses; reorg mishandling; poisoned scraped content |
| Intelligence outputs | Defamation by misattribution; LLM hallucination presented as fact; prompt injection via scraped or on-chain text (token names, memos) |
| Providers and websites | Abuse of their service (ToS violations, excessive load) |

## 2. Secrets management
- Secrets are supplied only via environment variables or files referenced by `*_FILE` env vars (Docker secrets compatible). A `.env` is allowed for local development only and is git-ignored. `.env.example` lists the names, never the values.
- Provider endpoint URLs that embed keys (e.g. `https://…/v2/<KEY>`) are themselves secrets and are stored in env vars, never in registry YAML.
- Settings use pydantic `SecretStr`. Log formatters redact `SecretStr` values, query-string keys and URL path segments matching known key patterns.
- Pre-commit secret scanning (e.g. gitleaks) is planned for Stage 1. GitHub secret scanning should be enabled on the repository (owner action; not yet verified).
- Keys are rotated when exposure is suspected. The registry records `key_id` (not the key) for usage accounting.

## 3. SSRF and URL safety (configurable RPC/web URLs)
- Only `https://` and `wss://` are allowed. `http://` is accepted only for explicitly flagged local endpoints (`allow_private: true`, e.g. a future self-hosted node), and requires an ADR.
- The host is resolved and each resolved IP is rejected if it is private, loopback, link-local, multicast or reserved (RFC 1918, 127/8, 169.254/16 including cloud metadata 169.254.169.254, ::1, fc00::/7, fe80::/10).
- The resolved IP is pinned for the connection to prevent DNS-rebinding.
- Redirects are disabled for RPC. For web fetches, each redirect hop is re-validated, with a maximum of 3.
- Response size caps (e.g. 50 MB RPC, 10 MB web) and timeouts are enforced. Decompression-bomb guard: stream with a byte limit.
- The web fetcher allow-lists content types.

## 4. Input validation
- All external responses are parsed into pydantic models (or pyarrow schemas) before use. Unknown fields are kept in the raw payload but ignored by logic.
- Hex, base58, address and signature formats are validated. Numeric overflow is checked (uint256 is stored as decimal string or 128-bit pairs, never float).
- On-chain and scraped strings (token names, memos, READMEs) are **untrusted**. They are escaped in UI output and wrapped as quoted data in LLM prompts with an explicit "data, not instructions" boundary.

## 5. Database access controls
- Separate Postgres roles:
  - `bip_ingest`: writes `ingest`, `registry`
  - `bip_process`: reads `ingest`, writes `entity`, `graph`, `intel`
  - `bip_api`: read-only on curated views
  - `bip_agent`: read-only on agent views; statement_timeout 10 s, row limits enforced in views or functions
  - `bip_admin`: migrations only
- No superuser for applications. Postgres listens only on localhost or the docker network.
- Migrations are versioned (Alembic). No ad-hoc DDL in application code.

## 6. Audit logging
- Append-only `audit.events` record config changes, registry edits, scope and watchlist changes, agent tool calls (tool, args hash, rows returned, duration), report publication and manual label assignments, with actor and timestamp.
- Provider usage logs double as an abuse-prevention audit.

## 7. AI agent security
- Agents never get a shell, raw SQL, the filesystem or general network access.
- Tool API (typed, read-only): `get_entity_profile(id)`, `get_transfers(address, window, limit≤500)`, `get_edges(entity, depth≤2, limit)`, `get_evidence(ids)`, `run_template(name, params)` (pre-approved, parameterized DuckDB/SQL templates), and `fetch_public_doc(url)` routed through the web fetcher with robots/ToS checks and a domain allow-list.
- Per-run budgets cover tool calls, rows and tokens. Timeouts apply.
- Outputs pass a verifier. Every claim needs evidence IDs, and any identity claim about a real person requires a cited verifiable public source or the claim is downgraded to a hypothesis and redacted from alerts.
- LLM provider adapters must not send secrets or private watchlist notes to remote providers unless the owner enables that per provider.

## 8. Provider and website abuse prevention
- Hard client-side rate limits sit below published limits (default 70% of the published per-second limit).
- Monthly burn-rate guard (`DATA_ACQUISITION_STRATEGY.md` §8).
- No multi-account or IP rotation to multiply free quotas. One account per provider unless the provider's terms explicitly permit more (e.g. dRPC allows up to 5 keys; their use is still bounded by account quota).
- The web fetcher obeys robots.txt, has a per-domain rate limit and an identifying User-Agent (generic project name and contact URL configured by the owner, never the owner's personal email unless they choose it).

### 8a. Capability probing (Stage 4, implemented)
- **Targets.** Probes go to fixed registry endpoints only. The API has no URL field (extra fields are rejected), so there is no user-controlled RPC URL.
- **Transport and methods.** All requests use the hardened transport (validated, pinned DNS answers; redirects refused; size cap). Methods are checked against the read-only allow-list, and write-capable patterns are rejected both at import and when a probe is sent.
- **Hard budgets** (8/provider, 48/run, 90 s, 2 concurrent, 5 s timeout, 0 retries, 1 MB, ≥ 1 s spacing, one run at a time) are enforced on the server. Configuration and API callers can only lower them.
- **Rate limits.** `Retry-After` is honoured, and a rate-limited provider is stopped for the run. There is no burst-based limit discovery and no background probing.
- **Secrets.** Neither provider error text nor response bodies are stored. Configured endpoint URLs are redaction-registered. Tests check that a configured key never appears in API output, the database or logs.

### 8b. ML and AI research (Stage 5, implemented)
- **Model artifacts.** joblib files are written and read only inside `<data_root>/ml/models`. They are loaded only when the sha256 matches the registry, and are size-capped. Uploaded or imported data is labels only (validated JSON); model files are never imported.
- **No self-training.** Predictions are never written as labels, and there is no automatic retraining. Reviews are append-only.
- **LLM providers.** Ollama is loopback-only, and BIP never pulls models. The OpenAI-compatible provider requires https and an SSRF-checked host, reads its key from an environment variable that is redaction-registered, refuses redirects and caps response size. Prompts and outputs are not logged.
- **Research tools.** Tools are typed, read-only and scoped to the subject wallet, with per-specialist permissions in code. There is no SQL, shell, HTTP or RPC access, and the LLM gets no tool access.
- **Prompt injection.** Claims are passed as data, the output schema is validated, and a screen rejects instruction-like text, verification claims, identity attribution and financial advice. Statuses are assigned by BIP only.
- **Budgets.** Ceilings on time, tool calls, model calls, output sizes and concurrency are enforced on the server.

## 9. Resource protection on the shared VPS
- Container CPU and memory limits. `ionice`/`nice` for batch jobs.
- **Disk guard:** before any write batch, check free space on `BIP_DATA_ROOT`'s filesystem. Pause ingestion when free space < max(5 GB, 5%) or when BIP's usage exceeds its configured quota (`BIP_MAX_DATA_BYTES`). Raise an alert at 80% of the BIP quota.
- Postgres `max_connections` kept small. DuckDB `memory_limit` and `threads` set explicitly (e.g. 4 GB / 3 threads).
- No writes outside `BIP_DATA_ROOT`, the Postgres volume and the log dir.

## 10. Backup, restore, worker failure recovery
- See `DATA_ARCHITECTURE.md` §4.7. Backups are encrypted at rest when stored off-host (restic or age).
- Worker crash: leases expire and tasks are re-leased. Idempotent writes (content-addressed file names plus checkpoint transactions) make retries safe.
- Supervisor: docker `restart: unless-stopped`, health checks, and exponential restart backoff.

## 11. Data retention and privacy
- Data is public blockchain data plus public web content. No personal data is collected beyond what public sources expose, and BIP does not enrich addresses with identities from private sources.
- Retention per `DATA_ARCHITECTURE.md` §4.6. Scraped page bodies are kept only for allow-listed domains, as hashed snapshots plus extracted text.

## 12. Dependency and supply-chain hygiene
- Lockfile (`uv.lock`), pinned versions, `pip-audit` in CI, minimal dependencies, and no install scripts from untrusted sources.
