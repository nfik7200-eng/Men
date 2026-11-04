# Risk Register

Version 0.1 (2026-11-03). Likelihood (L) and impact (I) are rated High / Medium / Low.

| ID | Risk | L | I | Mitigation | Owner/Stage |
|---|---|---|---|---|---|
| R1 | **VPS disk exhaustion** (~94% used; other projects affected) | H | H | Disk guard with hard pause; `BIP_MAX_DATA_BYTES`; relocatable `BIP_DATA_ROOT`; separate storage before scale | Stage 1, owner decision |
| R2 | **Free-tier reductions or removal** (observed: Alchemy 300M→30M CU, Infura 6M→3M/day, QuickNode 50M→10M trial) | H | H | 6+ providers per chain; registry-driven limits; burn-rate guard; design works at reduced throughput | Ongoing |
| R3 | **EIP-4444 history expiry** — pre-Merge bodies and receipts disappearing from providers | H | M | Probe `history_floor_block`; BigQuery or era1 for pre-Merge; scoped pre-Merge only | Stages 4, 6, 10 |
| R4 | **Solana history unavailability** on free RPC; Old Faithful scale (~400+ TB total) | H | H | Scope-based Solana; per-epoch streaming extraction; mark `source_unavailable` honestly | Stage 8 |
| R5 | **BigQuery Solana dataset staleness** (reported stopped updating 2025-03-31, later lagging) | M | M | Check max timestamp before use; never treat as complete | Stage 10 |
| R6 | **Accidental BigQuery quota burn** (1 TiB/month; one full scan of logs is multi-TB) | M | M | Mandatory dry-run; per-query cap; monthly budget ledger | Stage 10 |
| R7 | **Incorrect or partial provider responses** treated as complete | M | H | Validation suite, cross-provider sampling, quality scores, quarantine | Stages 3, 6 |
| R8 | **Reorg mishandling** causing phantom events or alerts | M | H | Finality-aware statuses; correction events; alert "unconfirmed" labels | Stage 7 |
| R9 | **Misattribution / defamation** (address ↔ person) | M | H | Evidence model; identity claims need verifiable public sources; verifier blocks unsupported claims | Stages 11, 15 |
| R10 | **LLM hallucination / prompt injection** via token names, memos, scraped text | H | M | Evidence-only bundles; quoted-data boundaries; verifier; no tool escalation | Stage 15 |
| R11 | **ToS violation / IP ban** from aggressive polling or scraping | M | M | Client-side limits below published; robots; official APIs first; no IP rotation | Stages 3, 14 |
| R12 | **Secret leakage** (keys embedded in URLs) | M | H | URLs as secrets; redaction; gitleaks; env-only | Stage 1 |
| R13 | **Scope creep toward a full replica** | M | H | Scope-based acquisition principle; ADR required for chain-wide datasets | Ongoing |
| R14 | **Ingestion throughput too slow** for a serious system on free tiers (months for post-Merge backfill) | H | M | Prioritise live + scoped; BigQuery targeted slices; paid option documented for approval | Scenario 2 |
| R15 | **Inconsistent RPC semantics** (`eth_getBlockReceipts` support, log range limits, Solana WS methods) | H | M | Capability probe + per-method capability gates | Stage 4 |
| R16 | **Small-file explosion** in Parquet from live ingestion | M | M | Nightly compaction; manifest-based reads | Stage 5 |
| R17 | **Single-host failure** (VPS loss) | L | H | Off-host backup of Postgres and Bronze manifests (needs storage target) | Stage 13 |
| R18 | **Provider facts in this blueprint are search-derived**, not directly fetched (sandbox network policy) | H | M | `verified_at: null` until direct check; probe validates real behaviour | Stage 2/4 |
| R19 | **Resource contention with existing VPS workloads** (Ollama models, quant projects) | M | M | Container CPU/RAM caps; nice/ionice; schedule heavy jobs off-peak | Stage 1+ |
| R20 | **Price data unreliability** making PnL misleading | M | M | PnL only with coverage-complete cost basis; else `unknown`; show confidence | Stage 11 |
| R21 | **Analytical output read as identification or advice** (pattern labels, research leads) | M | H | Hypothesis labels ("Possible … pattern"), no identity/ownership inference, "not investment advice" on every lead, findings contract rejects unsupported verified claims | Stage 3 |
| R22 | **False corroboration** from providers that share infrastructure | M | H | Mandatory `infrastructure` group per RPC provider; independence = different groups; `could_not_run` recorded as nothing (ADR-021) | Stage 3 |
| R23 | **Uncalibrated thresholds** producing misleading anomalies | M | M | Robust baselines, minimum-data gates, no high severity under 30 samples, confidence capped at 0.5 for uncorroborated evidence; methodology published | Stage 3 |
| R24 | **Probing misread as provider verdicts** (an environment block shown as an outage; one sample read as archive access) | M | H | Separate capability status and connectivity; environment restrictions labelled "availability unknown"; `archive_capability_unverified` always; reliability minimum samples | Stage 4 |
| R25 | **Probes stressing providers or consuming quota** | L | M | Hard ceilings (8/provider, 48/run, 90 s, 0 retries, ≥ 1 s spacing), manual runs only, stop on 429/403/wrong network/blocked | Stage 4 |
