# ML / data-science harness (advisory, optional)

Status: **harness + rules baseline only.** The data collected so far does not justify supervised models or forecasting
(numbers below, measured 2026-11-07 on a copy of the verified snapshot). Nothing in `watchtower/ml/` is imported by the app,
the web UI, ranking or the core tests, and no model output may ever merge products: scores are advisory, uncertain pairs go to
the review queue (CLAUDE.md).

## Install (Linux VPS, CPython 3.13)

```bash
python3.13 -m venv .venv-ml                       # isolated; never the system site-packages
.venv-ml/bin/pip install -r requirements-core.txt -r requirements-ml.txt
.venv-ml/bin/python scripts/ml_env_check.py       # versions + fit/predict smoke test; exit code != 0 if broken
.venv-ml/bin/python -m pytest tests/test_ml_*.py
```

* `requirements-ml.txt` pins (generated from `pip freeze`): scikit-learn 1.9.1, numpy 2.5.4, scipy 1.18.1, joblib 1.6.0
  (+ transitive cloudpickle, narwhals, threadpoolctl). pandas (and its python-dateutil / tzdata pins) was dropped: nothing imports it,
  and it was uninstalled from `.venv-ml`. `requirements-core.txt` pins the
  app dependencies as resolved at the same time (`requirements.txt` is unchanged). Pins were resolved on Linux x86_64 /
  CPython 3.13.18; other platforms may resolve differently, so re-run `ml_env_check.py`.
* Disk: the venv is ~437 MB including the app deps (scipy ~140 MB, sklearn ~48 MB, numpy ~43 MB; pandas no longer installed). `.venv-ml/` and
  `models/` are git-ignored. No paid services, no model downloads: everything is classical scikit-learn trained locally.
* rapidfuzz was **not** added: pair features use `difflib` and the whole pipeline (1,284 labelled pairs, feature extraction,
  two models, 3-fold CV) runs in ~2 s, so there is no measured benefit to justify another dependency.
* The app and `python -m pytest` work without scikit-learn; ML tests `importorskip("sklearn")` (4 modules skipped in the system
  python). `watchtower/ml/` imports sklearn lazily and never imports `shopping`.

## Run (on a snapshot COPY only)

```bash
python -m watchtower.cli backup --out /tmp/copy.db          # WAL-safe snapshot; never cp the .db
.venv-ml/bin/python -m watchtower.ml.sufficiency --db /tmp/copy.db
.venv-ml/bin/python -m watchtower.ml.evaluate   --db /tmp/copy.db [--seed 0] [--k 3] [--exploratory]
```

`open_readonly_session` opens the file read-only and refuses the application DB path (`config.DB_URL`).

## What the data is, and what it justifies (measured)

| Quantity (live sources only: adafruit, sparkfun) | Value |
|---|---|
| Live listings | 282 (adafruit 192, sparkfun 90) |
| Cross-merchant products | 6 (Raspberry Pi 4 Model B and Pi 5, each 2/4/8 GB) |
| Live observations | 343, on **1** distinct day (2026-11-06); max 2 observations for a listing |
| Human-verified labels (both listings `confirmed` / audited accepted-rejected decision / reviewed file) | **0** |
| Rule-derived labelled pairs | 1,284: 6 positives (`rule_auto`), 1,278 negatives (1,266 `rule_veto`, 12 `distinct_cross_merchant_products`) |
| Listings in review state (excluded) / unlabelled blocked candidates | 15 / 88 |
| Listings meeting anomaly history (>= 8 obs on >= 7 days) | 0 |

Verdict from `sufficiency_report()`: *Insufficient for supervised models and forecasting; harness + rules baseline only.*

| Capability | Justified? | Threshold (documented rules of thumb) |
|---|---|---|
| Supervised matching (LR / GBM) | **No** | >= 100 human-verified positives, >= 100 negatives, >= 30 labelled product groups |
| Per-listing robust anomaly baseline (MAD z, IQR) | **No** (0 listings qualify) | >= 8 observations on >= 7 distinct days per listing |
| Fleet IsolationForest | **No** | >= 30 qualifying listings and >= 300 observations among them |
| Price forecasting | **No** (1 day) | >= 90 distinct days and >= 30 listings with >= 30 obs on >= 30 days |

