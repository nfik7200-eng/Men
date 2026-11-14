# ML Wallet Classification (Stage 5)

Version 1.0 (2026-11-04). Code: `bip/ml/`. Taxonomy 1.0.0, features 1.0.0, rules `bip-rules` 1.0.0.

> **Honest status.** BIP has no real, provenance-backed labelled wallets yet. So **no real model has been trained** and there are no real-world accuracy figures. What exists:
> - a working training, evaluation and calibration pipeline;
> - a deterministic, evidence-backed baseline (`bip-rules`);
> - a synthetic dataset that exercises the pipeline end to end.
>
> Models trained on synthetic data are registered as `experimental_synthetic`. Their metrics describe how well the pipeline recovers the generator's own rules, not real-world performance.

## 1. Architecture

```
stored observations ──► features (versioned, as-of, availability) ──► bip-rules (always)
                                                     │                       │
                                                     └─► active model ───────┤
                                                         (validated          ▼
                                                          thresholds)  ClassificationResult
                                                                       (validated, persisted)
                                                                             │
                                                                             ▼
                                                      AI research tool `wallet_classification`
```

The classification engine has no dependency on any LLM, so it keeps working when no AI provider is configured.

## 2. Taxonomy (`bip/ml/taxonomy.py`)

There are two dimensions:

- **Account type:** exactly one label, possibly `unknown_account_type`.
- **Behavioural roles:** zero or more labels, so a wallet can carry several roles at once.

**Account types:**
- `externally_owned_account`
- `smart_contract`
- `token_contract`
- `defi_protocol_contract`
- `bridge_contract`
- `multisig_account`
- `solana_program`
- `token_account`
- `wallet_account`
- `unknown_account_type`

**Behavioural roles:**
- Exchange-like: `exchange_hot_wallet`, `exchange_cold_wallet`, `exchange_deposit_wallet`
- Trading: `defi_trader`, `high_frequency_activity`, `arbitrage_participant`, `mev_participant`, `market_maker`, `liquidity_provider`, `short_term_trader`
- Holding and value: `high_value_participant`, `long_term_holder`
- Automation: `automated_bot`
- Organisations: `treasury_wallet`, `dao_related`, `protocol_operator`, `fund_or_institutional_treasury`
- Deployment: `token_or_nft_deployer`, `contract_deployer`
- Infrastructure: `bridge_related`, `staking_participant`, `validator_related`, `miner_related`
- Other: `low_activity_or_new`, `potentially_malicious_behaviour`

Labels describe behaviour only. Five labels are flagged **sensitive**: the exchange-wallet, fund-treasury and potentially-malicious labels. They are always reported with this caveat: *"Behavioural resemblance only: no operator, owner or intent is established."* No label identifies a person or organisation.

## 3. Features (`bip/ml/features.py`, version 1.0.0)

There are 27 features in a fixed order.

| Group | Features |
|---|---|
| Activity | `tx_count`, `active_days`, `span_days`, `tx_per_active_day` |
| Transfers and counterparties | `in_transfer_count`, `out_transfer_count`, `in_share`, `distinct_counterparties`, `counterparty_top_share`, `counterparty_hhi`, `distinct_assets`, `token_transfer_share`, `nft_transfer_share`, `repeated_amount_share`, `self_transfer_share` |
| Outcomes | `failed_tx_rate` |
| Timing | `burst_max_10min`, `median_inter_tx_s`, `inter_tx_cv`, `hour_entropy`, `weekend_share`, `days_since_first_seen`, `recent_activity_ratio` |
| Contracts and programs | `deployments`, `distinct_call_targets`, `call_target_top_share`, `unknown_instruction_share` (Solana) |

- **Leakage control.** Only records with a block time at or before `as_of` are used. Training uses each label's `observed_at` as `as_of`. Records without a block time are excluded and counted (`coverage.untimed_records`). This is why a wallet page can list one more transaction than the classification counts.
- **Missing is not zero.** Every feature carries `availability` (`observed` / `unavailable`). An unavailable value is `None`, never 0.
- **How models see missing values.** Each feature becomes two inputs: the value with the training median imputed, and a missing-indicator flag. A model can therefore tell "unavailable" from a real value, and from zero.
- **Coverage.** Each vector records transactions, transfers, counterparties, the observation period, untimed and future records, the record-limit flag and a completeness label. Ethereum is always `partial`, because log-based discovery misses some activity.
- **Snapshots** are stored in `wallet_features` (deduplicated by digest).

