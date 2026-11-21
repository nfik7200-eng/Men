# Provider Capability Methodology (Stage 4)

Version 1.0 (2026-11-04). Applies to `bip/providers/{capabilities,prober,intelligence}.py`, `ProviderRouter.probe`, the `/api/v1/providers/...` intelligence routes and `make probe`.

The goal is an **evidence-based capability matrix** per provider: what was *documented*, what is *configured* locally, what was *tested and worked*, what was *observed to fail*, and what is *untested*. These are always kept apart.

## 1. Sources of truth

| Kind | Where it lives | Never confused with |
|---|---|---|
| **Documented** (limits, archive, log range) | `registry.toml`, with `documentation_basis` (`official_docs` / `search_derived` / `unverified`) and `documentation_checked` (date) | Observed behaviour |
| **Advertised / permitted methods** | `registry.toml` `methods`: the read-only methods BIP may call through that provider | Supported methods |
| **Configured** | Keyless built-in endpoint, or `BIP_PROVIDER_*_URL` set (a secret, never shown) | Reachable |
| **Observed** | `provider_capability_observations`, one row per capability per probe run | Documentation |

Probe results are never written back into the registry. Today every registry fact is `search_derived` (2026-11-03): direct fetches of provider pages were blocked in the authoring sandbox.

## 2. Capability catalogue

The catalogue is ordered by priority. A capability is either requestable (one read-only request) or **derived** from another capability's response at no extra request cost.

**Ethereum**

| # | Capability | Request |
|---|---|---|
| 1 | `chain_id` | `eth_chainId` (must be `0x1`) |
| 2 | `latest_block` | `eth_getBlockByNumber("latest", false)` |
| 3 | `finalized_block` | `eth_getBlockByNumber("finalized", false)` |
| 4 | `historical_block` | One block at ⌊(head − `BIP_PROBE__ETHEREUM_HISTORY_LOOKBACK_BLOCKS`, default 100,000) / 1000⌋ × 1000 |
| 5 | `transaction_lookup` | `eth_getTransactionByHash` |
| 6 | `transaction_receipt` | `eth_getTransactionReceipt` |
| 7 | `event_logs` | `eth_getLogs` for exactly the head block, filtered to the ERC-20/721 `Transfer` topic |
| 7a | `erc20_transfer_logs`, `erc721_transfer_logs` | Derived from `event_logs` (3-topic vs 4-topic logs) |
| 8 | `native_balance` | `eth_getBalance` of the zero address |
| 9 | `block_number` | `eth_blockNumber` |
| 10 | `contract_code` | `eth_getCode` of the zero address |

`transaction_lookup` and `transaction_receipt` use the first transaction hash listed in the provider's own `latest_block` answer.

**Solana**

| # | Capability | Request |
|---|---|---|
| 1 | `network_identity` | `getGenesisHash` (mainnet-beta) |
| 2 | `current_slot` | `getSlot` (finalized) |
| 3 | `finalized_block` | `getBlock` at that slot (no transaction details, no rewards) |
| 4 | `historical_block` | `getBlock` at ⌊(slot − 1,000,000) / 1000⌋ × 1000 |
| 5 | `signature_history` | `getSignaturesForAddress(System Program, limit 1)` |
| 6 | `transaction_lookup` | `getTransaction` for that signature |
| 6a | `native_transfer_data`, `spl_transfer_data`, `token_balance_changes` | Derived from the parsed transaction |
| 7 | `native_balance` | `getBalance` |
| 8 | `health` | `getHealth` |
| 9 | `version` | `getVersion` |
| 10 | `block_height` | `getBlockHeight` |
| 11 | `latest_blockhash` | `getLatestBlockhash` |

With the default cap of 8 requests per provider, the lowest-priority items are recorded as `not_tested` with the reason. A later run that selects only those capabilities tests them.

The identity capability (`chain_id` / `network_identity`) is **always** tested first, even when other capabilities are selected, and it counts against the cap. Without it, data from another network could be recorded as supported.

The profile shows the latest *tested* observation per capability. A later run that merely skipped a capability (not selected, over budget, stopped) never hides earlier evidence; the full per-run record stays in the history.

**Safe samples only.**
- Balance, code and history reads use public protocol addresses: the zero address on Ethereum, the System Program on Solana.
- Transaction hashes and signatures always come from the provider's **own live answer in the same run**. They are never invented, and never taken from stored records, so offline-fixture identifiers can never reach a real provider.
- If no sample exists (an empty block, no signature returned), the capability is `not_tested` with that reason.

## 3. Status vocabulary

