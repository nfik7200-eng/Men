# Discovery, signals and alerts

BIP finds candidates without being given an address, **over the data it has already stored**. It
does not scan the chain: the stored data is what ingestion fetched for the wallets (and their token
accounts) it was asked to follow. A signal is a candidate for a person to review. It is not a
finding, not attribution and not a statement about intent.

## Pipeline

stored canonical evidence -> detector -> candidate -> explainable score -> deduplication and
cooldown -> signal (discovery feed) -> alert, watchlist or case

Orphaned transactions never enter a detector. If a block is later orphaned, the signals,
relationships and analyses that rested on it are invalidated in the same database transaction.

## Detectors

| Id | Family | Chains | Method | Confidence cap |
|---|---|---|---|---|
| `large_transfer_mad` | large_transfers | ethereum, solana | median/MAD robust z of log10(amount) per asset against the stored baseline | 0.8 |
| `dormant_reactivation` | wallet_activity | ethereum, solana | gap between a wallet's consecutive stored signed transactions | 0.5 |
| `activity_burst` | abnormal_frequency | ethereum, solana | median/MAD of daily signed-transaction counts on active days | 0.8 |
| `new_contract_deployment` | contracts | ethereum | `receipt.contractAddress` of stored creation transactions | 0.6 |
| `new_token_activity` | tokens | ethereum | counts of finalized ERC-20 `Transfer` logs per token | 0.35 |
| `pool_reserve_change` | liquidity | ethereum | change between stored v2-style `Sync` reserves, median/MAD when history allows | 0.5 |
| `holder_concentration` | holders | ethereum | top-3 share of positive net inflow in stored transfers (observed flow, not balances) | 0.4 |
| `solana_new_entities` | tokens, contracts, pools | solana | verified compiled-instruction layouts in finalized slots (SPL `InitializeMint`/`InitializeMint2`, upgradeable-loader `DeployWithMaxDataLen`, Raydium AMM v4 `Initialize2`, Orca `initialize_pool`) | 0.45 |
| `new_liquidity_pool` | pools | ethereum | v2/v3-style `PairCreated`/`PoolCreated` factory logs from finalized blocks | 0.4 |
| `common_funding_source` | related_wallets | ethereum, solana | one sender funding several recipients with no earlier stored activity | 0.5 |

Each detector declares its coverage, limitations, refresh frequency and cost in
`GET /api/v1/discovery/detectors`. All cost: local database reads only, no RPC, no provider credits.

Not implemented: whole-chain scanning (needs a data source and cost decision), Solana program
deployment, liquidity and pool detection, holder-concentration change, exchange attribution. These
need data BIP does not store or a provider and budget decision; see *Blocked or not built* in
`CURRENT_STATE.md`.

## Score

`score = 100 x (0.5 x magnitude + 0.3 x baseline_reliability + 0.2 x evidence_finality)`.

* `magnitude`: robust z / (3 x threshold), clamped to 0..1 (count-based detectors say how).
* `baseline_reliability`: observations in the baseline / 50, clamped.
* `evidence_finality`: share of supporting transactions that are finalized.

Severity: `high` from 65, `medium` from 40, otherwise `low`. Confidence is
`baseline_reliability x (0.5 + 0.5 x finality)`, capped per detector. Every signal stores its score
components with a note for each, so the reasoning can be read back.

## Deduplication, cooldown, expiry

* **Signal key** identifies one event (for example one transfer). Seeing it again refreshes the
  signal (`occurrences` + 1) instead of opening another.
* **Cooldown** (`cooldown_seconds`, 6 h): a *new* event for the same detector and subject merges
  into the signal opened within the window. A dismissed signal stays dismissed during it.
* **Cap**: at most `max_signals_per_detector` new signals per run; the dropped count is reported.
* **Expiry**: an open signal expires after `ttl_days` (7). Closed signals are pruned after 90 days.
* **Statuses**: `open`, `dismissed`, `promoted`, `invalidated` (evidence orphaned), `expired`.