Features BIP cannot compute reliably today are not invented. That includes swaps, liquidity provision, holding periods, buy/sell legs, MEV sequences and exchange interactions, because there is no decoder for them and no attributable exchange list.

## 4. Deterministic baseline (`bip/ml/rules.py`)

Rules cite stored records. They carry `status = rule_based` and **no probability**, because their precision has not been measured.

**Account type:**
- **Ethereum:**
  - `smart_contract`: the address is a receipt `contractAddress`.
  - `token_contract`: it emitted Transfer logs.
  - `externally_owned_account`: it sent a transaction.
- **Solana:**
  - `solana_program`: it was invoked as a program.
  - `token_account`: balance metadata records an owner for it.
  - `wallet_account`: it paid a transaction fee.

**Roles:**
- `contract_deployer`
- `token_or_nft_deployer`: a deployed contract later emitted Transfer logs.
- `high_frequency_activity`: ≥ 10 transactions and ≥ 10 per active day.
- `low_activity_or_new`: < 5 transactions, or first observed ≤ 7 days ago. This is a first *observation*, not a creation date.

## 5. Labels (`bip/ml/labels.py`)

| Status | Meaning | Used for training |
|---|---|---|
| `verified` | Strongly attributed by an independently verifiable public source | Yes |
| `corroborated` | Two or more independent sources | Yes |
| `analyst_reviewed` | Accepted by a reviewer | Yes |
| `inferred` | Derived by a deterministic rule | Only after an `accepted` review |
| `weak` | Noisy or heuristic | Only after an `accepted` review |
| `unknown` | — | No |

**Each label records:**
- `source` and `source_ref`;
- `observed_at`, the time from which the label applies;
- `value`: true, or false for a confirmed negative;
- `data_kind = real`. Importers cannot set it; only the synthetic generator produces `synthetic`.

**Reviews are append-only** (`wallet_label_reviews`):
- A `rejected` review excludes the label from training.
- The original label row is never edited.

**No self-training.** Model predictions are **never** written as labels, and nothing retrains automatically.

**Explicit examples only.** A wallet is an example for a label only if that label was stated for it. Absence is never treated as a negative.

## 6. Training (`bip/ml/training.py`, `bip/ml/backend.py`)

1. **Data gate.**
   - Real labels need ≥ `BIP_ML__MIN_LABELLED_WALLETS` (default 30) wallet snapshots.
   - Each modelled label needs ≥ `BIP_ML__MIN_POSITIVES_PER_LABEL` (default 5) positives **and** negatives in the training split.
   - Otherwise the run is recorded as `insufficient_data` and no model is produced.
2. **Dataset version.** sha256 over every feature digest and every (wallet, label, value, status, source).
3. **Out-of-time split.** Examples are ordered by `as_of`: train 60 %, validation 20 %, test 20 %.
4. **Model per label.** `RandomForestClassifier`:
   - 150 trees, depth 8, min leaf 2;
   - `class_weight = balanced_subsample` (handles imbalance);
   - fixed seed;
   - `n_jobs = 1` (CPU only, small).
5. **Calibration.** Sigmoid (Platt) on the validation split, only when it has ≥ 10 positives and ≥ 10 negatives. Otherwise the label is "uncalibrated".
6. **Thresholds** are chosen on validation data and never below 0.5:
   - `high` is the lowest score whose validation precision is ≥ `high_precision_target` (0.95), with ≥ `min_threshold_support` (10) predictions at or above it;
   - `moderate` is the same with a target of 0.80;
   - uncalibrated labels get neither.
7. **Unsupervised parts.**
   - An Isolation Forest is fitted on training features (≥ 20 rows) to give behavioural-outlier scores with a training percentile.
   - An HDBSCAN cluster summary is computed (informational; ≥ 20 rows).