Several crawls on one day are not multi-day history, so days (not rows) are what counts. Forecasting is not implemented:
building it before there is history would be theatre.

## Label provenance and circularity

Every pair carries `label_source` and `trust` (`dataset.py` docstring is authoritative).

* `human` trust: a pair is human only if BOTH listings are `confirmed`, or a `MatchCandidate` row (accepted/rejected with
  `decided_by` NOT NULL) covers that pair (candidate listing vs a member of the decided product on another merchant), or an
  explicit reviewed-labels JSON (`[{"a": "source:external_id", "b": "...", "label": 0|1, "reviewer": "name"}]`). **None exist
  yet.** Snapshots whose `match_candidates` table has no `decided_by` column yield no human decisions.
* `human_propagated` trust: same product where only ONE listing is `confirmed` (the other is `auto`): the human judged one
  listing, not this pair. Reported, never counted toward sufficiency, never evidence.
* `rule_derived` trust: the rule matcher's own output: same product with status `auto` (positive); different products with a
  hard-conflict / brand / category veto, or two different already-linked cross-merchant products (negative).

**Circularity risk.** Scoring the rule matcher against labels it produced measures agreement with itself. Measured result:
rule baseline vs rule-derived labels = TP 6, FP 0, FN 0, TN 1,278 (precision = recall = 1.0) but flagged `circular`, and
`underpowered` (6 positives). This is *not* an accuracy estimate and must not be quoted as one. A model trained on these labels
learns to imitate the rules; so the model feature set (`MODEL_FEATURE_NAMES`) drops the features that echo the vetoes
(`title_conflict`, `hard_conflict`, `gtin_shared`, `mpn_shared`). That is NOT enough: the measured single-feature ROC AUC on the
real snapshot pairs is 0.992 for `name_equal` / `name_jaccard`, 0.995 for `seq_ratio`, 0.986 for `unit_kinds_agree` and 0.953 for
`price_ratio` (report block `ablation_single_feature_auc`, flag `task_trivial`, headline `TASK TRIVIAL`). Reason: selection bias.
The negatives are only rule-vetoed pairs (plus 12 distinct cross-merchant products) and the 88 blocked candidates the rules could
not label are excluded, so every ambiguous pair (the only interesting ones) is missing and near-identical names almost define the
positive class. A high model score here shows the task as constructed is trivial, not that the model matches products. The 6 positives were eyeballed (e.g. "Raspberry Pi 5 - 2 GB RAM" vs "Raspberry Pi 5 - 2GB") and look correct,
but one engineer's glance is not an independent label set. Real accuracy needs human decisions from the review queue
(see COMMERCIAL_ROADMAP / admin review), recorded as `confirmed`/accepted/rejected.

**Human labels and selection bias.** Human decisions exist only for candidates the matcher itself placed in its review band, so
they describe the matcher's uncertain cases, not the pair population. Sufficiency therefore counts DISTINCT DECISIONS (one
decision on a 3-merchant product yields 3 pairs but is one decision) and DISTINCT POSITIVE PRODUCTS (>= 30; singleton negative
groups do not count), not pairs or groups.

## Evaluation protocol (`evaluate.py`)

1. Pairs: blocked candidates (cross-merchant, compatible brand, shared name-signature token) + same-product positives.
2. Split by **group**: union-find of (a) a product's listings and (b) listings with an identical (canonical brand, name-signature)
   (`build_group_map`), so colour variants / re-listings share a fold. Measured on the snapshot: grouping by product only left
   27.7% of test-fold listings with an identical (brand, name-signature) listing on the train side (23% of listings have such a
   twin); with the union grouping it is 0.0% (reported as `grouping.cross_fold_duplicate_rate`). Pairs whose two groups fall on
   different sides are dropped, so no listing/product/duplicate is on both sides (`assert_no_group_leak`, tested).
3. Baseline: the existing `match_score` decision (`auto` = positive) by trust tier. Candidates: logistic regression
   (standardised) and gradient boosting, out-of-fold predictions.
