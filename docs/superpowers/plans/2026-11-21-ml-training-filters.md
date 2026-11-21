# ML Training Filters and Controlled Continuous Retraining

**Owner request (2026-11-21):** "create a bunch of filters that help bip machine learning models learn
and train as they are running".

**Roadmap mapping:** Stage 4, Human Supervision and Machine Learning. The remaining items are
"controlled retraining" and "training datasets" ("Controlled retraining on real labels is not done",
docs/IMPLEMENTATION_ROADMAP.md).

**Authority:** no separate spec exists. The binding sources are CLAUDE.md §2 (principles 5, 7, 8, 10, 12)
and ADR-027 and ADR-050 in DECISIONS.md. This plan adds to them and never contradicts them.

## Design

The models learn while BIP runs. Two pieces make that safe:

1. **Training-example filters** (`bip/ml/filters.py`). One ordered pipeline sits in front of every
   trainer. It decides which examples a model may learn from, and records exactly what it dropped and
   why. Filters never change a value and never impute: they only keep or drop examples (principle 8).
2. **Controlled continuous retraining** (`bip/ml/continuous.py`). A scheduled job, off by default, checks
   whether enough new filtered, human-verified examples exist. If they do, it trains a challenger and
   promotes it only if it beats the current model (the champion) on the same held-out data. The job
   is never on the ingestion path (principle 10) and never contacts providers.

## Global Constraints

- Python 3.12+, typed, Pyright strict clean, ruff clean, pytest `filterwarnings=error`. Tests use
  local fakes and fixtures, never the network.
- Test first (TDD) for every filter and for the promotion gate.
- **No schema migration.** Filter reports go in the existing `ml_training_runs.dataset_json` (Text)
  and `params_json` columns. Continuous-training bookkeeping goes in `app_metadata` keys prefixed
  `ml:continuous:`. A challenger that is not promoted is never inserted into `ml_models`: its run row
  records status `succeeded`, message `challenger not promoted`, and metrics. If you find that a
  migration is truly unavoidable, stop and report BLOCKED instead.
- **Missing stays missing.** Filters read `None` as missing, never as 0. No filter imputes.
- **Machine labels never train.** Model predictions, AI-proposed labels and synthetic labels are
  dropped by the filters. Take the exact source and status values from `bip/ml/labels.py`,
  `bip/ml/curation.py` and `bip/ml/taxonomy.py`; do not invent them. Synthetic training keeps its
  existing separate, explicitly enabled path.
- **Every run is reproducible and explained.** The filter report is deterministic for the same input.
  It is stored with the run and is part of the dataset hash input.
- Outputs label model results `statistical_signal`. The model never confirms, rejects or triggers
  anything.
- Settings live in `MLSettings` (`bip/config/settings.py`) with these exact defaults:
  - `filters.enabled`: all filter names, in pipeline order
  - `filters.max_missing_fraction` = 0.5
  - `filters.max_examples_per_entity` = 5
  - `continuous_training_enabled` = False
  - `continuous_interval_hours` = 6
  - `continuous_min_new_examples` = 20
  - `promotion_brier_tolerance` = 0.005
- Commits: imperative, say why, and no model identifiers or attribution lines. Stage with explicit
  paths. Append an entry to the top of IMPLEMENTATION_LOG.md (newest first) in each task, with the real
  check output.
- Never dispatch subagents.

### Task 1: Filter framework and filters