8. **Evaluation** is stored per split and label in `ml_evaluation_results`:
   - precision, recall and F1 at 0.5;
   - the confusion matrix, FPR and FNR;
   - Brier score and reliability bins;
   - precision and recall at the chosen high and moderate thresholds.
9. **Registration.** Artifacts are written to `<data_root>/ml/models/<name>/<version>.joblib`, capped at `BIP_ML__MAX_ARTIFACT_BYTES`, with a sha256 recorded in `ml_models`.
   - They are loaded only from inside that directory, and only when the hash matches.
   - joblib is pickle, so files from anywhere else are never loaded.
   - Model status is `trained` (real labels) or `experimental_synthetic`.

**Test-split precision is reported at the thresholds chosen on validation data**, so any optimism in threshold selection is visible.

**Not used:**
- LightGBM, XGBoost, CatBoost: heavier dependencies, with no labelled data to show they would add value.
- Graph and temporal neural models: same reason.

## 7. Classification statuses and abstention (`bip/ml/service.py`)

| Status | Rule |
|---|---|
| `high_confidence` | Calibrated score ≥ the validated high threshold |
| `moderate_confidence` | Calibrated score ≥ the validated moderate threshold |
| `low_confidence` | Score ≥ 0.5 without a validated threshold (always the case for uncalibrated models) |
| `rule_based` | Deterministic rule match (no probability) |
| `insufficient_evidence` | No basis for the label |

**Abstention.** Model roles are withheld when fewer than `BIP_ML__MIN_TX_FOR_CLASSIFICATION` (3) transactions are observed. Labels below 0.5 are listed in `not_assigned` with their scores, so near-misses stay visible.

**Overall status:**
- `insufficient_evidence`: no stored observations.
- `classified_with_limitations`: few observations, no trained model, a synthetic model, or contradictions.
- `classified`: otherwise.

**Contradictions** between rules and model are kept side by side; neither is preferred automatically. For example, the rule says `low_activity_or_new` while the model says `high_frequency_activity`.

**Every result** is validated by `ClassificationResult` (strict pydantic) and appended to `wallet_classifications`. Each one includes:
- the rules and model references (name, version, feature version, status, data kind);
- the feature digest and `as_of`;
- class scores;
- evidence references and coverage;
- limitations.

## 8. Hand-off to AI research

The research tool `wallet_classification` returns the full classification contract plus its history. The research engine then turns it into claims:
- rule matches become `deterministic_rule` claims and model outputs become `model_prediction` claims, both always with status `inferred`;
- contradictions become `conflicted` claims;
- empty role sets become `missing_information`.

The AI can read these but cannot change them; see `docs/AI_RESEARCH_METHODOLOGY.md`. AI output never alters a prediction, a label or a verification status.

## 9. API (`/api/v1/ml`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/taxonomy` | Labels, features and status definitions |
| POST | `/classify/{chain}/{address}` | Classify (rules plus active model); persisted |
| GET | `/classifications/{chain}/{address}` · `/classifications/{id}` | History · one result |
| GET | `/features/{chain}/{address}` | Latest feature snapshot |
| GET | `/models` · `/models/{name}/{version}` | Registry (with the active model per chain) · evaluation |
| GET, POST | `/training-runs` | History · train (`{"chain", "source": "stored_labels" \| "synthetic"}`), one run at a time |
| GET, POST | `/labels` | List · import (validated, ≤ 5,000 per request) |
| POST | `/labels/{id}/reviews` | Append a review |

## 10. Known limitations

- No real labels exist, so there is no trained real model and no real-world metric.
- Several roles are not computable from current data and are never predicted: swaps, liquidity, holding periods, MEV, exchange flows, staking and validators.
- Training-set coverage is limited to what BIP ingested (selection bias).
- Probabilities from small validation sets stay uncalibrated, so predictions are capped at `low_confidence`.
- The synthetic generator is simple; near-perfect synthetic metrics are expected and meaningless.
- Retraining is manual. A model can be retired only by registering a newer one; there is no automatic promotion.
