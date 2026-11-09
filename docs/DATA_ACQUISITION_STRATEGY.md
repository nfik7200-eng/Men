# Data Acquisition Strategy (Deliverables B and E)

Version 0.1 (2026-11-03). Status: **Draft, awaiting approval.** Provider facts are in `PROVIDER_REGISTRY.md`. The router design is in `DATA_ARCHITECTURE.md` §3.

## 1. Strategic position

1. **A full zero-cost replica of Ethereum + Solana is not feasible.** Ethereum archive nodes need roughly 2–3 TB on Erigon/Reth, and well over 10 TB on Geth (vendor/blog estimates; [reth docs](https://reth.rs/run/faq/pruning)). Solana's full ledger is "over 400 TB" ([Triton](https://blog.triton.one/inside-superbank-architecture-breakdown/)). The available VPS has only a few GB free. Free RPC quotas, measured in millions of requests per month, cannot backfill billions of objects.
2. **So BIP acquires data by scope, not by chain.** A *scope* is a declarative definition of what to collect: contracts, programs, token mints, event signatures, wallets, block/slot ranges. Each scope declares its depth (live-only, N days back, from deployment, from genesis) and its datasets.
3. **Tier the data by cost:**
   - **Tier 0, chain-wide and cheap:** block headers and slot metadata for continuity and reorg detection; chain-wide ERC-20 `Transfer` logs above size thresholds, where quota allows.
   - **Tier 1, scoped and complete:** everything for in-scope contracts, programs and wallets.
   - **Tier 2, on-demand:** an investigation pulls a wallet's history when it is needed, then caches it.
4. **Use each source for what it does best.** RPC for live data and precise lookups. BigQuery for narrow, partition-pruned historical slices. Explorer APIs for deployer and verified-source metadata. Indexers (The Graph, DefiLlama) for protocol aggregates that would be expensive to rebuild.
5. **Treat subscriptions as notifications, not records.** WebSocket events trigger fetches. Canonical data always comes from a fetch plus validation.

## 2. Source-to-data matrix

| Data | Ethereum primary | Ethereum fallback / verify | Solana primary | Solana fallback / verify |
|---|---|---|---|---|
| Head / new blocks, slots | WS `newHeads` (≥2 providers) | HTTP polling `eth_blockNumber` | WS `slotSubscribe` / `blockSubscribe`* | `getSlot` polling with `finalized` commitment |
| Blocks + txs | `eth_getBlockByNumber(full)` | 2nd provider hash compare | `getBlock` (`maxSupportedTransactionVersion: 1`) | 2nd provider; Old Faithful for old epochs |
| Receipts / logs | `eth_getBlockReceipts`* or `eth_getLogs` ranges | `eth_getTransactionReceipt` sampling | tx meta (logMessages, inner instructions) | — |
| Traces / internal txs | Not on free dRPC; BigQuery `traces` (narrow) | Providers with free `trace_*`/`debug_*` (probe) | inner instructions in tx meta | — |
| Contract deployments | receipts `contractAddress` (top-level) + Etherscan `getcontractcreation` + BigQuery `contracts` | traces | BPF Loader Upgradeable program instructions | — |
| Token transfers | `Transfer` logs (ERC-20/721), `TransferSingle/Batch` (1155) | BigQuery `token_transfers` | SPL Token / Token-2022 instructions + pre/post token balances | — |
| Historical by address | Etherscan `txlist`/`tokentx` (on-demand) | BigQuery narrow query | `getSignaturesForAddress` paging + `getTransaction` | Old Faithful |
| Historical balances | `eth_getBalance(blockTag)` on archive providers (Chainstack archive, probed) | derive from transfers (flagged `derived`) | pre/post balances in tx meta (point-in-time per tx) | — |
| DEX swaps / liquidity | decode pool logs (Uniswap V2/V3/V4, Curve, …) | The Graph subgraph aggregates | decode Raydium/Orca/Meteora/Jupiter instructions | indexer APIs |
| TVL / fees / revenue | DefiLlama | — | DefiLlama | — |
| Prices | DefiLlama coins API; DEX-derived | — | DefiLlama; DEX-derived | — |
| Developer data | GitHub REST/GraphQL; Etherscan verified source | — | GitHub; program IDs in repos/docs; on-chain IDL accounts | — |

`*` Support differs between providers. The capability probe must confirm it.

**Historical transactions vs. historical state.** Transaction and log history (what happened) can be obtained from logs, BigQuery and Old Faithful. Historical account state (balance or storage at a past block) needs archive-node RPC or reconstruction from the full event history. Reconstruction is only valid when that history is complete for the asset. BIP labels every balance as `observed_at_block` (from an archive call), `derived_complete` (from complete transfer history, coverage-proven) or `derived_partial` (never shown as a fact).

## 3. Web-intelligence acquisition

Order of preference: **official API, then RSS/Atom/JSON feed, then downloadable dataset, then robots-compliant HTML fetch.**

- **GitHub:** REST/GraphQL with a PAT. Conditional requests (ETag) avoid spending quota on unchanged resources. Track repos linked to watched projects: commits, contributors, releases, and contract addresses mentioned in code or READMEs.
- **Governance:** Discourse `.json`/`.rss` endpoints and the Snapshot GraphQL hub.
- **Announcements:** official exchange and project RSS or blog feeds where they exist.
- **Fetcher architecture:** a single `web_fetcher` service. It enforces a robots.txt cache (refreshed daily), a per-domain token bucket (default 1 request per 10 s, configurable down only), conditional GET, a content-hash dedup store, change detection (normalized-text diff), retries with backoff on 429/5xx only, provenance (URL, fetched_at, status, content hash, robots decision), and a per-domain ToS allow-list maintained by a human. Playwright is used only for domains that are explicitly allow-listed and need JavaScript rendering, never to bypass protections. Scrapy is not adopted at first; a thin httpx fetcher is enough until crawl breadth justifies it (ADR-010).
- **Never:** solve CAPTCHAs, rotate IPs to evade limits, use logged-in sessions to scrape, or ignore `Disallow`.

## 4. Historical ingestion — Ethereum

### 4.1 Unit of work
`RangeTask(chain=ethereum, dataset, scope_id, start_block, end_block)`. Default range: 1,000 blocks for blocks/txs; adaptive for `eth_getLogs`, shrinking on "range too large / too many results" errors, using a per-provider `max_logs_block_range` learned by the probe.

### 4.2 Workflow
```
plan_ranges(scope) -> range_tasks (state=PENDING) in Postgres
loop:
  lease task (SELECT ... FOR UPDATE SKIP LOCKED, lease_expires_at)
  choose source: BigQuery slice | RPC via router
  fetch -> raw payloads
  validate:
    - every block number in [start,end] present exactly once
    - parentHash chain continuity inside range and to neighbours (if known)
    - tx count per block == len(transactions)
    - receipts: one per tx; logs: logIndex contiguous per block; blockHash matches
    - eth_getLogs: cross-check count vs. a 2nd provider on sampled sub-ranges
  write Bronze Parquet to staging path -> fsync -> atomic rename to final path
  in one Postgres transaction: mark task DONE, store file path, row counts, content hash, provider(s), validation report
  on failure: attempts++, backoff, maybe split range, maybe switch source; after N attempts -> FAILED + coverage gap
```
Commit order: the file is durable first, then the checkpoint. A crash between the two leaves an orphan file, which the janitor deletes using the content-hash manifest. It never produces a checkpoint without its file.

### 4.3 Finality and reorgs
- Historical ranges are only planned up to the **finalized** block (`eth_getBlockByNumber("finalized")`, from at least 2 providers, taking the minimum).
- Data above finalized belongs to the live system.

### 4.4 Completeness and reconciliation
- A **coverage map** per (chain, dataset, scope) is stored as interval sets with status `verified | single_source | failed | missing`.
- **Gap scanner:** compares the coverage map against the target interval and re-plans missing parts.
- **Cross-provider verification:** about 1% of ranges, plus every range from a provider with a low quality score, are re-fetched from a different provider. Block hashes, tx hashes and log counts are compared. A mismatch causes quarantine, a third-source tiebreak, a provider quality penalty, and an incident record.
- **Pre-Merge (EIP-4444):** before scheduling RPC fetches below block 15,537,394, the router checks each provider's `history_floor_block`. If no provider serves the range, the task is routed to BigQuery, or marked `missing (source_unavailable)`.

## 5. Historical ingestion — Solana

### 5.1 Unit of work
Either `SlotRangeTask(start_slot, end_slot)`, used for chain-wide metadata or for Old Faithful epochs (an epoch is 432,000 slots), or `AddressHistoryTask(address, before_signature, until_signature)`, used for scoped programs and accounts. Scoped address history is the default for Solana.

### 5.2 Workflow — scoped address history
```
getSignaturesForAddress(address, before=cursor, limit=1000, commitment=finalized)
  -> persist signature page (Bronze) + cursor checkpoint
for each signature batch: getTransaction(sig, maxSupportedTransactionVersion=1, commitment=finalized, encoding=json)
validate: signature matches; slot <= finalized; meta present; err field recorded (failed txs are kept, flagged)
write Bronze; mark signatures fetched; cursor advances only after durable write
stop when: page shorter than limit AND oldest signature reached (end of history), or until_signature reached
```
The checkpoint is the oldest fully processed signature together with its slot. Paging is resumable.

### 5.3 Slot semantics
- **Skipped slots:** `getBlock` returns an error for skipped slots. These are recorded explicitly as `skipped` (confirmed by `getBlocks(start,end)` from ≥2 providers), never as missing.
- **Versions:** always request `maxSupportedTransactionVersion: 1` (v1 is live on mainnet since epoch 1035, 2026-09-15; a lower value fails on v1 transactions with -32015). Store `version` (legacy/0/1) and resolve address lookup tables from `meta.loadedAddresses` (v0 only).
- **Commitment:** historical ingestion uses `finalized` only.
- **Long-term storage gaps:** RPC providers may not serve old slots. On `-32007`/`-32009`-style "slot not available / cleaned up" errors, the task is re-routed to an Old Faithful-backed source or marked `missing (source_unavailable)`. (Exact error codes must be confirmed against Solana RPC docs during Stage 8.)

### 5.4 Old Faithful usage (requires approved separate storage)
Download one epoch's CAR file into a temporary area on separate storage. Stream-parse it, extract only in-scope transactions into Bronze Parquet, verify counts against the epoch index, then delete the CAR. Never keep raw CAR files on the VPS.

## 6. Live ingestion — Ethereum

```
WS newHeads from >=2 providers (notification only)
 + HTTP poll every ~12s as heartbeat
on head h:
  fetch block h (full) + receipts via router; validate
  append to live buffer keyed by (number, hash)
  if parentHash != stored hash of h-1 -> REORG:
     walk back until common ancestor (bounded depth, e.g. 64)
     mark orphaned blocks/records status=orphaned (never delete; keep provenance)
     re-fetch canonical branch; emit correction events downstream
  persist checkpoint {last_processed_number, hash} transactionally with writes
finality job (every epoch ~6.4 min): blocks <= finalized -> status=finalized; flush to Bronze partition
on reconnect/restart: from checkpoint to current head -> backfill via HTTP (bounded catch-up queue)
```
Event dedup key: `(chain, block_hash, tx_hash, log_index)`. Downstream consumers receive `observed`, then `finalized` or `orphaned` status changes, so alerts can say "unconfirmed" early and confirm later.

## 7. Live ingestion — Solana

```
slotSubscribe / blockSubscribe (if provider supports) or logsSubscribe(mentions: program) for scoped programs
 + getSlot(commitment=confirmed) poll as heartbeat
for scoped programs: logsSubscribe -> signature -> getTransaction(confirmed) -> provisional record
finalization: periodically getSignaturesForAddress(until=last_finalized_sig, commitment=finalized)
   -> authoritative list; reconcile: provisional records not in finalized list after timeout -> dropped
checkpoint per scope: last finalized signature + slot
on reconnect: page getSignaturesForAddress from checkpoint forward (until=checkpoint) -> no gaps
```
Dedup key: `(chain, signature)`, plus `instruction_index` and `inner_index` for instruction-level records.

## 8. Prioritisation

The router's request queue has priority classes:
1. **P0 live head and finality checks**
2. **P1 live scoped fetches** (watchlists)
3. **P2 investigation on-demand**
4. **P3 verification re-fetches**
5. **P4 historical backfill**

Backfill uses only the leftover quota budget. P4 pauses automatically when a provider's monthly burn rate is on track to exceed 70% of its quota before the reset date.

## 9. Bandwidth and quota budgeting (estimates, explicit assumptions)

Assumptions (unverified, to be replaced by measurements in Stage 4):
- Ethereum: ~7,200 blocks/day (12 s slots).
- A full block with txs plus receipts is ~100–300 KB of JSON (assumption).
- Solana `getTransaction` JSON is ~2–5 KB (assumption).

| Activity | Requests/day | Raw JSON/day |
|---|---|---|
| ETH live: blocks + block receipts (1 + 1 call/block) | ~14,400 | ~0.7–2 GB |
| ETH chain-wide ERC-20 `Transfer` logs via `eth_getLogs` (per 10-block window) | ~720 | included above if receipts are used |
| SOL scoped: 50 programs/accounts × ~2,000 tx/day | ~100,000 `getTransaction` + ~100 signature pages | ~0.2–0.5 GB |

Conclusion: ETH live at full-block granularity is about 15k requests/day, which fits comfortably across 4–6 providers. Solana chain-wide is not feasible on free tiers: about 86M non-vote transactions/day at ~1,000 TPS, versus Helius at 1M credits/month. Solana must be scoped.