## Alerts

Alert rules match on minimum severity, minimum score, signal families and chains. A matching *new*
signal creates one in-app alert per rule (never two for the same rule and signal). Delivery is
in-app only; e-mail, push and SMS are not configured and need an approved service.

## Measuring false positives

`GET /api/v1/discovery/performance` shows, per detector, how analysts treated its signals
(promoted, dismissed, invalidated, expired). The dismissal rate is withheld until 20 signals were
decided. It is a proxy: a dismissed signal is not proven wrong and a promoted one is not confirmed.

## Relationships

`entity_relationships` stores typed, evidence-backed relationships built from stored canonical
data: `deployed_by`, `funded_by` (first stored inbound native transfer), `transferred_to` (three or
more transfers) and `interacted_with` (Solana programs). The basis is always `observed`; a
`verified` basis is refused by the schema. "Shares a first funder with" is computed on demand and
labelled a hypothesis (confidence 0.2): exchanges, faucets and payroll produce it, and it is never
evidence of common ownership.

## Where unseen activity comes from

The detectors read stored data only. Without a collector that data is whatever wallets were
ingested. The Ethereum block collector (`POST /discovery/collect/ethereum`, admin; optional
`chain_scan` job, off) adds a bounded feed that needs no supplied address: contract creations and
native transfers of at least `collector.min_native_value_eth`, and Uniswap v2/v3-style pool-creation
events (one unfiltered `eth_getLogs` per planned range, at most `collector.max_pools_per_run` stored),
read from **finalized** blocks only. A pool signal says an event with that layout was emitted and
whether its tokens had no earlier stored pool; it does not verify a DEX, liquidity or the tokens.
`new_contract_deployment` and `large_transfer_mad` therefore see contracts and large movements
nobody asked about, and `new_liquidity_pool` sees new pools. It does not feed anything else: no
ERC-20 transfer events, internal transfers, liquidity or reserve changes or holder changes, and
nothing for Solana. `GET /discovery/collector` reports the
cursor, the limits and the last run's manifest, including how many transactions were skipped.

The collector also counts ERC-20 `Transfer` logs per token (topic filter only; the window is split
if a provider limit is hit), stores the transfers of tokens that have a known pool, and reads
`Sync` events of known v2-style pools (one address-filtered call). Everything it stores is from
finalized blocks and was checked against the canonical block hash. `holder_concentration` reports
observed net inflow inside the stored window; it never claims balances, holdings or ownership.
`pool_reserve_change` reports what a `Sync` event states, not price or value locked.

## Solana

`SolanaCollector` (`POST /discovery/collect/solana`, admin; optional `solana_scan` job, off) reads
finalized slots with `getBlock` (compiled `json` encoding, versions 0 and 1) and recognises exactly
four creation layouts, each checked against an official source on 2026-11-09 and listed with its
`source_url` by `GET /discovery/collector/solana`. Failed transactions are never decoded. The
cursor keeps the last slot and blockhash; broken blockhash continuity, skipped or unavailable
slots, slots given up after repeated failures and ranges jumped when the cursor fell too far behind
are all counted or recorded as coverage gaps. Not recognised: other pool programs (including Orca
`initialize_pool_v2`, Raydium CPMM/CLMM, Meteora, pump.fun), SPL transfers, holders and reserves.

## Attribution and reviewed labels

Every signal carries an `attribution` block. It lists labels only when a person approved them
(`/entities/labels`): a label attributes an address to an organisation or protocol, never to a
person. A proposal can come from an analyst, a signal or the local model, and none of them can
approve it. Approval needs a human reviewer, a review note of at least ten characters and at least
one evidence item a person added (`url`, stored `transaction` or stored `signal`); evidence the
model cited does not count alone. With authentication required the proposer cannot be the
reviewer. Transaction and signal evidence must exist in BIP's database. An empty `labels` object
means "unattributed", not "not an exchange". No labels ship with BIP and none are inferred.