Create `bip/ml/filters.py` and `tests/ml/test_filters.py` (or the repo's existing ML test directory).

- `Candidate`, a frozen dataclass and the generic adapter that every trainer maps to:
  - `key: str` is unique per example
  - `entity: str` is `chain:subject`
  - `as_of: datetime` is when the features were taken
  - `label_time: datetime | None`
  - `label: str | None`
  - `value: bool | None`
  - `label_source: str`
  - `label_status: str`
  - `features: Mapping[str, float | None]`
- `FilterStep` is a protocol: `name: str` and `apply(items: Sequence[Candidate]) -> StepResult`.
  `StepResult` holds the kept items, a per-reason drop count, and up to 20 dropped keys per reason,
  in input order.
- Filters, in this exact pipeline order:
  1. `human_verified`: drops machine-origin labels (see the Global Constraints).
  2. `point_in_time`: drops an example when `label_time` is set and `as_of > label_time`, or when
     `as_of` is after the run's `now`. The clock is injected.
  3. `finite_values`: drops an example with any NaN or ±inf feature value. `None` is allowed.
  4. `feature_completeness`: drops an example when the share of `None` features is greater than
     `max_missing_fraction`. An empty feature map counts as fully missing.
  5. `label_conflict`: when the same `(entity, label, as_of)` appears with different values, drops
     all of those examples.
  6. `deduplicate`: for the same `(entity, label, value, feature digest)`, keeps the newest `as_of`.
     Ties go to the lowest key. The feature digest is sha256 over sorted feature items, with `None`
     encoded distinctly from 0.
  7. `per_entity_cap`: keeps at most `max_examples_per_entity` examples per entity, newest first.
     Ties go to the lowest key.
- `run_pipeline(items, settings, now) -> (kept, report)`. The report is JSON:
  `{"filters_version": "1", "input": n, "output": m, "steps": [{"name", "input", "dropped", "reasons": {...}, "samples": {...}}]}`.
  It runs only the filters in `settings.filters.enabled`, but always in the fixed order. The function
  is deterministic and does not mutate its input.
- Add the `MLSettings` fields listed in the Global Constraints, with validation: fraction in [0, 1],
  cap ≥ 1, and enabled names restricted to the known set.
- Tests: each filter's keep and drop cases, including the `None`-is-not-0 cases; determinism
  (shuffled input gives the same report); the order is fixed even when `enabled` is reordered; and a
  disabled filter is skipped.

### Task 2: Apply the filters in both trainers

- **Wallet classifier** (`bip/ml/training.py`, `Trainer`): map stored-label examples to `Candidate`s, one
  per (example, label), and run the pipeline. Keep a label on an example only if its candidate
  survived; drop examples left with no labels. Do this before the data gate, so the gate counts
  filtered data. Store the report in the run's `dataset_json` under `"filters"` and include it in the
  dataset hash. The synthetic path does not run the filters but records `"filters": "not_applied_synthetic"`.
- **Signal ranking** (`bip/ml/signal_ranking.py`): map labelled rows to `Candidate`s. The label is the
  human review outcome, `label_time` is the review time, and the features are the snapshot features.
  Run the pipeline before the split and the readiness gate. Store the report with the ranking run
  (`signal_ranking_runs`, in its existing JSON column).
- Tests: a stored machine label is excluded from training; a too-incomplete example is excluded and
  counted; the report is persisted on the run; and the existing trainer tests still pass unchanged,
  except where a test asserted the old example counts, which must be updated with the reason stated.

### Task 3: Controlled continuous retraining

Create `bip/ml/continuous.py` and register a scheduler job in the existing jobs module (`bip/ops/jobs.py`).
Follow how other optional jobs are registered and gated.

- The job is `ml_continuous_training`. Its interval comes from `continuous_interval_hours`,
  `contacts_providers=False`, and it is registered only when `continuous_training_enabled` is true.
  It respects whatever analysis or ingestion lock the existing training endpoint uses.
- For each chain with real labels:
  - Count the filtered eligible examples, using the Task 2 pipeline. If the count minus the count
    stored at the last decision (`app_metadata` `ml:continuous:wallet_classifier:<chain>:examples`)
    is below `continuous_min_new_examples`, skip and record the reason.
  - Otherwise, train a challenger through `Trainer` in a mode that evaluates without registering.
  - **Promotion gate.** If there is no champion, promote when the data gate passes. Otherwise re-score
    the champion on the challenger's test split and promote only when both hold:
    - challenger macro PR-AUC ≥ champion macro PR-AUC
    - challenger Brier ≤ champion Brier + `promotion_brier_tolerance`

    When the champion's `feature_version` differs, the models are incomparable: promote, with the
    decision note `feature version changed`.
  - On promotion, register through the existing registry path. On rejection, record the run as
    described in the Global Constraints.
  - Store the decision in `app_metadata` `ml:continuous:wallet_classifier:<chain>:last_decision` as
    JSON: time, decision, the metrics of both models, the filter report summary and the example count.
- Signal ranking: when new human review outcomes since the last decision reach
  `continuous_min_new_examples`, run the existing ranking evaluation once, which is gated as it is
  today. Record the decision in the same way, under `ml:continuous:signal_ranking:<chain>:...`.
- First confirm how `ClassificationService` picks the model it serves (newest `trained`). A promoted
  challenger must become the served model, and a rejected one must never be served.
- Tests use a fake clock and fixtures:
  - skip when there are too few new examples;
  - promote when there is no champion;
  - promote a better challenger;
  - reject a worse challenger, which then is not served;
  - feature-version mismatch;
  - the job is not registered when disabled;
  - the job never touches provider code.

### Task 4: API, Models page, documentation

- `GET /api/v1/ml/continuous`, readable by analysts and viewers like the other ML reads. It returns:
  whether continuous training is enabled, its interval and thresholds, the last decision per model and
  chain, and the latest filter report per model and chain.
- `POST /api/v1/ml/continuous/run`, admin only, keeping the access policy fail-closed. It runs one
  cycle synchronously, under the same lock as training.
- Mirror the access rules in `frontend/src/auth/access.ts` only if the policy tables there require it.
  Update `docs/API_REFERENCE.md` so `tests/unit/test_api_reference.py` passes.
- Models page: add a "Training filters and continuous learning" panel with three parts. It shows the
  enabled status and thresholds; the last decision per chain (promoted, rejected or skipped, with the
  reason and both models' metrics, labelled statistical signal); and the latest filter report as a
  table of step, input, dropped and top reasons. Add a vitest test and extend the fake backend.
- Docs:
  - ADR-052, "Training-example filters and controlled continuous retraining", status Proposed,
    with Context / Decision / Consequences.
  - CURRENT_STATE.md: Stage 4 row and capability table.
  - IMPLEMENTATION_LOG.md entry.
  - `.env.example`: commented settings for the new options.
