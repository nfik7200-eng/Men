# Intelligence Methodology (Stage 3)

Version 1.0 (2026-11-03). Analyzer version `3.0.0`. Applies to `bip/intel/`.

This document states exactly how BIP turns stored observations into profiles, rules, anomalies, flows, graphs, research leads and findings. Every rule here is implemented as written, and the tests in `tests/stage3/` exercise them.

## 1. Principles

1. **Scope, not lifetime.** Every number describes the *retrieved dataset*: the records BIP's bounded ingestion runs stored for that wallet. Labels say so ("15 transactions observed in retrieved dataset"). BIP never claims lifetime totals.
2. **Three kinds of statement** (CLAUDE.md §2.12):
   - **verified observation**: a stored record whose evidence is corroborated by independent providers;
   - **statistical signal**: a computed metric with a stated method, window and threshold;
   - **hypothesis**: an unconfirmed explanation, always labelled as such ("Possible … pattern").
3. **No identity, motive or ownership.** Counterparties are "observed transfer parties". Graph edges are observed interactions. Nothing infers common ownership, control or a real-world operator.
4. **Missing is not zero.** Unknown decimals stay in raw units and are never assumed. A missing timestamp makes a time-based detector `could_not_run`, not "0 activity". No USD values exist, because no verified price source is configured.
5. **Deterministic and reproducible.** Same stored inputs + same parameters + same analyzer version give the same output. Analysis never calls the network.

## 2. Data used

`bip/intel/dataset.py::load_wallet` reads one wallet's stored rows:

- **transactions** where the wallet is sender or recipient, linked via `wallet_activity`, a party to a stored transfer, or a party to a balance change;
- **transfers** where the wallet (or a token account it owns, per balance metadata) is sender or recipient;
- **balance changes** (Solana) for the wallet or its token accounts.

It loads at most `BIP_INTEL__MAX_RECORDS_PER_WALLET` (default 5,000) rows of each kind, newest first. Beyond that, `scope.truncated = true` and a limitation is added.

Solana token transfers name token *accounts*. A counterparty token account is shown as its owner only when stored balance metadata names exactly one owner for it (`resolved_from_token_account`). Otherwise it stays as the raw account.

## 3. Verification model

States, stored per piece of evidence (per transaction), never per profile:

| Stored value | Shown as | Meaning |
|---|---|---|
| `unverified` / `single_source` | unverified | One provider's observation, not cross-checked |
| `cross_verified` | **corroborated** | ≥ 2 independent infrastructure groups report identical normalized fields |
| `conflict` | **conflicted** | An independent provider reports different data. Both versions are kept |
| `invalidated` | invalidated | ≥ 2 independent providers agree on a *different* block hash (reorg) |

**Independence.** Each RPC provider in `bip/providers/registry.toml` declares an `infrastructure` group. Two providers are independent only when their groups differ (`registry.independent`). For example, `publicnode-eth` and `publicnode-sol` are both `allnodes`, so they never corroborate each other.

**Procedure** (`bip/intel/verify.py`, `POST /intelligence/verification/{chain}/{tx}`):

1. Load the stored record and its source provider.
2. Select up to two eligible providers whose groups differ from the source's group and from each other.
3. Fetch the same transaction from each: `eth_getTransactionByHash` + `eth_getTransactionReceipt`, or Solana `getTransaction` at `finalized`. Each fetch is budgeted at 12 calls / 30 s in total.
4. Normalize and compare these fields:
   - Ethereum: block number, block hash, status, from, to, value, created contract, and each stored transfer (by `log:N` / `native` key: token, from, to, amount).
   - Solana: slot, status, fee payer, fee, block time, and each stored instruction transfer (by `ix:` path).
   - Fields unknown on either side are not compared. Extra provider transfers that BIP never stored are not conflicts.
5. Record every answer in `provider_observations` and the outcome in `evidence_verifications`. The latest row is the effective status. Stored source records are **never** modified. Failed lookups are stored as `{"lookup_failed": true, "error": "<exception class> (<BIP error class>)"}`: provider-supplied message text is never stored or returned, because it could echo URLs or keys.
6. One verification runs at a time (409 `verification_busy`). A wallet request checks at most 20 transactions within 60 s and stops at the first `could_not_run` caused by having no eligible independent provider.