| Capability status | Meaning |
|---|---|
| `supported` | The request returned a response that passed validation (types, required fields, identity, matching hash or number) |
| `unsupported` | The provider explicitly reported the method as not available (JSON-RPC −32601). **Nothing else becomes "unsupported"** |
| `inconclusive` | The request failed in a way that establishes neither support nor non-support: network, timeout, TLS, rate limit, auth, invalid parameters, data not available (e.g. a skipped slot), malformed or contradictory response, wrong network |
| `not_tested` | Deliberately not sent: budget, run stopped, no safe sample, not selected, or method not permitted for that provider |

**Connectivity** is a separate field:

| Connectivity | When |
|---|---|
| `reachable` | A valid JSON-RPC exchange happened, including "method not found" |
| `network_blocked` | DNS failure or proxy refusal (`environment_restricted = true`); generic connection failure; or a host that resolved to a non-public address |
| `authentication_required` | Credentialed provider without configuration |
| `unauthorized` | HTTP 401 / 403 |
| `rate_limited` | HTTP 429 or a JSON-RPC rate-limit error |
| `timeout` | No complete response within the timeout |
| `tls_error` | TLS failure |
| `provider_error` | HTTP 5xx or other 4xx, a redirect (never followed), or a JSON-RPC server error |
| `invalid_response` | Not JSON-RPC, or failed validation |
| `wrong_network` | Chain id or genesis hash differs from the registered network |
| `not_tested` | No request was sent |

The UI can therefore show "reachable, but method X unsupported" as well as "unknown: the local environment could not connect".

## 4. Environment versus provider failures

| Observed | Classification | What is claimed |
|---|---|---|
| DNS resolution failed | `network_blocked`, `dns_failure`, environment-restricted | *"Live verification could not be completed because of an environment network restriction. Provider availability remains unknown."* |
| Proxy refused CONNECT | `network_blocked`, `proxy_refused`, environment-restricted | Same |
| Other connection failure | `network_blocked`, `connection_failed` | "BIP cannot tell whether the local environment or the provider caused it. Provider availability remains unknown." |
| Host resolves to a private address | `network_blocked`, `unsafe_destination` | BIP refused to connect (SSRF protection) |
| HTTP 403 | `unauthorized` | "Access denied… does not show that the provider is down." |

None of these is ever reported as a provider outage, and none makes a capability `unsupported`. After the first such failure, that provider is not probed further in the run, so the same failure is never repeated.

The dashboard's "environment restriction" notice reflects the latest restricted observation only while no later observation has reached a provider. It disappears once connectivity is observed again.

Only validated values feed cross-provider comparisons. A response that failed validation (for example a finalized block above the latest block) is never compared.

## 5. Budget and rate-limit policy

Budgets are enforced on the server (`BIP_PROBE__*`). The values below are hard ceilings: configuration can only make probing more conservative, and API callers can only lower the per-provider cap.

| Limit | Default = ceiling |
|---|---|
| Requests per provider per run | 8 |
| Requests per run | 48 |
| Run duration | 90 s |
| Concurrent providers | 2 |
| Per-request timeout | 5 s |
| Retries | 0 (always) |
| Response body | 1 MB |
| Spacing per provider | ≥ 1 s (a floor; larger spacing allowed) |
| Concurrent runs | 1 (409 `probe_busy`) |

**How a request is made.** Every request goes through `ProviderRouter.probe`, which uses:
- the provider's registry endpoint only (host allow-list, IP-pinned transport, redirects refused, size cap);
- a pinned provider, with no failover and no retries;
- no hidden identity side-calls: identity is the first capability.

`probe` refuses to send a method that is outside the provider's allow-list or write-capable (a pattern check over `eth_send*`, `eth_sign*`, `personal_*`, `admin_*`, `debug_*`, `sendTransaction`, `requestAirdrop`, and others). It also refuses when the provider is disabled, unconfigured or in a rate-limit cooldown.

**Rate limiting.**
- On HTTP 429 or a rate-limit error, BIP honours `Retry-After` as a cooldown (capped), records the event and stops that provider for the run.
- A rate limit is not recorded as a provider failure in the lifecycle state, because a usage policy is not an outage.
- Where limits are documented, the registry records them. Where they are not, they stay "unknown".
- BIP never sends bursts to discover a limit.

**Never automatic.** Probes run only when someone starts one: `POST /api/v1/providers/probe-runs`, the dashboard button (after a budget preview), or `make probe`. They never run at startup, on page loads, after ingestion, in tests against real hosts, or in a background loop. A scheduled mode would be a future extension and needs an ADR.

## 6. Historical coverage

Each provider gets a coverage label from its latest `historical_block` observation:

| Label | When |
|---|---|
| `historical_sample_observed` | One historical block, at a recorded block or slot and distance from the head, was returned |
| `recent_only_observed` | Recent blocks worked; no historical sample has been tested |
| `historical_access_failed` | The provider answered but did not serve the sample: a null result, or "data not available" other than a skipped slot |
| `insufficient_evidence` | Nothing established: a skipped Solana slot (−32007, never a failure), rejected parameters, an oversized response or an incomplete request |