4. Metrics, **per trust tier** (`models.<name>.by_trust_tier.{human,human_propagated,rule_derived,pooled}`): precision/recall,
   recall at precision >= 0.99, average precision, Brier score, 5-bin reliability table. Every block has `circular` (true for
   rule_derived and for pooled blocks containing it), `underpowered` (< 30 positives or < 30 negatives) and `evidence_eligible`
   (only the `human` tier, powered, with both classes). The pooled block is never evidence; `models_status` can read `evidence`
   only if sufficiency is met, the human block is eligible and the human-tier ablation is not trivial (`decide_models_status`).
   Recall-at-precision picks its threshold on the labels it is scored on, so it is labelled `oracle_threshold` (optimistic),
   carries a bootstrap 95% CI (`recall_ci95`) and `underpowered_for_target` when fewer than 100 predicted positives exist for a
   0.99 target; a nested number (threshold chosen on the training folds) is in `nested_train_threshold`.
5. Models are only fitted when sufficiency allows. With `--exploratory` they are fitted on insufficient data and the report is
   stamped `EXPLORATORY - NOT EVIDENCE`. Measured exploratory run (seed 0, k=3, union grouping): of 1,284 pairs, **740 were never in any test fold**
   (straddling groups; the earlier "1,718" was a sum over folds, where each straddling pair is counted in k-1 folds). Per fold
   (train pos/neg, test pos/neg): fold0 0/271, 0/405... i.e. train 0+271, test 6+405; fold1 train 6+762, test 0+72; fold2 train 6+783,
   test 0+61. All 6 positives land in one fold, so the out-of-fold set has a single class (133 pairs, 0 positives) and no ranking
   metric exists: the honest result is "not evaluable", flagged `underpowered`/`circular`. **It says nothing about real-world accuracy.**
6. Report: `models/reports/<run_id>.json`, `run_id = ml-<UTC time>-s<seed>-<dataset sha8>`, containing seed, folds, library
   versions (sklearn, numpy, scipy, joblib), git commit, a content-based dataset fingerprint (pairs plus the listing fields the features read), label counts, the sufficiency report, rule baseline, and model results or the refusal reason.

## Candidate generation

`candidate_pairs` blocks on name-signature tokens and **skips any token shared by more than `MAX_BLOCK_SIZE` = 400 listings**
(stop-word-like tokens would make blocking quadratic). Two listings whose only shared token is such a token are therefore never
candidates; pairs sharing any rarer token are still found.

## Anomaly detection (`anomaly.py`)

Robust per-listing baseline on ONE price per day (the day's last observation), requiring >= 5 distinct prices or >= 2 price
changes (a flat series has MAD 0 and would turn a 5% sale into a flag) and a dispersion floor of 5% of the median (moves under
~7.5% are never flagged) (modified z-score from median/MAD, |z| > 3.5; Tukey IQR fences) and a fleet-wide IsolationForest (`contamination='auto'` by default; a float forces that fraction of flags and is reported as `forced_flag_fraction`) on
(log price vs listing median, log step change). Each **refuses** (status `refused`, reason given, no model fitted) when history is
insufficient; NULL prices are skipped, never zero; non-finite, fractional and non-positive prices are dropped and counted (`price_rows_dropped`); a missing scikit-learn degrades the forest to `refused`; currencies are never mixed; only `Source.kind == 'live'`. Measured on the
snapshot: all 282 listings with prices are refused ("too few observations": each has 1-2 observations on a single day); IsolationForest refused (0 qualifying listings). Detection is demonstrated only on a synthetic series built inside the unit tests, labelled as such.
Flags are advisory; they do not alter deals, ranking or events.

## Versioning and fallback

* `ML_SCHEMA_VERSION` (watchtower/ml/__init__.py) and the run id tie each report to code version, seed and data fingerprint.
  Bump the schema version when features or label rules change; old reports stay comparable only within one version.
* No model artifacts are persisted or loaded by the app yet. If one is added later it goes under `models/` (git-ignored) with its
  run id, and the app must keep working without it.
* Fallback is always the rule matcher (`watchtower/matching.py`) and the deal/quality rules: without scikit-learn, without a
  model, with insufficient data or on any ML error, behaviour is exactly the current rule-based behaviour.

## When to revisit

Re-run `sufficiency` after: (a) humans have decided >= 100 review candidates each way across >= 30 products, (b) >= 7-8 days of
daily live crawls (anomaly baseline) and ~90 days (forecasting). Until then spend effort on collecting more sources/days and on
human review, not on models.