**Outcome rules** (`_decide`):

| Condition | Outcome |
|---|---|
| No independent provider eligible, or none gave a usable answer (transport/RPC error, or an answer BIP cannot parse) | `could_not_run`: **nothing recorded in `evidence_verifications`**, status unchanged (the failed lookups are still listed in the response) |
| All usable answers are "not found" | `unverified`: provider history depth is not established by any capability record, so absence is neither a conflict nor an invalidation |
| Any found answer differs | `conflict`, unless ≥ 2 found answers agree on one *other* block hash: `invalidated` |
| All found answers match, and independent groups (matching providers + the source's group, if declared) ≥ 2 | `cross_verified` |
| Otherwise | `unverified` |

A failed or blocked request is never converted into corroboration.

## 4. Wallet profile (`profile.py`)

- **Scope:** observed range, record counts, ingestion runs, truncation.
- **Metrics:** transactions, transfers in and out, distinct counterparties, distinct assets, active days, distinct call targets / programs, deployments, failed transactions. Every metric is labelled "… in retrieved dataset".
- **Volumes per asset:** in, out and net in raw units. Scaled values are given only when decimals are known. ERC-721 is counted, not summed.
- **Counterparties:** transfers, in and out counts, assets, first and last seen, and a verification summary.
- **Interactions:**
  - Ethereum: `to` of transactions the wallet sent, typed as "token contract" (seen as a token in transfers), "contract deployed by this wallet", or "address (contract status not checked)". BIP does not call `eth_getCode`.
  - Solana: invoked program ids. Names are given only for protocol-defined ids (System, SPL Token, Token-2022, Associated Token, Compute Budget). Everything else stays unnamed. Token metadata is never treated as proof of legitimacy.
- **Deployments:** stored `receipt.contractAddress` for creation transactions the wallet sent (migration v3 column `created_contract`).
- **Activity:** daily counts of transactions and transfers.
- **Coverage:** cursor, partial parses, missing timestamps, unknown instructions, discovery method.
- **Verification coverage and limitations.**

## 5. Behaviour rules (`behaviour.py`, version 1.0.0)

Each rule returns `rule_id`, `inputs`, `window`, `magnitude`, supporting transactions/transfers (the first 20 listed; the total always counted), `confidence`, `verification` and `explanation`. Each rule has one of three statuses: `detected`, `not_detected` or `not_enough_data`.

| Rule | Minimum data | Detected when |
|---|---|---|
| `high_frequency` | 10 timestamped tx | ≥ 10 tx per active day |
| `repeated_interactions` | ≥ 1 outgoing call / program | One target ≥ 5 times |
| `repeated_counterparties` | ≥ 1 counterparty transfer | Some counterparty in ≥ 5 transfers |
| `concentration` | 10 counterparty transfers | Top counterparty share ≥ `concentration_share` (0.5) |
| `bursts` | `burst_count` tx | ≥ `burst_count` (5) tx within `burst_window_minutes` (10); non-overlapping |
| `newly_observed` | 1 timestamped record | Earliest retrieved record within `newly_observed_days` (7). This is a first *observation*, not a creation date |
| `repeated_token_transfers` | 1 token transfer | One token in ≥ 5 transfers |
| `large_value_changes` | 5 non-zero transfers of an asset | Transfer ≥ 10 × that asset's median (raw units) |
| `service_like_pattern` | 10 counterparties | ≥ 25 distinct senders **and** ≥ 25 distinct recipients. **Hypothesis**: "Possible exchange-like or service-like activity pattern"; never an identification |

## 6. Anomaly detectors (`anomaly.py`)

Every detector publishes: id, version, window, minimum data, baseline, thresholds, severity rule and limitations (`GET /intelligence/status` lists them). Each one has one of four statuses:

- `insufficient_data`: the minimum was not met, and nothing is claimed;
- `no_anomaly`: the detector ran and nothing crossed its threshold;
- `anomaly`: a threshold was crossed, with evidence attached;
- `could_not_run`: a required input is missing, e.g. every timestamp.

**Robust statistics.** Detectors use the median and MAD. The scaled MAD is 1.4826 × MAD.

**Confidence** = `min(1, n/30) × (0.5 + 0.5 × corroborated share)`, halved if any cited evidence is conflicted:

- uncorroborated evidence caps confidence at 0.5;
- labels are low (< 0.34), medium (< 0.67) and high.

**Severity** is never `high` when fewer than 30 observations back the baseline. A `high` from a small sample is downgraded to `medium`.

| Detector | Minimum data | Baseline | Threshold | Severity |
|---|---|---|---|---|
| `tx_frequency_spike` 1.0.0 | 8 days spanned, 10 tx | Daily counts before the last day | Last day ≥ max(5, `spike_factor` × median, median + 6 × scaled MAD) | low; medium ≥ 2×; high ≥ 3× (n ≥ 30) |
| `large_transfer` 1.0.0 | 10 earlier non-zero transfers of the asset | log10 amounts of earlier transfers | Robust z ≥ `large_transfer_mad_z` (6); if MAD = 0, amount ≥ 10 × median | low; medium ≥ 1.5× z; high ≥ 2× z (n ≥ 30) |
| `new_counterparty_surge` 1.0.0 | 8 days, 10 transfers | First-seen counterparties per day | Last day ≥ max(3, `spike_factor` × median, median + 6 × scaled MAD) | as the spike detector |
| `rapid_repeat_transfers` 1.0.0 | `burst_count` transfers | None (absolute rule) | ≥ `burst_count` same counterparty + asset + direction in `burst_window_minutes` | low; medium ≥ 2×; high ≥ 4× (n ≥ 30) |
| `new_wallet_new_contract` 1.0.0 | 1 timestamped tx | First appearance of each target in BIP's whole database | Wallet and target both first observed within `newly_observed_days` | always low |

**Reference time.** Detectors run against `as_of`, the end of the current UTC day. It is part of the parameters, so results are stable within a day.

## 7. Capital flows (`flows.py`)

- **Canonical source:** the `transfers` table only.
  - Solana balance changes are not added, because they describe the same movements and would double count.
  - Self transfers are counted separately and excluded from in and out.
- **Output:**
  - per asset: in, out and net in raw units (scaled where decimals are known);
  - counterparties per asset;
  - daily buckets;
  - largest transfers per asset (never compared across assets);
  - repeated `(from, to, asset)` paths (≥ 2);
  - verification summary;
  - limitations.
- **Window:** optional `since`/`until` (timezone required). The window is capped at `flow_max_window_days` (365).
- **No USD.** `usd_values` is always `null`.

## 8. Relationship graph (`graph.py`, version 1.0.0)

- **Computation.** The graph is computed on demand from `transfers` and `transactions`. There is no graph database (ADR-022).
- **Nodes:**
  - `address`: type not established;
  - `contract`: deployed in a stored transaction;
  - `token`: an ERC-20/721 contract or SPL mint seen in transfers;
  - `program`: a Solana program.
  - There are no `protocol` nodes: protocols cannot be identified reliably yet.
- **Edges** are aggregated per (source, target, kind, asset). Kinds: `native_transfer`, `token_transfer`, `token_activity`, `called`, `invoked`, `deployed`.
  - Each edge keeps: count, summed raw amount (none for ERC-721), first and last seen, verification summary, and up to 10 evidence items.
  - Each evidence item carries: tx, block/slot, time, asset, amount, provider, verification.
- **Traversal:**
  - Breadth-first from the wallet. Only address and contract nodes are expanded; token and program nodes are not (they would explode the graph).
  - Bounds: depth ≤ `graph_max_depth` (2; ≤ 3 via the API), nodes ≤ `graph_max_nodes` (200), edges ≤ `graph_max_edges` (500), deadline `graph_timeout_s` (5 s).
  - Each stored row becomes an edge observation once, even when a deeper level reads it again.
  - `truncation` reports which bound applied: node limit, edge limit, per-level row limit (2 × max edges), timeout, or an unexpanded frontier.
- **Ownership:** never inferred. The response carries that statement, and the UI shows it.

## 9. Research leads (`opportunities.py`)

Rule `rising_participation` 1.0.0, per chain and subject. A subject is a token, a Solana program (protocol-defined programs excluded) or an EVM call target.

- **Windows.** Anchored at the latest stored event time T, not the wall clock: recent = (T − 7 d, T], prior = (T − 14 d, T − 7 d].
- **Minimum data.**
  - At least 3 ingested wallets on the chain.
  - For a subject: ≥ 3 distinct recent participants and ≥ 10 recent events (one event = one stored transfer or transaction, not one per participant).
  - Otherwise BIP returns **no candidate**, with status `insufficient_data`.
- **Candidate when** recent participants ≥ 2 × max(prior participants, 1).
- **Score** in 0..1 is the sum of transparent contributions:

  | Feature | Weight | Feature value |
  |---|---|---|
  | `participant_growth` | 0.4 | min(log2(growth) / 3, 1) |
  | `breadth` | 0.3 | min(recent participants / 10, 1) |
  | `activity` | 0.2 | min(recent events / 50, 1) |
  | `corroboration` | 0.1 | corroborated share of evidence |

- **Labels.** A lead is "research lead" only when every evidence transaction is corroborated. Otherwise it is "**unverified research lead**".
- **Disclosures.** Each lead lists its uncertainty: selection bias (only ingested wallets), fragile growth from small counts, and "rising activity is not evidence of quality, legitimacy or returns".
- **Never advice.** Leads are never buy/sell advice; every response says so.
- **Persistence.** `candidate_key` = sha256(rule | kind | chain | subject):
  - a refresh updates the lead instead of duplicating it;
  - a lead that no longer meets the rule is kept with `current: false`.

## 10. Analysis runs and findings (`service.py`)

- **Input hash.** `POST /intelligence/analyze/{chain}/{address}` computes `input_hash` = sha256 over:
  - every loaded record's identity;
  - its effective verification;
  - the parameters (all `BIP_INTEL__*` settings, `as_of` date, analyzer version).
- **Reuse.** If a succeeded run with the same hash and analyzer version exists, it is returned (`reused: true`). Corroborating evidence changes the hash.
- **Stored records:**
  - one `analysis_runs` row;
  - one `anomaly_observations` row per detector, for every status, so "could not run" stays visible;
  - a `Finding` per anomaly, and per detected hypothesis rule.
- **Findings** use the existing contract (`bip/contracts/findings.py`):
  - `statistical_observation` claims carry the detector method and threshold;
  - hypothesis rules give `hypothesis` claims;
  - a `verified_fact` claim is added only for supporting transactions that are corroborated;
  - limitations are always listed; `produced_by = detector@version`; the observation window is included.
- **Finding ids.** `finding_id` = `bip.<detector>.<sha256(detector | chain | address | supporting tx ids)[:24]>`. The same evidence never produces a second finding. When it is recomputed (for example after corroboration), the existing finding's derived content (status, confidence, evidence verification) is refreshed in place, and its investigation link is kept.
- **Investigations and watchlists:**
  - attach a finding with `POST /intelligence/findings/{id}/attach`;
  - filter anomalies by watchlist with `GET /intelligence/anomalies?watchlist_id=`;
  - analyse a watchlist (≤ 50 entries) with `POST /intelligence/watchlists/{id}/analyze`.

## 11. Data origin

- **Fixture backend.** The offline fixture backend (`tests/stage2/serve_offline.py`) sets `app_metadata.data_origin = offline_fixture` in its database.
- **Labelling.** Every intelligence response carries `data_origin`, and the UI shows a "Demo data: offline fixtures" banner for it. Fixture data and real data are never mixed silently.

## 12. Known limitations

- Ethereum discovery sees only Transfer-log activity in the scanned window (ADR-020). Plain ETH transfers and internal transfers can be missing, so frequency and counterparty metrics undercount.
- Thresholds are initial, documented defaults. They are not calibrated against labelled data, and none exists in BIP.
- `first_seen_targets` (new-contract detector) reflects BIP's own database, not chain history.
- Graph and flows are computed per request. That is fine up to the record limits above, but it is not designed for large neighbourhoods (Parquet/DuckDB per ADR-022 once measured).