The archive label is always `archive_capability_unverified`: one sample never proves full archive access.

## 7. Reliability (`reliability@1.0.0`)

Reliability is computed from stored observations in the last 30 days. Only observations for which a request was sent count.

- **Attempts:** requests whose failure was not an environment restriction.
- **Excluded from attempts:** environment-blocked requests (DNS, proxy, connection failed, unsafe destination). They are counted separately, as `environment_blocked`.
- **Successes:** attempts with `reachable` connectivity and validation not failed. This includes "method not found" and "data not available", because the provider answered correctly.
- **Failures:** rate limits, timeouts, invalid responses, provider errors and auth failures, each also counted separately.

| State | Rule |
|---|---|
| `unknown` | No attempts, but ≥ 1 environment-blocked request. **Blocked never means unreliable** |
| `insufficient_data` | < 20 attempts or < 3 distinct runs |
| `observed_reliable` | ≥ 20 attempts across ≥ 3 runs, success rate ≥ 0.95, and no invalid responses |
| `observed_degraded` | Success rate ≥ 0.80 (or any invalid response) |
| `observed_unreliable` | Success rate < 0.80 |

**Latency.**
- Median needs ≥ 5 successful samples.
- p95 (nearest rank) needs ≥ 20 samples.
- Below those counts, the value is shown as "insufficient data".

**What reliability is not.**
- It is not a ranking, a star rating or a score.
- It is separate from evidence verification: a healthy provider never makes wallet evidence corroborated.

Each run also stores a snapshot in `provider_reliability_observations` for audit.

## 8. Cross-provider consistency

After a run, every pair of providers on the same chain that were reached is compared like-for-like:

- **Chain identity:** chain id or genesis hash.
- **Finalized block hash:** compared only at the *same* block number or slot.
- **Historical block hash:** compared only at the same position. Historical targets are rounded, so independent providers usually sample the same block.

| Outcome | When |
|---|---|
| `consistent` | Equal values |
| `conflict` | Different values at the same position. Both values are kept |
| `inconclusive` | Different positions (different sync points or samples). Never a conflict |

Pairs within one infrastructure group are stored with `independent = false` and a note.

The results live in `provider_consistency_checks` only. They never touch the Stage 3 `evidence_verifications` or `provider_observations` tables, so they never change any wallet evidence status.

## 9. Storage (migration v4, additive)

| Table | Contents |
|---|---|
| `provider_probe_runs` | Stable `run_key` (UUID), times, mode, data origin, selection, budget, requests used and skipped, status (`running` / `completed` / `partial` / `failed`), summary, failure summary |
| `provider_capability_observations` | Provider, chain, network, infrastructure, capability, method, status, connectivity, failure kind, environment flag, sanitized explanation, HTTP status, RPC code, latency, size, validation, block or slot reference, time. Unique per (run, provider, capability) |
| `provider_reliability_observations` | Method version, window, state, sample size, full payload |
| `provider_consistency_checks` | Pair, groups, independence, compared values, outcome, note |

**What is never stored:**
- raw provider response bodies;
- provider error message text (only BIP's classification, HTTP status and JSON-RPC code);
- endpoint URLs or keys.

## 10. Security controls

- Probe targets are registry endpoints only; the API has no field that accepts a URL (unknown body fields → 422).
- The transport's controls still apply: SSRF checks, every DNS answer validated and pinned, redirects refused, size cap, total deadline.
- The method allow-list is checked when a probe is planned and again when it is sent, and write-capable methods are rejected at import time.
- Bounded concurrency, per-provider spacing, global budgets and a single run lock.
- Configured URLs are registered for log redaction. Tests check that a configured key never appears in responses, the database or logs.
- No background tasks.
- Every run records its `data_origin` (`offline_fixture` on the fixture backend), and the UI labels fixture data.

## 11. Known limitations

- **Nothing has been probed live.** The authoring sandbox's egress gateway refuses CONNECT to the RPC hosts (403 on 2026-11-03; proxy status on 2026-11-04 shows the same policy). Every result so far comes from deterministic fakes. Run `make probe` on a machine with internet access to collect real observations.
- A few small requests say nothing about quotas, sustained throughput, or history depth beyond the samples taken.
- Thresholds (20 attempts, 3 runs, 0.95 / 0.80) are documented defaults, not calibrated against provider SLAs.
- An HTTP 403 cannot be told apart from an endpoint policy versus a key problem without provider-specific error parsing, which BIP deliberately does not store.
- The Solana `finalized_block` sample is the exact finalized slot of each provider, so cross-provider comparisons there are often `inconclusive`.
