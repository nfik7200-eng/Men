# Implementation Log

Entries are newest first. Each entry records what was done, how it was verified, and what was not done.

_Rebuilt on 2026-11-21 (see the first entry). This file is the union of the two histories that a faulty rebase had collapsed to 18 lines: the upstream log at `9765710` (1186 lines) and the local Detective, strategies, curation and graph-explorer entries from `8e9112c`. Entries inside the upstream block keep their original text; only their order was normalised to newest first._

## 2026-11-23 — Setup from the web app (ADR-056)

**Owner goal.** "Complete the entire backend and frontend; if you need keys or APIs, leave that to me. I will set up the backend from the frontend UI."

**Done.**
- **Store** (`bip/config/store.py`): `ConfigStore` (atomic 0600 writes in a 0700 directory, symlinks refused, a corrupt file is an error), `LayeredEnviron` (the environment first, never both a store value and an environment `<NAME>_FILE`), `load_effective_settings` (allowlisted overrides minus environment-set fields; invalid overrides are reported, not fatal) and `validate_override`.
- **Catalogue** (`bip/config/setup_catalog.py`): 24 editable settings. Binding, authentication, paths and limits are excluded.
- **API** (`bip/api/routes/setup.py`): `GET /setup`, `POST`/`DELETE /setup/secrets/{name}`, `POST`/`DELETE /setup/settings/{key}` and `POST /setup/restart`. `access.ADMIN_ANY` now covers `/setup` (server and frontend mirror).
  - Provider URLs are checked against the registry host allow-list.
  - Telegram needs its chat id and token before it can be switched on, and its token cannot be removed while it is switched on.
  - Every change is audited by name.
- **Wiring.**
  - The router, the Telegram secret lookup and the AI provider factory now read `LayeredEnviron`, so provider keys apply live.
  - Telegram switched on from the page without a token is skipped with a warning instead of stopping startup.
  - `bip/api/lifecycle.py` and the `python -m bip.api` loop restart the server in place.
- **Frontend.** `Setup.tsx`, client calls, types, a route, a nav entry under the system group, styles and fake-backend routes. `Settings.tsx` shows the real sign-in mode (it said "Not implemented").
- **Docs.** ADR-056, a "Setup from the web app" section in `docs/OPERATIONS.md`, and the regenerated `docs/API_REFERENCE.md`.

**Tests.** `tests/unit/test_config_store.py` (13), `tests/api/test_setup_api.py` (15), `frontend/src/pages/setup.test.tsx` (4).
- The store and API tests were written before their implementations, but their first run came after the implementation. Only the `checkable` flag test was seen failing before its change.
- Five mutation checks were each caught by a test: the store shadowing an environment `_FILE`, no provider host check, Telegram without a token, the store beating the environment, and a non-editable key being applied.

**Rulings** (decisions taken without a separate owner answer, with their cost if wrong)
- Keys are kept in a server-side 0600 file, not in SQLite: a database backup or copy never carries keys. Cost if wrong: keys need their own backup.
- Reads of `/setup` are admin-only, because they reveal which accounts are configured. Cost if wrong: analysts cannot see provider set-up status there (`/providers` still shows it).

**Evidence.** Full backend run `1359 passed in 654.60s (0:10:54)` (1331 before, 28 new). `ruff check .` clean; `ruff format --check .` 337 files formatted; `pyright` 0 errors. Frontend: `vitest run` 31 files / 302 tests passed (including `research/runpage.test.tsx`, which was flaky earlier); `tsc --noEmit` and `eslint .` clean. `docs/API_REFERENCE.md` regenerated; its test passes.

## 2026-11-22 — Solana discovery and memory context (M6, narrowed)

**Why.** M6 (from the 2026-11-13 audit) is that Solana has fewer detectors and memory sources. The Solana collector already stores program payers and pool mint pairs in `solana_discoveries.detail_json`, but discovery and memory used them only as display detail.

**Done (test-first; all 9 initial tests seen failing).**
- **`bip/discovery/detectors.py`: `solana_new_entities` version 2.**
  - Repeat-payer count over the baseline window, magnitude `0.3 + 0.7·(n−1)/9`.
  - Unseen pool mints: the earliest stored pool per mint, at or before the evaluation time, through a JSON-string match re-checked on the parsed row; magnitude 0.6 or 0.25.
  - Mint-initialised-earlier links: +0.1, and the mint signature is added to the evidence.
  - Kind-specific caveats: the payer may not be the developer, and a launch pattern is not proof.
  - The registry limitations are updated.
- **`bip/intel/memory.py`.** The Solana phase asserts `deployed_by` (payer, with basis) for programs and `pool` (protocol, both mints) for pools, and creates the mint token entities. The aggregate phase adds `role:deployer` with `programs_deployed` for Solana payers, bounded to 1,000 rows.
- **Docs.** `docs/SIGNALS.md`: the detector row is updated. The "Not implemented" paragraph is corrected: it still said Solana program and pool detection were missing.

**Mutation checks** (each change was restored afterwards):
- removing `block_time >= since` from the payer count: 1 test failed;
- replacing the unseen-mint test with `True`: 1 test failed;
- dropping the "mint slot before pool slot" rule: no test failed at first, because the existing case placed the mint after the evaluation time, where the query bound already hid it. `test_a_mint_initialised_after_its_pool_is_not_linked` was added, and the mutation then failed 1 test.
- The `block_time <= now` bounds inside the two pool and mint lookups are defensive only: a candidate is never after the evaluation time and the lookups compare earlier slots, so no output test can catch them.

**Rulings** (decisions taken without a separate owner answer, with their cost if wrong)
- No Solana holder-concentration detector. Stored SPL transfers name token accounts, not owners, so AMM vault accounts would dominate net inflow and produce systematic false signals. Cost if wrong: a missing signal until owners or vault accounts are stored.
- The program payer is recorded as the "deployer" under the existing fact key, with an explicit basis, rather than under a new key. Cost if wrong: a reader ignoring the basis could equate the payer with the developer; the basis and the caveat say otherwise.

**Evidence.** Full backend run `1331 passed in 627.29s (0:10:27)` (1321 before, 10 new). `ruff check .` clean; `ruff format --check .` 329 files formatted; `pyright` 0 errors. The related suites (`tests/discovery`, `tests/collector`, `tests/api/test_memory*.py`, `tests/preview`) passed 282/282 before the full run. No frontend change.

## 2026-11-22 — Strategy outputs: remove invented facts and the copy recommendation

**Why.** The strategy heuristics had one shallow unit test and one API test. Reading them before adding tests showed outputs that break CLAUDE.md §2.7, §2.11 and §2.12 and ADR-024, so tests pinning the old behaviour would have locked those claims in:
- `upgrade_authority_held: True` and `github_linked: False` were constants;
- `funder` was `transactions[0].from_address`, normally the wallet itself;
- `recommendation: "moderate_copy"` came from a transaction count alone;
- every transaction without a recipient counted as a contract deployment, which is every Solana transaction;
- the `unknown` classification lacked the `metrics` the page reads (a page crash).

**Done (test-first).**
- `tests/stage5/test_strategy_honesty.py`: 10 tests, all seen failing before the change.
- `bip/strategies/evidence.py`: a shared deployment rule, under which only a recorded `created_contract` from the wallet counts. An EVM transaction with no recipient and no recorded contract counts as `unconfirmed_creations`, and Solana never does.
- All four outputs carry `evidence_class: "hypothesis"` and `limitations`.
- `lead` replaces `recommendation`.
- `first_transfers` (sorted by time) replaces `early_entries`.
- The unchecked facts are `null` and listed in `not_checked`.
- The funder is the earliest inbound native sender, as a hypothesis, or `null`.
- The two older tests were updated to the renamed fields.
- Frontend: the types, the fake backend fixture and `Strategies.tsx` were updated. Each panel shows its evidence label and limitations. "Copy Trade Score" is now "Activity Score", and the panels are renamed for what they compute. A new `strategies.test.tsx` has 3 tests.

**Rulings** (decisions taken without a separate owner answer, with their cost if wrong)
- Removed the `recommendation` and `early_entries` response fields instead of keeping misleading names. Cost if wrong: an external API consumer would break. None is known; the bundled page is updated.
- Kept the existing score formulas, now labelled hypotheses, rather than inventing new ones. Cost if wrong: the scores remain uncalibrated, which the limitations state.

**Evidence.** Full backend run `1321 passed in 724.33s (0:12:04)`. `ruff check .` and `ruff format --check .` (327 files) clean; `pyright` 0 errors. Frontend: `tsc --noEmit` and `eslint .` clean; `strategies.test.tsx` 3/3.
- A full `vitest run` gave 297/298, then 296/298 on a second run. The failures were in `src/pages/research/runpage.test.tsx` ("lists persisted runs on the research index", and in the second run also "reruns as a new linked run..."). That file passed 7/7 alone twice. It was already recorded as a full-run timing flake on 2026-11-22, and this change does not touch it, so it is not fixed here; its flakiness remains open.

## 2026-11-22 — Leak tests for the as-of discovery upper bounds

**Why.** The 2026-11-14 entry recorded that only the `_tx_rows` upper bound had a leak test. The other bounds (large transfers, holder concentration, pools, both reserve queries, Solana discoveries, common funding) were untested, so a regression could let rows stored after the evaluation time into a historical run.

**Done.** `tests/discovery/test_as_of_bounds.py` (5 tests) calls each as-of-capable detector with `now=AS_OF` over rows just before and just after it. A mutation check deleted each `<= ctx.now` line in `bip/discovery/detectors.py` in turn and restored the file afterwards:
- `large_transfers` (line 197), `new_liquidity_pool` (517), `pool_reserve_change` series (706), `solana_new_entities` (934) and `common_funding_source` (1006): each made its test fail;
- `pool_reserve_change` pool selection (694): all tests still passed, as expected. It only chooses which pools to read, and their rows are re-read under the series bound. Its only effect is through the 500-pool cap. This is documented in the test module.
- `holder_concentration` was not tested: it is not as-of capable, so as-of runs skip it.

**Evidence.** `tests/discovery`: `149 passed in 84.61s`. ruff and pyright clean on the new file. No production code changed.

## 2026-11-22 — Lake storage layer, roadmap Stage 5 Part A (standard library)

**Done.** Plan: `docs/superpowers/plans/2026-11-22-lake-storage.md`. ADR-055 (Proposed).
- `bip/storage/lake/`: `schema.py`, `partition.py`, `manifest.py`, `writer.py`, `integrity.py`, `compaction.py`; `bip/tools/lake.py`; `StorageIntegrityError` and `RowValidationError` in `bip/core/exceptions.py`.
- Tests: `tests/storage/` (63 tests). The schema tests were seen failing (import error) before the implementation. The manifest, writer and compaction tests passed on their first run, so three mutations were made by hand afterwards, and each one was caught:
  - letting an uncommitted file be overwritten: `test_an_existing_file_with_different_content_is_never_overwritten` failed;
  - ignoring `supersedes`: 2 tests failed;
  - not closing a torn manifest line: `test_torn_trailing_line_is_ignored_and_the_next_append_starts_a_new_line` failed.
- Crash tests run a child process that dies with `os._exit(137)` at `encoded`, `placed` or `committed`. Before the commit, nothing is committed, the leftover shows as a warning (stale temp or unreferenced file) and a re-run adopts it. After the commit, a replay adds nothing.

**Blocked.** `uv add --group lake pyarrow duckdb` (owner-approved 2026-11-22) was refused by the session's permission classifier. No dependency was installed, and `pyproject.toml` and `uv.lock` are unchanged. Part B (Parquet codec with zstd, DuckDB reader, bytes per row on real data) waits for that install.

**Rulings** (decisions taken without a separate owner answer, with their cost if wrong)
- Superseded files are kept, and crash leftovers are reported, not removed: the platform does not delete without approval. Cost if wrong: disk use until a retention step exists.
- Single-host advisory `flock`. Cost if wrong: a network filesystem would need another lock.
- The JSON-lines codec lives under `tests/` only, so production cannot silently write non-Parquet files. Cost if wrong: none until Part B.

**Evidence.** Full backend run `1306 passed in 719.13s (0:11:59)`. `ruff check .`: All checks passed. `ruff format --check .`: 324 files already formatted. `pyright`: 0 errors, 0 warnings, 0 informations. No frontend change, so the frontend checks were not run.

## 2026-11-22 — Telegram alert delivery (Stage 12 alert channel)

**Done.** Plan: `docs/superpowers/plans/2026-11-22-telegram-alerts.md`. Owner decision 2026-11-22: Telegram bot. ADR-054 (Proposed).
- Task 1 (`9de96d0`): corrected the roadmap progress table. It had listed UI milestones B to E as not started; B is now Partial, with the missing pieces named, and C to E are Done. The 2026-11-22 owner decisions are recorded.
- Task 2 (`1862100`): migration v23 adds the `alert_deliveries` outbox. `DiscoveryService(external_channels=...)` writes `pending` rows inside the alert transaction, with no network I/O.
- Task 3 (`5408594`): `TelegramChannel`, plus `DeliveryOutcome`, `ExternalChannel` and `format_alert_text`. The response mapping only counts 2xx with `ok: true` as delivered. A 429 honours `retry_after`; 5xx, transport errors and non-JSON bodies are retried; other 4xx are permanent. The token is checked against a strict pattern before use, registered for redaction, and kept out of every error.
- Task 4: `AlertDispatcher` (at-least-once, single-run lock, backoff capped at 1 h, per-run limit and send spacing); `AlertSettings` (`BIP_ALERTS__*`); `BIP_TELEGRAM_BOT_TOKEN` added to the ADR-053 allowlist; app wiring (refuses to start when enabled without a token); the `alert_delivery` job; `POST /alerts/channels/telegram/test`; docs (`OPERATIONS.md`, `PROVIDER_REGISTRY.md`, `.env.example`, regenerated `API_REFERENCE.md`).

**Provider facts** fetched 2026-11-22: `sendMessage` text 1-4096 characters (core.telegram.org/bots/api, sendMessage); one message per second per chat and 20 per minute in groups (core.telegram.org/bots/faq).

**Rulings** (decisions taken without a separate owner answer, with their cost if wrong)
- Task 1: committed the untracked plan file with the roadmap fix, because the roadmap row links it. Cost if wrong: none.
- Task 1: Milestone B is marked Partial, because named components and the graph list view are absent. Cost if wrong: wording.
- Task 2: the v22-to-v23 test builds a database with an alert, drops the outbox table and the v23 row, then migrates. Cost if wrong: low.
- Task 3: `redact` in the plan is `redact_text`; the channel also replaces its own token explicitly. Cost if wrong: none.
- Task 3: truncation counts UTF-16 units, which is how Telegram counts characters. Cost if wrong: emoji-heavy titles are cut slightly shorter.
- Task 3: a refused redirect retries like other transport errors; a status below 200 is permanent. Cost if wrong: one wasted retry.
- Task 4: the dispatcher selects only rows for configured channels, so rows for an unconfigured channel are counted as `skipped` but cannot use up the per-run limit. Cost if wrong: none.
- Task 4: the dispatcher lock is a public `lock` attribute, so the concurrency test can hold it. Cost if wrong: naming.
- Task 4: a huge `retry_after` is capped at 1 h like the backoff. Cost if wrong: an early retry that gets another 429.
- Task 4: `create_app(alert_channels=...)` lets tests inject a fake channel. The chat-id pattern lives in settings and is shared with the channel, to avoid a config→discovery import. Cost if wrong: none.
- Task 4: `test_only_in_app_delivery_is_available` now expects `telegram` under `unavailable`. Cost if wrong: none.
- Task 4: ran `ruff format` on the plan markdown, because ruff formats its Python snippets. Cost if wrong: none.

**Checks** (2026-11-22)
```
$ uv run ruff check .            -> All checks passed!
$ uv run ruff format --check .   -> 308 files already formatted
$ uv run pyright                 -> 0 errors, 0 warnings, 0 informations
$ uv run pytest -q               -> 1240 passed in 739.53s (1243 after the review fix, 711.35s)
$ npm run lint && npm run typecheck -> clean
$ npx vitest run src/pages/alerts.test.tsx -> 12 passed
```

**Final review:** self-review of the whole branch (no reviewer subagent was requested), checked against the plan's Review Focus list.
- Fixed (Important): `POST /alerts/channels/telegram/test` fell under the analyst pattern `^/alerts/`, although it contacts Telegram, and `bip/api/access.py` makes every external contact admin-only. An analyst could also have spammed the chat. It is now admin-only through `ADMIN_WRITE`. Test: `test_access_policy[POST-/alerts/channels/telegram/test-admin]`, RED then GREEN.
- Deferred (minor): Telegram enabled with `BIP_WORKERS__ENABLED=false` queues rows that are never sent, and nothing warns at startup (documented in `OPERATIONS.md` only).
- Deferred (minor): the dispatcher lock is per process. Several API worker processes could each send the same due row once (still at-least-once).
- Deferred (minor): the Alerts page has no "send test message" button; the endpoint is API-only.

**Not done.** No bot exists and nothing was sent live. The owner must create the bot and set the variables (`docs/OPERATIONS.md`).

## 2026-11-22 — Hardening fixes

**Done.** Plan: `docs/superpowers/plans/2026-11-22-hardening-fixes.md`.
- Task 1 (`84e9c05`): stdlib Keccak-256 in `bip/core/keccak.py`. The sponge is tested against `hashlib.sha3_256` (padding `0x06`) across block boundaries, plus known Keccak vectors.
- Task 2 (`7997753`): `evm_address` rejects mixed-case addresses with a wrong EIP-55 checksum. One fixture had an invented mixed-case address and now uses a valid checksum.
- Task 3 (`73560ad`): `unknown_bip_variables` plus a check in `load_settings`, and ADR-053 (Proposed). The allowlist was widened beyond the plan to cover `BIP_E2E_*` and `BIP_API_URL`, which the frontend e2e harness passes to the API it starts, and `<NAME>_FILE` for any known name. `test_unknown_nested_key_is_refused` already held before the change, because nested models use `extra="forbid"`.
- Task 4: `frontend/src/pages/Strategies.tsx` types its error state as `ApiError | null` instead of `any`. That `any` was the ESLint failure in CI. Corrected `CURRENT_STATE.md`, which still listed M1–M5 as open.

**Checks.**
```
$ uv run ruff check .            -> All checks passed!
$ uv run ruff format --check .   -> 302 files already formatted
$ uv run pyright                 -> 0 errors, 0 warnings, 0 informations
$ uv run pytest -q               -> 1183 passed in 552.56s (0:09:12)
$ M1/M4 recheck: tests/security/test_http.py -k "local_mode or allowed_hosts or foreign_origin" -> 4 passed; tests/discovery/test_pipeline.py -> 22 passed; discovery_lock present in discovery.py and entities.py
$ npm run lint                   -> clean (after the Strategies fix; was 1 error)
$ npm run typecheck              -> clean
$ npx vitest run                 -> 2 failed | 293 passed (295); the two (App.test.tsx "navigates between all sections" 5 s timeout, runpage.test.tsx rerun location) pass alone: 2 files, 24 passed
$ gh run list                    -> last 3 runs (up to 2419bb0) failed: Pyright (fixed in d1e77ea) and ESLint (fixed here)
```

**Not done.** CI has not yet run on these commits. The two Vitest timing flakes are not fixed. `Strategies.tsx` was already not Prettier-formatted, and Prettier is not in CI.

## 2026-11-21 — Controlled continuous retraining (Task 3)

**Done.** `bip/ml/continuous.py` adds `ContinuousTraining`: per chain it counts filtered eligible examples (`Trainer.eligible_examples`), skips when fewer than `continuous_min_new_examples` are new since the count stored at the last decision (missing or garbled stored count is 0), otherwise fits a challenger without registering it and applies the promotion gate (no champion: promote; champion `feature_version` differs: promote with reason `feature version changed`; else re-score the champion on the challenger's test split and promote only if macro PR-AUC is not lower and Brier is within `promotion_brier_tolerance`, both over the labels both models score). `Trainer.train` is now `fit` (evaluate without registering, returns a `Challenger`) plus `register` (the unchanged registry path) or `reject` (run closed as `succeeded` / `challenger not promoted` with metrics, never inserted in `ml_models`). `ClassificationService.active_model` serves the newest `trained` row, so a rejected challenger cannot be served (tested through `/ml/models` and classify). Signal ranking: when new human review outcomes reach the minimum, the existing `SignalRanking.evaluate` runs once and a decision is recorded per chain. Job `ml_continuous_training` (`bip/ops/jobs.py`) is registered only when `continuous_training_enabled`, uses `continuous_interval_hours`, `contacts_providers=False`, and takes `train_lock` (wallet) and `analysis_lock` (ranking), the same locks as the manual routes. State is in `app_metadata` keys `ml:continuous:<kind>:<chain>:examples` and `:last_decision`; no migration. 21 new tests in `tests/stage5/test_continuous_training.py`.

**Checks.**
```
$ uv run ruff check bip tests
All checks passed!
$ uv run ruff format --check bip tests
264 files already formatted
$ pyright (whole repo)
3 errors (all in tests/stage2/test_free_provider_expansion.py, pre-existing)
$ uv run pytest -q
1155 passed in 718.72s (0:11:58)
```
Pyright was run with `extraPaths` set to the worktree, because the venv's editable install points at the main checkout.

**Not done.** No API or UI for the decisions (Task 4). The champion/challenger better and worse cases fake only `score_champion`; the real re-scoring path has its own test. No task review was run.

## 2026-11-21 — ML training filters: applied in both trainers (Task 2)

**Done.** `Trainer` (`bip/ml/training.py`) maps each stored (example, label) to a `Candidate` and runs `run_pipeline` before the data gate, so the gate counts filtered data. A label is kept only if its candidate survived; examples left with no labels are dropped. A reviewer-accepted weak or inferred label trains with status `analyst_reviewed` and keeps its real source. The report is stored in `dataset_json["filters"]` (also on `insufficient_data` runs) and included in the dataset hash. The synthetic path records `"filters": "not_applied_synthetic"`. `SignalRanking` (`bip/ml/signal_ranking.py`) maps labelled snapshots to `Candidate`s: the label is the human review outcome, `label_time` is the latest human decision time (caller-supplied experimental labels have none), and the features are the snapshot features. The filters run before the split and readiness gate. The report is returned as `filters`, saved in the run's summary JSON and included in its dataset hash. Timestamps are made timezone-aware and features are passed as floats or `None`. New tests: `tests/stage5/test_training_filters.py` (6) and 3 in `tests/discovery/test_signal_ranking.py` (machine label excluded, accepted weak label kept, incomplete example excluded and counted, post-review snapshot excluded as leakage, report persisted, synthetic path untouched). No existing test needed a count change.

**Checks.**
```
$ uv run ruff check bip tests
All checks passed!
$ uv run ruff format --check bip tests
262 files already formatted
$ uv run pyright bip/ml tests/stage5/test_training_filters.py tests/discovery/test_signal_ranking.py
0 errors, 0 warnings, 0 informations
$ uv run pytest -q
1134 passed in 1217.76s (0:20:17)
```

**Not done.** Continuous retraining (champion/challenger, Task 3) and the endpoint/UI (Task 4) are not started. Pyright was run on the touched paths only, not the whole repo. No task review was run on this diff.

## 2026-11-21 — Free provider expansion: owner onboarding checklist, ADR-052 and state docs

**Done.** Across the branch `providers-expansion`: seven RPC-or-catalogue entries in the first batch (`validationcloud-eth`, `validationcloud-sol`, `ankr-sol`, `dwellir-eth`, `blockpi-eth`, `nodereal-eth` with adapters; `infstones-eth` catalogue only), then `chainbase` and `routescan` as catalogue entries, and a note on `infura-eth` that Infura Solana is limited to select customers. `dwellir-sol` was skipped (no Solana offering found). In this task: `docs/PROVIDER_ONBOARDING.md` (owner checklist: owner creates accounts and keys personally, no automated signups, keys only in `.env`, verify with the budgeted probe; Dune and Bitquery listed as not recommended), a second-batch section in `docs/PROVIDER_REGISTRY.md`, ADR-052 (Proposed), `CURRENT_STATE.md`. Review follow-ups: added the source URLs behind the `chainbase` and `routescan` notes to their `sources`; dropped the routescan "incompletely indexed" caveat because the research file names no page for it; removed a tautological `checked == len(ADAPTER_IDS)` assertion from the placeholder-host test. The plan file `docs/superpowers/plans/2026-11-21-free-provider-expansion-plan.md` is committed.

**Checks (run after the final edit, offline).**
```
$ uv run --offline ruff check bip tests
All checks passed!
$ uv run --offline pyright bip/providers
0 errors, 0 warnings, 0 informations
$ uv run --offline pytest tests/stage2 -q
137 passed in 166.15s (0:02:46)
```

**Not done.** No account or key was created, no live request or probe was made, so no new provider capability is verified; all facts are `search_derived`. Terms of several new providers were not reviewed in full. No adapter for Dune or Bitquery. The full test suite, frontend checks and `ruff format --check` were not re-run for this documentation and registry-data change.

## 2026-11-21 — ML training filters: framework and seven filters

**Done.** `bip/ml/filters.py`: `Candidate` (generic adapter every trainer maps to), `FilterStep`/`StepResult`, the seven filters in fixed order (`human_verified`, `point_in_time`, `finite_values`, `feature_completeness`, `label_conflict`, `deduplicate`, `per_entity_cap`) and `run_pipeline(items, settings, now) -> (kept, report)`. The report is deterministic JSON (input sorted by key first, so shuffled input gives the same report), runs only `settings.filters.enabled` and always in pipeline order. `None` is missing, never 0; nothing is imputed. `MLSettings` gains `filters` (`enabled`, `max_missing_fraction` 0.5, `max_examples_per_entity` 5) and the flat `continuous_*` / `promotion_brier_tolerance` fields (defined only; used by a later task). Machine-origin set (`MACHINE_ORIGIN`) cites `training.py`, `research/engine.py` and the `entity_label_source` constraint; any status outside `labels.TRAINABLE` is also dropped (conservative). Tests: `tests/stage5/test_ml_filters.py` (42). Trainers are not wired yet.

**Checks.** `uv run ruff check .`: All checks passed!; `uv run ruff format --check .`: 293 files already formatted; `uv run pyright`: 0 errors, 0 warnings, 0 informations; `uv run pytest -q`: `1101 passed in 597.40s (0:09:57)`.

## 2026-11-21 — Provider catalogue additions, live probe and synthetic ML evaluation

**Done.** Nine providers added to `bip/providers/registry.toml` as catalogue entries with no adapter (chainstack, drpc, getblock, moralis, coingecko, birdeye, solscan, dune, bitquery). Each cites official sources with `documentation_checked = 2026-11-21`. Birdeye and Solscan free limits are recorded as unverified (not published on the pages read). Dune and Bitquery have no free API tier and are marked "do not configure". `.env.example` has commented placeholders; `docs/PROVIDER_REGISTRY.md` has a section. No keys exist and none were written to `.env` (it does not exist). An earlier plan to mine the owner's Gmail was cancelled by the owner; four Gmail search calls were made, all failed ("needs to be connected"), and nothing was read.

**Live check.** `make probe`: publicnode-eth reachable (6 supported, 1 timeout), solana-public reachable (10 supported); 15 requests, 18.7 s. `make live-smoke`: publicnode-eth ok (identity 0x1, head block, balance); keyed providers skipped (not configured); 0 failures.

**ML.** `stored_labels` training: `insufficient_data` (0 of 30 wallets) on both chains. Synthetic training succeeded (240 synthetic wallets, test n=48 per label). The models score F1 0.78 to 1.00; this is a statistical signal on separable synthetic data, not real accuracy. high_frequency_activity: model P 0.90 / R 0.69 versus rules P 0.81 / R 1.00 (Ethereum).

**Checks.** ruff check passed; ruff format --check passed after formatting one tracked plan doc that had failed it; pyright 0 errors; `uv run pytest -q`: `1059 passed in 642.66s (0:10:42)`; frontend typecheck clean; `npm run build` built; `npm test` (run alone): `Test Files 29 passed (29), Tests 295 passed (295)` (a first run concurrent with pytest had 2 timeout failures).

**Not done.** No Gmail discovery, no adapters for the new providers, no Flipside or label-dataset entries, no per-chain PublicNode hostnames, no e2e run.

## 2026-11-21 — Merge repair — final review fix wave

A final whole-branch review found two Important issues and several minor ones. Commits (forward only, nothing amended): `dfd973f` (migration 22), `8fd5eb2` (analyst grant), and the docs commit that carries this entry.

**Fixed.**
- **Migration 22** ("repair databases from the pre-merge local line", `SCHEMA_VERSION` 22): databases built on the pre-merge local line recorded cases as v6 and curation as v7, so upstream's v6/v7 tables (research links, signals, discovery runs, alert rules, alerts) were never created there. Migration 22 runs `create(checkfirst=True)` for `V6_TABLES + V7_TABLES`; additive and idempotent.
- **Analyst grant**: analysts may write `/cases/*` and `/curation/feedback` (`bip/api/access.py`, mirrored in `frontend/src/auth/access.ts`).
- **ADR-051** added to `DECISIONS.md` (Proposed) recording the renumbering, migration 22 and the analyst grant.
- **Doc wording**: `CURRENT_STATE.md` (roadmap row, "Awaiting owner approval" item 0, repair note now says `SCHEMA_VERSION` is 22) and a "(now migration 21)" note on the curation entry in this log. The merge-repair plan file is committed under `docs/superpowers/plans/`.

**Check output (working tree after the fixes).**
- `uv run ruff check .`: All checks passed!
- `uv run pyright`: 0 errors, 0 warnings, 0 informations
- `uv run pytest -q`, run 1: `1059 passed in 953.52s (0:15:53)`
- `uv run pytest -q`, run 2: `1059 passed in 813.77s (0:13:33)`
- `npx tsc --noEmit`: no errors
- `npm run build`: built OK (chunk-size warning only)
- `npx vitest run`: a first run concurrent with pytest had 3 files fail (5 s timeouts in `App.test.tsx` and `cases.test.tsx`, one text assertion in `research/runpage.test.tsx`); the contention cause was not isolated. Run alone: `Test Files 29 passed (29), Tests 295 passed (295)`.

**Also in this push (owner request).** `970228a` adds a Claude Code PreToolUse commit guard (`.claude/hooks/guard.py`: blocks commits with conflict markers, model attribution or a staged `.env*` file, pushes other than to `claude/modest-fermi-0yklvj` or with force, and edits to `uv.lock`) and a user-invoked `create-migration` skill whose `check_numbers.py` checks migration numbers on every branch (it reproduces the v6/v7 collision between `9765710` and `5bdddbf`). The guard was then changed to allow `.env` edits (owner decision) and to block staging them. 19 + 5 piped allow/block cases pass; ruff clean (`pyproject.toml` ignores T201, S603, S607, BLE001 for `.claude/**`).

**Not done.** No browser (e2e) run in this wave.

## 2026-11-21 — Repair of the botched rebase merge (backend, frontend, state documents)

**Starting point:** branch `claude/modest-fermi-0yklvj` at `b1d7fc3`. A rebase of the local Detective work onto upstream `9765710` had gone wrong in three steps:
- `b0c3f32` committed conflict markers.
- `434ded3` overwrote `IMPLEMENTATION_LOG.md` (1186 to 14 lines) and `CURRENT_STATE.md` (262 to 25 lines).
- `b1d7fc3` dropped backend code.

The repair used forward commits only. No history was rewritten and nothing was force-pushed.

**Measured state at `b1d7fc3`, before the repair:** pytest 20 failed / 1033 passed; pyright 325 errors; ruff 15 errors; tsc 33 errors.

**Done**
- `7a666b7` "Restore backend lost in the rebase merge": union of the `investigations` routes (research-links plus the cases workbench) and the restored `curation_feedback` table. The local migrations 6 and 7 were renumbered to 20 and 21 because upstream already uses 6 and 7. `SCHEMA_VERSION` is 21 (`bip/db/schema.py`).
- `f4b2d6b` "Dispose the in-memory engine in the case migration test": an undisposed sqlite engine in `tests/investigations/test_models.py` caused intermittent `PytestUnraisableExceptionWarning` failures under `filterwarnings=error`.
- `5700e75`: `docs/API_REFERENCE.md` documents the curation and strategies routes (186 routes in 17 groups).
- `364108a` "Resolve frontend merge conflicts by uniting both feature lines": the local `EvidenceClass` was renamed `ExplorerEvidenceClass`; navigation entries were added to `components/shell.tsx`; the local `ConnectionBadge` was superseded by upstream's `HealthProvider`.
- This commit restores `IMPLEMENTATION_LOG.md` (union of `9765710` and `8e9112c` plus the `5bdddbf`/`9dc8899` entry), merges `CURRENT_STATE.md`, and updates the five-stage table in `docs/IMPLEMENTATION_ROADMAP.md`.

**Verification evidence (real output)**
```
Backend
$ uv run --offline pytest -q -p no:cacheprovider
1053 passed in 578.89s (0:09:38)
$ ruff check .
All checks passed!
$ pyright
0 errors, 0 warnings, 0 informations
$ ruff format --check .
1 file would be reformatted  (docs/superpowers/plans/2026-11-06-bip-all-in-one-intelligence-plan.md, a markdown plan, not code)

Frontend
$ npx tsc --noEmit -p .
0 errors
$ npx vitest run
29 files / 291 tests passed
$ npm run build
built OK
```

**Not done / limits**
- The post-leak-fix evidence (after `f4b2d6b`) is a single green full run. It has not been repeated, and the earlier flakiness was intermittent, so one run does not prove it is gone.
- Nothing here was run against a live provider, a real model, or a deployed host.
- No Ollama or other LLM runtime is configured (`BIP_AI__PROVIDER=none` by default), and there is no production deployment.
- The log entries from before this date keep their original wording and test counts, which were true for their own dates and are not current.

## 2026-11-20 — Full Stage 5 Verification & Status Sync (recorded as written; see the 2026-11-21 repair)

_Recovered from commits `5bdddbf` and `9dc8899`, which replaced this log with this entry alone. Kept as written. Its figures were not reproduced during the 2026-11-21 repair and are superseded by that entry's evidence. Note that the command shown below cannot by itself have produced a count of 545 for `tests/stage5/` alone, and the two copies of the entry give different commands (`uv run pytest tests/stage5/` in `5bdddbf`, `uv run pytest` in `9dc8899`)._

**Starting point:** `claude/modest-fermi-0yklvj` branch, clean working tree.

**Done**
- Verified complete backend test suite (545 tests passed successfully, including Stage 5 strategy, ML, research, and curation tests).
- Confirmed frontend test suite (49 Vitest tests passed successfully).
- Synchronized `CURRENT_STATE.md` and `IMPLEMENTATION_LOG.md`.

**Verification evidence**
```bash
$ uv run pytest tests/stage5/        # as in 5bdddbf
$ uv run pytest                      # as in 9dc8899
545 passed in ~80s
```

## 2026-11-20 — Task 5: human-in-the-loop ML curation

_Date not recorded in the original entry; it was appended to the log in commit 8e9112c (2026-11-20)._

Added schema v7 `curation_feedback` (now migration 21), `bip/ml/curation.py`, `/api/v1/curation/*` routes and the `/curation` page. tests/api: 65 passed (7 new curation tests); vitest 9 files / 59 tests passed; ruff clean on new Python files.

## 2026-11-19/20 — Task 4: Temporal Graph Explorer and Evidence Trail

_Date not recorded in the original entry; it was appended to the log in commit 8e9112c (2026-11-20)._

- Added `bip/intel/graph_explorer.py` (pure post-processor over `build_graph`): time window, evidence-class filter (verified / single_source / hypothesis), timeline histogram, node evidence trail.
- `bip/intel/graph.py`: additive `evidence_cap` parameter; evidence now carries `verification_status` and `provider_response` (provider, endpoint, method, request id, raw ref/row/content hash).
- API: `GET /intelligence/graph-explorer/{chain}/{address}` and `.../nodes/{node_id}` (`since`, `until`, repeated `status`).
- Frontend: `/explorer` page "Temporal Explorer" (slider, status checkboxes, node trail); `GraphCanvas` gained an optional `nodeExtra` slot.
- Tests: tests/intel/test_graph_explorer.py, tests/stage3/test_graph_explorer_api.py, frontend/src/pages/explorer.test.tsx (vitest 8 files / 57 tests passed).

## 2026-11-19 — Tasks 3 & 4: Backend API Integration & Frontend Dashboard for Multi-Strategy Intelligence

**Starting point:** `61a3e49`, branch `claude/modest-fermi-0yklvj`.

**Done**
- Created `bip/api/routes/strategies.py` and `bip/core/api.py` exposing multi-strategy intelligence endpoints (`/strategies/classification/{chain}/{address}`, `/strategies/copy-trader/{chain}/{address}`, `/strategies/meme-momentum/{chain}/{address}`, `/strategies/dev-tracker/{chain}/{address}`).
- Registered strategy router in `bip/api/app.py`.
- Created comprehensive backend API tests in `tests/stage5/test_strategy_api.py`.
- Implemented frontend dashboard page `frontend/src/pages/Strategies.tsx` supporting target selection and views for wallet classification, copy trading signals, meme momentum opportunities, and developer activity trackers.
- Updated `frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/App.tsx`, and `frontend/src/test/fakeBackend.ts`.
- Created Task 3 and Task 4 implementation reports in `.superpowers/sdd/2026-11-06-bip-all-in-one-intelligence-plan/`.

**Verification evidence**
```bash
$ uv run pytest tests/stage5/test_strategy_api.py -v
tests/stage5/test_strategy_api.py::test_strategy_api_endpoints PASSED [100%]
1 passed in 9.94s

$ uv run pytest
500 passed in ~66s

$ cd frontend && npx vitest run
Test Files 6 passed (6)
Tests 49 passed (49)
```

## 2026-11-19 — Task 2: Advanced Strategy Extraction & Wallet Classification Engine

**Starting point:** `61a3e49`, branch `claude/modest-fermi-0yklvj`.

**Done**
- Created `bip/strategies/__init__.py`, `bip/strategies/classifier.py`, `bip/strategies/copy_trader.py`, `bip/strategies/meme_trader.py`, `bip/strategies/dev_tracker.py`.
- Created comprehensive test suite in `tests/stage5/test_advanced_strategies.py`.
- Verified all tests pass successfully (`pytest tests/stage5/test_advanced_strategies.py`).

**Verification evidence**
```bash
$ uv run pytest tests/stage5/test_advanced_strategies.py -v
tests/stage5/test_advanced_strategies.py::test_advanced_strategies_comprehensive PASSED [100%]
1 passed in 1.05s
```


**Starting point:** `61a3e49`, clean tree on branch `claude/modest-fermi-0yklvj`.

**Done (testing and validation only; no code changes)**
- **Live RPC connectivity testing:** Confirmed that BIP successfully connects to public Ethereum RPC endpoints (PublicNode) when `BIP_ROUTER__LIVE_REQUESTS_ENABLED=true`.
- **Real blockchain data access:** Verified real-time queries to Ethereum mainnet (latest block, chain ID, network verification).
- **Provider lifecycle validation:** Confirmed provider state transitions from `untested` to `healthy` after successful connection.
- **Error handling verification:** Tested and confirmed graceful handling of public API limitations (eth_getLogs restrictions on free endpoints).
- **API endpoint validation:** All major endpoints tested and responding correctly (health, providers, chains, ingestion, settings).
- **Documentation:** Created `TESTING_SUMMARY.md` with comprehensive results and recommendations.

**Verification evidence**
```bash
# Public RPC connectivity test (all passed)
PublicNode Ethereum: eth_chainId → 0x1 (mainnet confirmed)
Solana Public RPC: getHealth → "ok"
Latest block: 0x1937745 (26,461,029 decimal)

# System test suite (unchanged from previous run)
$ uv run pytest -q
498 passed in 161.48s

# Code quality checks (unchanged)
$ uv run ruff check .            → All checks passed!
$ uv run ruff format --check .   → 157 files already formatted
$ uv run pyright                 → 0 errors, 0 warnings, 0 informations

# Live integration tests with public APIs (5/6 passed)
✅ Health Check: All systems operational
✅ Ethereum Connectivity: Connected to public RPC
✅ Provider Management: 1 healthy provider (PublicNode)
✅ Wallet Ingestion: Job completed with "partial" status (expected)
⚠️  Dashboard: 404 in test environment (works in main suite)
✅ Configuration: Settings loaded correctly, live RPC enabled
```

**Test results**
- **Public RPC connectivity:** 3/3 endpoints reachable (PublicNode Ethereum, Solana Public RPC)
- **Live Ethereum connection:** Successfully retrieved mainnet data
- **Provider health tracking:** Automatic transition to "healthy" state confirmed
- **Wallet ingestion:** Completed with "partial" status due to public API restrictions on eth_getLogs
- **Error handling:** PublicNode's "Please specify an address" restriction properly detected and reported
- **API functionality:** 5/6 integration tests passed

**Public API limitations identified**
- PublicNode restricts eth_getLogs queries without full address filters (standard for free tier)
- Wallet history discovery limited to recent Transfer logs
- Full historical scanning requires credentialed provider (Alchemy, Infura, Ankr)
- BIP correctly handles these limitations with graceful degradation

**Expected results confirmed**
- ✅ Application connects to public blockchain RPCs
- ✅ Real-time blockchain queries functional
- ✅ Provider health status tracked accurately
- ✅ Error handling works as designed
- ✅ API endpoints respond with correct data structures
- ✅ Partial success states properly reported
- ✅ System remains stable despite provider limitations

**Not done**
- No code changes made (testing only)
- No credentialed providers configured (requires free account creation, awaiting owner approval)
- No Solana ingestion tested (focus was Ethereum)
- Dashboard endpoint 404 in test environment not investigated (works in main test suite)

**Recommendations for production**
1. Add credentialed providers: Alchemy, Infura, or Ankr for full eth_getLogs access
2. Implement Etherscan API integration for wallet history fallback
3. Add direct transaction lookup by hash as alternative to log scanning
4. Consider multiple provider failover chains
5. Implement caching for frequently accessed blockchain data

## 2026-11-14 — Stage 3 close-out and Stage 4 (uncommitted)

**Owner decisions this round:**
- write proof as a custom header plus exact origin;
- rename `accepted` to `confirmed`;
- one human decision makes a label, and disagreement holds the example out;
- both signal ranking and the wallet-classifier upgrades;
- Stage 4 plan approved.

Rules this round: no commit, push, deploy or live-chain calls.

**Stage 3 close-out (each test-first unless noted):**
- **Fail-closed write protection with sign-in off (ADR-048):**
  - `X-BIP-Client` is required on POST, PATCH and DELETE (`client_proof_missing`).
  - Exact Origin allowlist and `Sec-Fetch-Site`; Host allowlist (`host_denied`, plus `api.allowed_hosts`); Unix sockets skip the host check.
  - The frontend, the e2e helper, the documented `curl` example and the test clients (conftest default) send the header.
  - Tests: `tests/security/test_http.py` local-mode tests.
- **Freshness:**
  - Runs record `kind` (live, historical, producer); freshness uses live passes only; historical and expansion runs are reported separately.
  - A crashed run is closed as `failed`.
  - Tests: `tests/discovery/test_pipeline.py`.
- **Locks:**
  - `POST /relationships/rebuild` and the relationships job take the discovery lock; opportunity refresh takes the analysis lock (409 when busy).
  - The job test was written after the fix, so it was never seen failing.
- **Memory counts (M2):** exact counts are now in the aggregate signatures. The test initially passed for the wrong reason (1 and 2 transfers sit in different levels) and was reworked to 2 then 3 transfers.
- **Reorg dependents (M3):** funder facts and open expansion candidates are withdrawn when their relationship is invalidated.
- **Label history (M5):** `entity_label_events`, an append-only proposal and review history.
- **Orphaned transactions:** a transaction included again in a different canonical block replaces its orphaned row.
- **Solana backfill:**
  - It stops before the budget is spent, reserving one call per eligible provider, so finished slots are committed.
  - An accidental `git stash` / `git stash pop` was used once to check red; the working tree was verified intact afterwards and the stash list is empty.

**Stage 4:**
- **Review lifecycle and ground truth (ADR-049; v19 rebuild of `signal_decisions`):**
  - The migration test uses a hand-written v18 table with the old constraint.
  - Tests: `tests/discovery/test_review_lifecycle.py` (14).
  - Normal launches mark unmarked databases `live_network`; test apps mark nothing.
- **Candidate ranking (ADR-050):** `bip/ml/signal_ranking.py`, `signal_feature_snapshots`, `signal_ranking_runs`, the API routes and the Models page. Tests: `tests/discovery/test_signal_ranking.py` (10) cover:
  - snapshots written once from data before the event;
  - anomaly scores using only earlier candidates;
  - splits never sharing a subject;
  - the not-ready gate;
  - synthetic labels exercising every scorer without being stored;
  - determinism;
  - persisted runs from real ground truth;
  - disputed and machine decisions excluded;
  - hand-computed metrics;
  - the API.
- **Wallet classifier:** rule-baseline comparison, PR-AUC and precision@10 in its test evaluations; dead split code removed.
- **Frontend:** review form with per-state reasons, duplicate link, correction and wallet label; ground-truth badge; ranking panel; Models page; access mirror. Tests: `src/pages/stage4.test.tsx` (7).
- **Deliberately changed tests:**
  - Two Stage 3 UI tests used reasons the new per-state table refuses (`monitoring` with `evidence_sufficient`, `archived` with `duplicate`).
  - Stage 3 memory tests now read `confirmed`.
  - The client header test now includes `X-BIP-Client`.

**Verification (local):**
- **Backend:** `uv run pytest` 991 passed; `ruff check` and `ruff format --check` clean; `pyright` 0 errors.
- **Frontend:**
  - `npx vitest run` 26 files, 279 passed; `npm run typecheck` and `npm run lint` clean.
  - `npm run build`, then `npm run e2e`: 221/221. The first e2e run failed at 4 checks because the stale `dist/` lacked the header; a rebuild fixed it.
- **Fixture:** the 9-step scratch fixture passes 9/9.
- **API reference:** regenerated with `python -m bip.tools.api_contract`.
- **Secrets:** `gitleaks` is not installed here; a regex scan of the diff found nothing.

**Not verified:** CI (not pushed), a real Codespace or phone, any live chain. No live provider was contacted this round.

### 2026-11-14 — fixes from three independent reviews (owner: "fix all, test-first")

None of the three reviews (correctness, security and ML integrity, conventions) found a critical issue.

**Fixes** (test-first; each new test was seen failing first unless marked):
1. **Missing features are `None`, not 0.** This covers subject age, the anomaly score, logistic imputation with a missing count, and drift.
2. **`dispute_resolved` needs a live disagreement.** Self "second opinions" are refused.
3. **The supervised gate checks each chain's training slice.** This also adds an empty-set guard. Previously, a chain with all its subjects late crashed the evaluation.
4. **The `token_or_nft_deployer` rule is bounded by `as_of`.** It had leaked later Transfer logs into the rule baseline.
5. **Data-origin marking is careful.** A seeded or copied unmarked database becomes `unknown`, not `live_network`. The new `BIP_DATA_ORIGIN` setting lets the operator say so explicitly.
6. **The ground-truth set reads all decisions in one query.** Previously it ran one query per decided signal.

**Smaller items:**
- **Socket exemption:** the Host check is skipped only for a configured Unix socket. The socket test now configures `api.unix_socket`.
- **Model lookup:** `explain` uses the newest stored model.
- **Ties:** metrics rank ties pessimistically and deterministically.
- **Wallet labels:** a fed wallet label is withdrawn (rejected review) when its candidate leaves confirmed.
- **Review-rule drift guard:** `tests/unit/test_review_rules_mirror.py` compares the frontend copy with the server's. A temporary edit to the frontend table made it fail, and it passed again once restored.
- **Freshness in the UI:** the Signals page shows historical and expansion runs as "does not count as current".
- **Evidence class:** explanations carry `evidence_class`.
- **Types:** the frontend types gained the freshness fields.

**Without a dedicated test:**
- the decision state columns widened to 24 characters;
- the dead `_V19_DECISION_COLUMNS` constant removed;
- the subject-overlap figure computed;
- the missing-snapshot count made exact;
- orphaned transactions excluded from subject age;
- the live-run lookup paged up to 10,000 runs;
- the snapshot written in a savepoint;
- `ReviewService` typed.

**Re-review of the fixes:** no regressions; one medium and two low findings, all fixed test-first.
- **Medium:** a withdrawn wallet label was never restored. It now gets an `accepted` review when a person confirms the candidate again.
- **Low:** the label sync runs after the decision is stored, so it is guarded and a failure is logged instead of reported as a failed decision.
- **Low:** only a human can give a second opinion; a machine actor can no longer flip a confirmed candidate.

## 2026-11-13 — Stage 3 verification audit and approved repairs

**Audit (read-only):**
- **Reviews:** two independent reviews, one for intelligence correctness and one for API, UI, security and test reality.
- **End-to-end fixture:** a 9-step script drives the real app and collector against `FakeEthNode` on an isolated SQLite database. Path: observation → relationships → memory → discovery → score → review → restart → repeat. Result: 9/9.
  - The first run failed step 4 because the fake blocks are dated 2025-10, outside `lookback_hours`.
  - Re-dating the fake blocks in the scratch script only (no repository change) exercised the time-windowed detectors, and exposed H3.
- **Findings:**
  - **HIGH:** H1, H2, H3, fixed below.
  - **MEDIUM:** M1 to M6, open (see `CURRENT_STATE.md`).
  - **LOW:** a few, open.
- **Owner decision:** approved committing and pushing Stages 1–3 (`ffc5b6f`), fixing the three HIGH findings, and adding a devcontainer.

**Repairs (each test-first):**
- For H1 and H2, the new tests were seen failing before the fix.
- For H3, the tests were first run together with the implementation. Afterwards a mutation check removed the `_tx_rows` upper bound: `test_an_as_of_run_finds_candidates_in_backfilled_history` then failed on `assert later not in found`, and passed again once the bound was restored.
- Only the `_tx_rows` bound has a leak test. The other six upper bounds (large transfers, holder concentration, pools, both reserve queries, Solana discoveries, common funding) are untested.
- **H2, funder links from censored data** (`6b8760f`):
  - `funded_by` basis is `hypothesis`, with a note naming the collector threshold.
  - The memory signature includes the basis, so old observed facts are superseded rather than left current.
  - Funding and large-transfer explanations state the censoring.
  - Changed tests: `test_funded_by_uses_the_first_stored_inbound_transfer` and `test_shared_funder_is_a_labelled_hypothesis_not_a_stored_link` now expect `hypothesis`. This is deliberate. New: `test_a_funder_link_reclassified_as_hypothesis_supersedes_the_observed_fact`.
- **H1, capped rebuilds starved new data** (`a517e7a`):
  - Scans read the newest `cap` rows.
  - A truncated scan merges: weight and confidence never drop, first-seen never moves later, and evidence is appended (newest 20 kept). It still invalidates nothing.
  - A funder is proposed from a capped window only when the recipient has no unread earlier inbound transfer.
  - The existing capped-scan test comment changed from "oldest 100" to "newest 100". Three new tests.
- **H3, backfill never produced signals** (`66c4e67`, ADR-047 Proposed):
  - `DiscoveryService.run(chain, as_of=...)` and an `as_of` field on `POST /discovery/run`.
  - Upper time bounds were added to every detector query: `_tx_rows`, large transfers, holder concentration, liquidity pools, pool reserves (both queries), Solana discoveries and common funding.
  - `new_token_activity` and `holder_concentration` are skipped as not evaluable as of a past time.
  - Signals are stored on the wall clock, carry `evaluated_as_of`, and raise no alerts.
  - Validation: `as_of` must be time-zone aware, not in the future, and within `as_of_max_days` (365). The run is manual and uses the discovery lock.
  - Four new tests.
- **Codespaces:**
  - `.devcontainer/devcontainer.json`: Python 3.12, Node 22. Post-create runs `uv sync` and `npm ci`. Only 5173 is forwarded; 8000 is set to `onAutoForward: ignore`.
  - `.devcontainer/start-preview.sh`.
  - `tests/preview/serve_preview.py` adds `https://$CODESPACE_NAME-5173.$GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN` to its allowed origins.
  - Without that origin, sign-in mode would refuse every write from the forwarded URL. Verified with simulated variables: login 200, write from the codespace origin 201, write from another origin 403.
  - The start script was run here: frontend 200, API through the proxy 200, protected route without a session 401.
  - `frontend/vite.config.ts` allows exactly `$CODESPACE_NAME-5173.<domain>` in `server.allowedHosts`, and only inside a codespace.
    - Without it, Vite refused the forwarded host with "Blocked request" (403). This was found by curl with a codespace Host header after the first version.
    - Verified through the Vite proxy with simulated variables: page 200, other host 403, login 200, write from the codespace origin 201, write from another origin 403.
  - Not yet run in a real Codespace.

**Verification:**
- `uv run ruff format --check .`: 248 files already formatted.
- `uv run ruff check .`: all checks passed.
- `uv run pyright`: 0 errors, 0 warnings.
- `uv run pytest`: 953 passed in 277 s, which is 945 plus 8 new.
- `python -m bip.tools.api_contract` regenerated `docs/API_REFERENCE.md`.
- The end-to-end fixture re-run after the repairs: 9/9.
- Frontend (only `vite.config.ts` changed): re-run against the changed backend.
  - `npx vitest run`: 25 files, 269 tests passed.
  - `npm run typecheck` and `npm run lint` clean.
  - `npm run e2e`: 221/221 checks.
- No live provider was contacted.

**Incident:**
- A shell `sed` with an empty address briefly appended a line after every line of `bip/discovery/detectors.py`.
- It was detected from `git diff --stat` (4,001 insertions) before any commit. The identical inserted lines were removed and the diff re-checked (25 intended insertions).

## 2026-11-13 — Five-stage programme, Stage 3: Intelligence Memory and Autonomous Discovery

**Constraints (owner):**
- No commit, push, merge or deploy.
- No live ingestion, and discovery off or in test mode by default.
- No Ralph loop: the owner chose "No loop" when asked, given the programme's rule against unlimited loops.

**Audit (two read-only agents, checked against the source).**
- **Already present:** relationships with evidence and reorg invalidation, ten explainable detectors with scoring, discovery runs, signals with promote and dismiss, reviewed labels, cases, and the wallet workspace.
- **Missing:**
  - persistent identities beyond wallets;
  - any fact history;
  - a review lifecycle with reasons;
  - discovery seeded from no supplied address beyond the fixed detectors;
  - UI for memory, review history, run history and policies.
- **Stage 2 interfaces consumed:** stored transactions, transfers, pool events, Solana discoveries, signals, relationships, classifications and labels. All are local reads, so no fake integrations were needed.

**Built:**
- **Migration v18:** `entities`, `entity_facts`, `signal_decisions`, `discovery_examined`.
- **Services:** `bip/intel/memory.py` (MemoryService), `bip/discovery/review.py` (ReviewService), `bip/discovery/expansion.py` (ExpansionEngine), and `DiscoveryService.run_producer`.
- **Endpoints:**
  - `GET /entities/memory/search`, `GET /entities/memory/{chain}/{ref}`, `POST /entities/memory/refresh/{chain}`;
  - `POST /signals/{id}/review`, `GET /signals/{id}/decisions`, plus a `review_state` filter and field on the signals list;
  - `GET /discovery/expansion` and `POST /discovery/expand/{chain}`.
- **Jobs:** `memory_refresh` and `discovery_expansion`, both off by default.
- **Frontend (by a subagent owning `frontend/`):**
  - Workspace Memory tab;
  - SignalDetail Review and Decision history panels;
  - Signals review filter and column;
  - Coverage run history and expansion policies panels;
  - tests and the e2e layout route.

**Independent review.**
- **First reviews:**
  - **Security, limits and tests:** no Critical. Medium findings for lock, aggregate truncation, transaction length, rotating reads and test gaps.
  - **Correctness:** two High findings (deployments upgraded in place were skipped; funder facts flip-flopped) and several Medium (reorg left count and kind facts standing, expansion cooldown blocked cross-run matches, seed spread beyond the depth limit).
  - **Frontend:** no High or Medium, seven Low.
- **Repair round 1:** fixed everything above with tests. It also fixed an e2e regression: the policy cards were `article` elements and were counted as detectors.
- **Round 2:** the re-review found two findings partly fixed and three new Medium (lost queued work, the queue never emptying, the wrong kind for requeued addresses). All fixed with tests.
- **Round 3, the last allowed:** lock-first pending queue, and a guarded kind revert. No Critical, High or Medium finding is open.
- **Rulings:**
  - Confirmations from an orphaned transaction are not withdrawn.
  - Analysts may run refresh and expansion, which use local data only.
  - The review state name "accepted" is kept, documented as never a verified fact.

**Verification (final).**
- **Backend:** `uv run ruff format --check .` clean; `uv run ruff check .` all passed; `uv run pyright` 0 errors; `uv run pytest` 945 passed. This is 915 after Stage 2 plus 30 Stage 3 memory, review and expansion tests, with migration v18 covered in the forward-migration tests.
- **Frontend:** `npm run typecheck`, `npm run lint` and `npm run build` clean; `npm test` 25 files, 269 tests; `npm run e2e` 221/221 checks at 390x844, 820x1180 and 1366x850.
- **Live:** nothing in this stage contacts a provider.

**Known limitations:**
- Memory reflects only stored data, and measurements are BIP's stored counts.
- Contradiction detection covers reviewed labels only.
- Facts have no retention.
- Expansion sees only stored, active relationships.
- `opportunity_candidates` and `anomaly_observations` are not merged into the review lifecycle.
- The frontend Memory tab exists for wallets, inside the wallet workspace. Contracts, tokens and pools are reachable through the API and search, but have no dedicated page.

## 2026-11-13 — Five-stage programme, Stage 2: Multi-chain Data Acquisition

**Constraints (owner):** no commit, push or merge; no live ingestion, large backfill or VPS deployment; fixtures and isolated databases only. Nothing in this stage contacted a real provider; the only external access was reading official provider documentation pages.

**Audit (four read-only investigators).**

| Capability | Before Stage 2 | After |
|---|---|---|
| Provider registry | Implemented; quotas partly search-derived | Re-checked against official pages 2026-11-13 (Ankr, Alchemy, Infura, Helius, Solana public, PublicNode, Etherscan, BigQuery); Blockscout catalogue entry |
| Ethereum adapter | Implemented: head/finalized, blocks, receipts, adaptive range-splitting logs, ERC-20/721 decoding, deployments, native transfers | Reused; allowlisted ERC-20 transfers (live and address-filtered backfill) |
| Solana adapter | Implemented: finalized getBlock (v0/v1), instruction paths, SPL Transfer/TransferChecked decoding (stored only in the wallet path) | SPL transfers of allowlisted mints stored by the collector; pruned vs ambiguous history classified |
| Multiple providers / failover | Implemented (router health ranking, retries, 429 cooldown, circuit breaker from Stage 1) | Plus BIP's own daily usage counts and optional per-provider caps (v17) |
| Historical backfill | Missing | Bounded backward workers per chain |
| Live ingestion | Implemented (forward finalized collectors) | Pausable per direction |
| Persistent checkpoints | Stage 1 tables; historical direction unused | Used by the backfill |
| Deduplication | Implemented (unique event keys, idempotent stores) | Unchanged |
| Gap recovery | Missing (gaps recorded only) | Bounded recovery worker |
| Reorg handling | Wallet path (finality upgrade, orphaning); collectors read finalized only | Unchanged; backfill windows sit below the finalized head and check parent links |
| Coverage | Stage 1 coverage ranges | Historical extent, boundary and basis in `/ingestion/coverage/{chain}` |
| Resource controls | Per-run budgets, caps 150 calls / 90 s, disk guard | Plus live priority, feed-call reserve, daily caps, skip retry |

**Built:** see ADR-043 and ADR-044. Files:
- **New:** `bip/ingest/backfill.py`.
- **Changed:** `bip/ingest/solana_collector.py`, `bip/ingest/collector.py`, `bip/ingest/ethereum_events.py`, `bip/db/checkpoints.py`, `bip/db/schema.py`, `bip/db/engine.py` (v17), `bip/providers/state.py`, `bip/providers/registry.toml`, `bip/router/router.py`, `bip/rpc/jsonrpc.py`, `bip/chains/base.py`, `bip/config/settings.py`, `bip/api/routes/ingestion.py`, `bip/api/routes/system.py`, `bip/api/deps.py`, `bip/api/app.py`, `bip/ops/jobs.py`, `bip/ops/scheduler.py`.
- **Docs:** OPERATIONS.md, `.env.example`, the roadmap, and `API_REFERENCE.md` (regenerated).

**Independent review (four reviewers, then two scoped re-reviews).**
- **Round 1:** no Critical findings. Two High (Ethereum), seven Medium and several Low. All fixed with tests:
  - the receipt cap emptied a window;
  - after a provider fallback, receipts and logs still came from the pruned provider;
  - backfill had a narrower scope than live;
  - an unrecorded gap appeared when backfill started before live;
  - a window costlier than the budget never advanced;
  - the fallback misidentified providers;
  - the transaction cap dropped SPL transfers without recording a gap;
  - an ambiguous `-32009` was treated as a hard floor;
  - a live job skipped by a busy lock waited a full interval;
  - a stale live RUNNING status blocked historical work;
  - probes bypassed the daily budget;
  - one recovery job held the lock for both chains.
- **Round 2:** the re-review confirmed all 16 addressed and found three new Medium issues, all fixed with tests:
  - ambiguous slots would starve recovery;
  - `-32001` was reclassified for EVM too;
  - disk-full skips retried every minute.
- **Round 3:** a final scoped re-review confirmed the round-2 fixes and found that the ambiguous-slot filter ran after the 50-row limit. It now runs in the query, and a test puts 60 ambiguous rows above a real failure. That was the last of the three allowed repair rounds; no Critical, High or Medium finding is open.
- **Rulings kept:**
  - The SPL cap cannot double-count, because each slot is scanned once per run.
  - The identity-mismatch double count and a slight daily-cap overshoot from in-call failover are documented.
  - Coverage compaction is deferred.

**Verification (after round 2).**
- `uv run ruff check .`: All checks passed. `uv run ruff format --check .`: clean.
- `uv run pyright`: 0 errors.
- `uv run pytest`: 915 passed. This is 880 before the stage plus 35 new: backfill, recovery, pause, history floors, fallback, SPL, allowlist, budgets, failover, auth error, scheduler, migration v17.
- `python -m bip.tools.api_contract`: regenerated; the guard test passes.
- Frontend: only the Stage 1 type change, not re-run in Stage 2.
- **Live connectivity:** NOT tested in this stage.

**What the tests do and do not prove:** every Stage 2 path is exercised against local fake nodes (`tests/stage2/fakes.py`). That proves the logic, not live connectivity or the completeness of any real provider's data.

## 2026-11-13 — Five-stage programme, Stage 1: Intelligence Foundation and Control Plane

**Constraints for this stage (owner):** no commit, push, merge or PR; no live provider calls; no VPS or operational database changes; extend, don't replace. All work is in the working tree of `claude/modest-fermi-0yklvj` on top of `0da1ec1`.

**Audit.** Four read-only investigators (architecture, ingestion, security, quality gates), with contradictions checked against the source. Baseline before changes: `uv run ruff check .` clean, `uv run pyright` 0 errors, `uv run pytest` exit 0 (836 passed on this commit).

| Capability | State before Stage 1 | Evidence |
|---|---|---|
| Provider registry (id, chains, network, methods, archive, range limit, credentials via env, docs and check dates) | Implemented and verified | `bip/providers/registry.toml`, `registry.py`, ADR-017 |
| Provider health, lifecycle, last success/failure, consecutive failures | Implemented and verified | `bip/providers/state.py`, `status.py`, `provider_checks` |
| Capability and reliability observations | Implemented and verified | `provider_capability_observations`, `provider_reliability_observations`, prober |
| Per-run call budgets, deadlines, retries, backoff, 429 cooldown | Implemented and verified | `bip/router/router.py` (`CallBudget`), settings caps 150 calls / 90 s |
| Circuit breaker | Missing (flag defined, never read) | `settings.py` `circuit_breakers_enabled` |
| Persistent provider quota accounting across runs | Missing | per-run only |
| Chain contracts preserving block vs slot, log index vs instruction path, finality | Implemented and verified | `bip/contracts/blockchain.py`, `events.py`, `provenance.py` |
| Event identity finer than tx hash/signature | Implemented (transfers, pool events) | `transfers` unique `(chain, tx_id, event_key)` |
| Provider observations and verification history | Partial (per transaction, not per event) | `provider_observations`, `evidence_verifications` |
| BIP-owned checkpoints, historical vs live | Partial (cursor keys in `app_metadata`; live only; wallet cursors separate) | `collector.py`, `solana_collector.py`, `wallet_cursors` |
| Coverage and gap records with distinct statuses | Partial (capped JSON gap list, free-text reasons) | `collector:<chain>:gaps` |
| Reorg and finality | Implemented for Ethereum wallet path; collectors read finalized only | ADR-036 |
| Read-only status routes for checkpoints, coverage, budgets | Missing | — |
| Live backfill workers, failover across many providers, wallet profiles, review centre, Ollama agents | Deferred to Stages 2–5 | — |

**Built.**
- Migration v16 (additive): `ingestion_checkpoints` and `coverage_ranges`. It seeds both from the existing `app_metadata` cursors and gap lists, labelled `pre-v16`, with reasons redacted and a hash copied only when it provably belongs to the cursor. Legacy keys stay and are still written.
- `bip/db/checkpoints.py` `CheckpointStore`:
  - **Checkpoints:** the live and historical cursors move independently, only after commit, and only in their own direction. A provider failure changes status (`stalled` or `blocked`) and never resets the cursor. `cursor_hash` always belongs to `cursor`.
  - **Runs:** `start_run` and `finish_run` give each run a lifecycle, and `finish_run` stores `head_seen`, so lag is read without contacting a provider. `skip_to` moves past positions that were deliberately not read, and `set_boundary` lets a historical cursor end as `complete`.
  - **Coverage ranges:** statuses are `processed`, `confirmed_empty`, `incomplete`, `failed`, `skipped` and `provider_unavailable`. Rows with the same meaning that touch or overlap are merged. Gaps that a later read fully covers are marked resolved. Unobserved ranges are never shown as empty.
- **Collectors:** both write coverage and the live checkpoint after each batch is committed and before the legacy key. The Solana lag jump now runs only when the head is out of reach of one run (this fixes a failure loop found in review).
- **Router circuit breaker:** it is derived from persisted `provider_checks`, so it survives restarts. It allows a single half-open trial call, claimed only for the provider actually sent to. Probes and admin health checks bypass it.
- **Read-only routes:** `GET /ingestion/checkpoints`, `GET /ingestion/coverage/{chain}` and `GET /system/budgets`. `/settings` now returns the configured `authentication` mode instead of a stale placeholder, and the frontend type was updated to match.
- **Docs:** ADR-041 and ADR-042 (Proposed), the five-stage programme in `docs/IMPLEMENTATION_ROADMAP.md`, an as-built note in `docs/DATA_ARCHITECTURE.md` §6.4, `.env.example` circuit settings, and the regenerated `docs/API_REFERENCE.md`.

**Independent review (four reviewers on the real diff).**
- **Security:** no Critical, High or Medium findings. Three Low findings, all fixed: the half-open burst, probe interaction documented, and legacy reasons redacted.
- **Architecture, data integrity and tests: Important findings, all fixed in repair round 1:**
  - rescan duplicates in merged ranges;
  - no rule for superseding a gap;
  - the Solana lag-branch failure loop;
  - a cursor hash from another position;
  - the API parsing the collector manifest;
  - the half-open burst;
  - stale statuses;
  - eleven missing tests.
- **Repair round 2:** the round-1 re-review found the half-open token was claimed for every candidate, not only the provider called (it would have broken corroboration's pinned calls). Fixed and confirmed by a scoped re-review, with no new Critical or Important findings; the one suggested test was added. Its remaining notes are minor and accepted: a claim taken just before a budget stop expires on its own.
- **Rulings kept, and agreed by the re-reviewer:**
  - the migration seeding uses frozen literals;
  - the legacy dual-write is ordered, not atomic;
  - probe failures share the failure counter;
  - the `/settings` authentication fix stays.

**Verification (after repair round 2).**
- `uv run ruff check .`: All checks passed. `uv run ruff format --check .`: 241 files already formatted.
- `uv run pyright`: 0 errors, 0 warnings.
- `uv run pytest`: 880 passed in 167 s. This is 836 before the stage plus 44 new: store semantics, collector wiring, migration seeding and edge cases, router circuit (including pinned-call isolation), routes and required-mode protection.
- `uv run python -m bip.tools.api_contract`: regenerated; the guard test passes.
- Frontend: `npm run typecheck` clean; `npm test` 245 passed.
- **Not run:** Playwright e2e (no UI behaviour changed; only a TypeScript type), and live provider calls (forbidden for this stage).
- **Not done:** commit, push, PR, VPS changes.

**Deferred to Stage 2:**
- the collectors still read their cursor from `app_metadata`; the checkpoint table is not yet the source of truth;
- per-event provider observations;
- a `network` column and decoder version on canonical event rows;
- persistent per-provider quota accounting;
- moving wallet cursors into checkpoints;
- coverage-row compaction when providers alternate;
- a Solana orphan path outside the finalized-only collector.

## 2026-11-10 — CI result and CURRENT_STATE correction

**CI.** Head `cf07957`: backend `checks`, `frontend` and `secret-scan` each concluded `success` in both the push run and the pull-request run (explicit check-run conclusions, read after the jobs completed). Local re-run on the same commit: `uv run ruff check .` and `ruff format --check .` clean, `uv run pyright` 0 errors, `uv run pytest` 836 passed in 161 s. (A first attempt used the system Python instead of the project `.venv` and showed meaningless errors; it was discarded.)

**Documentation fix.** `CURRENT_STATE.md` still carried statements from earlier passes that the finished work contradicts: "nothing is committed", "the frontend has no sign-in screen", "no authentication", the Solana collector and reserve detection listed as blocked, "commit, push, PR not done", and the RPC hosts listed as denied. They were corrected to match the code and the live checks already recorded. No code changed.

## 2026-11-10 — A CI failure I had misread as a pass, and its cause

**What happened.** The backend `checks` job failed once, on commit `a08a911`: `tests/discovery/test_pipeline.py::test_per_run_signal_cap_is_applied_and_reported` raised a `UNIQUE constraint failed: transactions.tx_id` while seeding its own data (830 passed, 1 failed). I had not seen it: I read "check suite completed" events as "passed", but that event only says the suite finished. Only the explicit `check_run ... conclusion: failure` event carries the result, and I had not read that notification until later.

**Cause (reproduced).** The test helper `baseline()` built transaction ids from Python's built-in `hash(token)`, which is randomised per process, so two of the three tokens occasionally fell into the same bucket (`hash % 1000`) and collided: about a 1-in-300 chance per run. In 1,501 hash seeds, 5 collide (0.33%); the old line fails on the colliding seeds I ran (184, 592, 659) and the fixed line passes on all five. It never failed locally because the chance per run is small.

**Fix.** The helper now uses `int(token, 16)`, which is stable. The same pattern was not found anywhere else in the tests.

**Process change.** From now on a green CI means an explicit success conclusion on the head commit's check runs, not a completed suite.

## 2026-11-10 — Deployment rehearsal

**Built.** `deploy/rehearse.sh`: in a temporary directory, with the real entry points, it writes the env file from `deploy/bip.env.production.example`, runs the pre-flight check (no FAIL, scheduled provider use off), creates an administrator from a root-only password file, starts `python -m bip.api` on a Unix socket, then checks: socket mode 660, public health 200, anonymous protected route 401, sign-in with an HttpOnly, SameSite=Strict, Secure cookie, system report 200 with a session, a write without the CSRF token 403, a database-only discovery pass with the token 201, and a backup that verifies.

**Result.** 11 of 11 steps passed on the first run; nothing contacted a provider and everything was removed afterwards.

**Not covered.** systemd (only `systemd-analyze verify` was run on the unit files), a reverse proxy reaching the socket, TLS and certificate renewal, a real host's disk and users, off-site backup copies. The kit has still never run on a real host.

## 2026-11-10 — Unix-socket listener (closes the proxy-header limit from the security review)

**Why.** A TCP listener on loopback lets any local process on a shared host connect and spoof the client address used for throttling and audit (the uvicorn proxy-header rewrite plus BIP's own trust flag).

**Built.** `BIP_API__UNIX_SOCKET` (absolute path); `python -m bip.api` serves on it with `proxy_headers=False`; `bind_unix_socket` binds under umask 0117 and clears only a stale *socket* (a regular file is never removed). Found by a test, not assumed: uvicorn `chmod`s its own Unix sockets to 0666, so BIP binds the socket itself and passes it in. The exposure guard accepts a socket listener, `preflight` treats it as a pass, the production env template, systemd unit (`RuntimeDirectory=bip`, mode 0750), Caddy and nginx examples and the README use it.

**Verification.** `tests/unit/test_unix_socket.py` 5 passed (config shape; a real server answering over a real socket with mode exactly 0660; stale-socket handling; a regular file refused; guard and preflight). `pytest` 836 passed; ruff and pyright clean; `systemd-analyze verify` on the edited unit reports only that `python` is not installed here.

**Not verified.** Proxy-to-socket access with a real Caddy or nginx (neither is installed here).

## 2026-11-10 — Solana mint layout seen on a real event

**Scans (solana-public, read-only, throwaway database, 141, 81, 34 and 23 requests; one hit the 85 s deadline, as designed).** About 100,000 transactions across about 350 finalized slots. Two real `InitializeMint2` instructions were seen. The first produced no discovery, which I treated as a possible decoder miss until an independent cross-check (System `CreateAccount`, 82 bytes, Token program) and a printout at the moment of detection showed the second one decoded correctly (`mint`, 9 decimals, `InitializeMint2`); the first was a failed transaction, which the decoder skips by design. Raydium AMM v4 and Orca instructions occurred (tens and hundreds) but none was a pool initialisation, and no loader deployment occurred.

**Conclusion.** The SPL mint layout is verified live. Program deployments and pool initialisations are still verified only against official sources and synthetic fixtures.

## 2026-11-10 — Pool and reserve decoders checked on real events; a design flaw found by real data

**Live checks (free endpoint, throwaway database, 6, 71 and 72 requests; all within the 150-request limit).** Querying the documented Uniswap v2 and v3 factory addresses directly: 62 real `PairCreated` and 14 real `PoolCreated` logs over 5,000 blocks, every one decoded; sampled pools have contract code and pair with real WETH; fees 100 and 500 decoded. 20 real `Sync` logs of a new pair decoded to plausible reserves (an initial 4.5e27 / 0.5 ETH, then a small swap). The real collector, pointed at a window containing a real new v2 pair, stored the pool, watched its reserves and stored 7 (then 4, on a second pair) real reserve updates; no rejection and no continuity mismatch.

**Flaw found only by real data.** `token_transfers_stored` hit its cap of 500 because the new pair was TOKEN/WETH, so every WETH transfer counted as a pooled-token transfer and crowded out the new token. Fixed test-first: base assets (WETH9, from Uniswap's Ethereum deployments page) and hubs (tokens in at least `hub_pool_threshold` = 25 stored pools) are excluded from per-event storage. Re-run on a second real pair: 4 transfers stored, not 500.

**Still not seen live.** Solana mint, program-deployment and pool-initialisation events; factories other than Uniswap v2/v3.

## 2026-11-10 — "Run discovery now" and readable record lists

**Built.** Signals page: an analyst-gated "Run discovery now" that calls `POST /discovery/run` (reads the database only; reports new, merged and expired counts and alerts; refreshes the list). Signal detail: a list of records (for example the top addresses of a concentration signal) renders as rows with explorer links instead of raw JSON.

**Defect caught by its own test.** The first version of the button used `!can` on the object `useCan` returns, so it was never disabled for a viewer. The viewer test failed; fixed to `can.allowed`, with the reason as the button title.

**Verification.** `tsc` and `eslint` clean, `vitest` 245 passed, build ok, `npm run e2e` 221/221 (adds a check that a pass runs against the offline backend and sends no collector request). CI on the four previous head commits (80a255f, 13ad93c, b39e492, 851f0a9) reported complete with nothing failing.

## 2026-11-10 — Preview walkthrough found pages without the demo banner

**Finding.** A walkthrough of the populated preview (every new page at phone, tablet and desktop sizes) showed that Attribution and System displayed synthetic records with no "Demo data" banner, contradicting the documentation. Cause: `/entities/labels` and `/system/health` did not return `data_origin`.

**Fix (backend test first).** Both endpoints, and `GET /entities/{chain}/{ref}/labels`, now return `data_origin`; both pages render the existing banner; four unit tests and two browser checks pin it.

**Verification.** `pytest` 829 passed; frontend `tsc`, `eslint`, build, `vitest` 242 passed, `npm run e2e` 219/219. `systemd-analyze verify` on the three unit files reports only that `python` and `rsync` are not installed on this machine (no unknown directives).

## 2026-11-10 — Populated SYNTHETIC preview and small contract fixes

**Built.** `tests/preview/serve_preview.py` (`make preview`): throwaway database, invented data near "now", real detectors (13 signals from 9 of 10 detectors), two SYNTHETIC labels, three SYNTHETIC users with a one-off password, `offline_fixture` marker so every page shows "Demo data". Checked in a real browser: sign-in, Signals at 390 px wide (13 rows, no overflow, banner and role badge visible). The Ethereum collector's `not_covered` no longer says Solana has no collector, and its `limits` include `run_deadline_s`.

**Not done.** The preview shows invented data, so it proves the pages work, not that real collected data looks like this. No "Run discovery" button in the UI yet.

## 2026-11-10 — Dashboards for signals, alerts, coverage, attribution and system health (subagent owning `frontend/`, verified independently)

**Built.** Routes `/signals`, `/signals/:id`, `/alerts`, `/coverage`, `/attribution`, `/system` and nav entries (with an unread-alerts badge that never raises an alert), shared `Modal`, `ExternalLink` (http(s) only, `rel="noopener noreferrer"`), `EvidenceEditor`, discovery components, role gating through the existing `useCan`/`RoleHint`. "Run once" on Coverage never fires on page load or Cancel; Confirm sends exactly one request and shows the returned manifest.

**Verification (re-run by the integrating session).** `tsc` clean, `eslint` clean, `vitest` 238 passed (was 179), `npm run build` ok, `npm run e2e` 217/217 (was 165), including layout (no overflow, tap targets) for the five new routes at three viewport sizes and a viewer seeing both collector buttons disabled with the admin hint.

**Gaps.** No signal-creating API exists and the offline fake nodes' timestamps fall outside the detector lookback windows, so the browser suite shows empty states for signals and alerts; populated paths are unit-tested with SYNTHETIC data. No "Run discovery" control yet. The unit tests, not real collected data, are the only evidence the pages render real signals correctly.

## 2026-11-10 — Independent review of the collectors and labels, and the fixes

**Review.** A correctness reviewer and a security reviewer (read-only, a different agent type because the review-council agents refuse a checkout whose CLAUDE.md or CI file differs from `origin/main`; the guard was not bypassed) read the new backend and deploy files. Findings were checked against the code before acting.

**Fixed (each with a failing-then-passing test in `tests/collector/test_review_fixes.py`, `test_solana.py`, `test_labels.py`, `test_preflight.py`).** Ethereum: cursor and feeds after an exception, per-block give-up with a persistent gap list, receipt cap no longer half-stores a block, budget arithmetic from the caps (validated), reverted creations (also fixed at the parser), pool kept when its evidence transaction is missing, atomic token counting, oversize blocks skipped and recorded, trusted-factory ranking and restriction, token row cap, redacted messages. Solana: -32004 not treated as skipped, outage never burns attempts, continuity across runs (slot:hash), one gap per lag jump, discoveries outside the transaction cap, missing meta not success, base58 decode only for known programs and short data. Detectors: reserve truncation by pool name, per-row queries in the pool detector. Labels: requester recorded (migration v15), bundle-only citations, unverified evidence not reported as verified, review race, re-proposal after rejection, name normalisation, one model call at a time. Deploy: no env values on a command line, reviewed-commit checkout, `UMask=0077`, restart limits, no `--delete` off-site copy, `/srv` permissions, preflight `--use-environment`, stricter file mode.

**Verification.** `pytest -q` 828 passed; ruff and pyright clean; live re-check of both collectors on the throwaway database (60 and 21 requests): no continuity mismatches, nothing rejected.

**Not changed.** Serving the API on a Unix socket (loopback callers can still set the client address; documented). A second provider to confirm finalized blocks. Moving gaps to a table (the newest 50 and an evicted counter are kept).

## 2026-11-10 — Frontend sign-in (delegated to a subagent owning `frontend/`, verified independently)

**Built.** `frontend/src/auth/` (in-memory session store, actions, provider, sign-in form with `Retry-After` countdown, user menu, change-password dialog, role gating via a TypeScript port of `bip/api/access.py`), API client changes (credentials, `X-CSRF-Token` on writes, 401 returns to sign-in, 403/429 messages), `frontend/e2e/auth.mjs` (real backend in `required` mode on a throwaway data root with four SYNTHETIC users).

**Verification (run by the integrating session, not taken from the report).** `tsc` clean, `eslint` clean, `vitest` 179 passed (was 96), `npm run build` ok, `npm run e2e` 165/165 (was 49): the 116 new checks cover anonymous gate, wrong password, login, cookie flags, reload, CSRF-protected write, missing-token 403, role 403, sign-out, viewer controls disabled, and no overflow with 44 px touch targets at phone, tablet and desktop sizes. No backend change was needed.

**Notes.** `GET /auth/status` returns no CSRF token (the UI uses `/auth/me`). A locked account and a wrong password both return 401 `invalid_credentials`. `/auth/password` accepts a new password of any length from 1 to 256 in the schema while user creation requires 12; the dialog enforces 12 locally. Not exercised: a real deployment behind HTTPS.

## 2026-11-09/10 — Live verification of both collectors (free public endpoints, throwaway database)

**What ran.** Each invocation used a temporary data root, the registry's no-cost public providers (publicnode-eth, solana-public), read-only methods, and at most 110 requests (limit 150) and well under 90 s. No live database was touched.

**Ethereum.** One block: 20 requests, 315 transactions, 285 Transfer logs, 69 tokens. Twenty blocks (26376729 to 26376748): 6,201 transactions, 15,088 Transfer logs (about 750 per block), 448 tokens, 8 creations, 25 large native transfers stored, 110 requests. **Finding:** PublicNode answers an address-less `eth_getLogs` over several blocks with "Please specify an address in your request"; it was classified as transient and retried three times, which aborted the feed. Fixed: the message is a too-broad-query error, and the adapter goes straight to single-block windows (no bisecting) and remembers it; the feed call reserve scales with the block count; regression test reproduces the behaviour in the fake node. Pools and reserves: none occurred in the window.

**Solana.** 2 slots (3 requests), 40 slots (41 requests, 10,097 transactions, 2,680 failed), plus two read-only diagnostics (101 and 26 requests). Transaction versions seen in one 25-slot sample: legacy 2,296, v0 2,419, v1 2,012. Instruction decoding worked on every transaction (the 59 with none decoded were zero-instruction version-1 no-ops). No InitializeMint, program deployment or pool initialisation occurred in about 142 slots, so those layouts are not confirmed on a live occurrence.

**Not done.** Scheduled collection stays off. Nothing was run against a paid or keyed endpoint.

## 2026-11-09 — Deployment kit and pre-flight check

**Built.** `deploy/` (production env template, hardened systemd unit with `ExecStartPre` pre-flight, Caddy and nginx examples, off-disk backup timer, README with install/verify/upgrade/rollback and the procedure for turning collection on), `bip/tools/preflight.py` (PASS/WARN/FAIL; exit 1 on FAIL, 2 on invalid config; prints names and reasons, never values).

**Verification.** `tests/unit/test_preflight.py` 12 passed, including that the shipped template loads through the real settings with no FAIL and with collection off. Nothing was installed or started anywhere; `systemd-analyze` and the proxies were not run.

## 2026-11-09 — Reviewed entity labels and attribution (migration v14)

**Built.** `bip/entities/labels.py` (`EntityLabelService`), table `entity_labels`, routes `POST/GET /entities/labels`, `POST /entities/labels/{id}/review`, `POST /entities/labels/suggest`, `GET /entities/{chain}/{ref}/labels`, role policy (propose/review = analyst), `attribution` on every signal, ADR-040. Ollama check: provider `none`, port 11434 refused; no model contacted.

**Verification.** `tests/entities/test_labels.py` 6 passed (evidence rules, human-only approval, separate reviewer, attribution only after approval, ungrounded AI suggestions dropped, unconfigured model reported). ruff and pyright clean.

## 2026-11-09 — Solana finalized-slot collector (migration v13)

**Why.** The master goal asks for Solana finalized-slot collection with program/mint/pool discovery.

**Built.** `bip/chains/solana/programs.py` (wallet-agnostic decoders; every program id, tag and account position checked against Agave, solana-program/token, anza-xyz/solana-sdk, raydium-amm and orca-so/whirlpools sources on 2026-11-09; docs.raydium.io and dev.orca.so were not reachable from the session), `SolanaAdapter.block_full`, `bip/ingest/solana_collector.py`, migration v13 (`solana_discoveries`), detector `solana_new_entities`, routes `GET /discovery/collector/solana` and `POST /discovery/collect/solana`, job `solana_scan` (off), `solana_collector.*` settings (150 requests, 90 s ceilings), fake node support for full synthetic blocks.

**Verification (exact).** Full `pytest -q`: **783 passed**; `ruff` and `pyright` clean; API reference regenerated. All blocks are synthetic fixtures.

**Not done.** Never run live (block size and latency on a free endpoint unmeasured); Orca `initialize_pool_v2`, Raydium CPMM/CLMM and other pool programs unrecognised; no SPL transfers, holders or reserves for Solana.

## 2026-11-09 — ERC-20 coverage, pool reserves, holder concentration (migration v12)

**Why.** The new master goal asks for ERC-20 coverage, liquidity/reserve changes and holder concentration. The earlier "volume exceeds provider limits" claim was unmeasured, so the feed was built to split windows instead.

**Built.** `bip/ingest/ethereum_events.py` (`EventFeeds`: pools, tokens, reserves; collector now orchestrates blocks only), `EthereumAdapter.event_logs_adaptive` (topic and optional address filter, adaptive split), `decode_sync`, migration v12 (`token_stats`, `pool_reserves`), detectors `new_token_activity`, `pool_reserve_change`, `holder_concentration`, `RawCandidate.evidence_finalized`, settings caps (`ingest`/`collector` limited to 150 calls and 90 s by validators).

**Verification (exact).** Full `pytest -q`: **776 passed**. `ruff` and `pyright` clean; `docs/API_REFERENCE.md` regenerated. Synthetic fixtures only; the Sync topic is checked against a test-only keccak-256.

**Not done.** Never run live, so real log volume per block is unmeasured. v3 pool reserves, real balances (`eth_call`), Solana, reviewed labels and exchange attribution are still to do.

## 2026-11-08 — Pool-creation events and the `new_liquidity_pool` detector

**Why.** The goal review still named new pools as unmet. Factory creation events are rare and filterable by topic, so a bounded unfiltered `eth_getLogs` over the planned finalized range is feasible; ERC-20 transfers chain-wide are not (volume).

**Built.** Migration v11 (`pool_events`, additive), `bip/chains/ethereum/pools.py` (v2/v3-style decoders), `EthereumAdapter.pool_logs_adaptive`, pool scan in `ChainCollector` (runs before blocks so a failure leaves the cursor unchanged; checks the log's block hash against the canonical header; stores the creating transaction as evidence), `collector.max_pools_per_run`, detector `new_liquidity_pool` (confidence cap 0.4, flags tokens with no earlier stored pool), pool entities in `/search`.

**Verification (exact).** Event-signature constants checked against a test-only keccak-256. `tests/collector/` 14 passed (decode v2/v3, store with transaction, rerun dedupes, wrong block hash rejected, cap disclosed, detector scoring and search). Full `pytest -q`: **768 passed**. `ruff check`, `ruff format --check` clean; `pyright` 0 errors; `docs/API_REFERENCE.md` regenerated; forward-migration test now ends at v11.

**Not done / still BLOCKED.** Never run live. No Solana collector, chain-wide ERC-20 transfer ingestion, event streams, liquidity/reserve/holder changes, exchange attribution, frontend sign-in, real labels, live LLM.

## 2026-11-08 — Bounded Ethereum block collector (discovery without supplied addresses)

**Why.** The goal review said discovery only sees wallets BIP was asked to ingest. This adds the one slice that can be built and proven offline: contract creations and large native transfers read from finalized blocks.

**Built.** `EthereumAdapter.block_full`, `bip/ingest/collector.py` (`ChainCollector`), settings `collector.*` and `workers.collector_enabled`, routes `GET /discovery/collector` and `POST /discovery/collect/ethereum` (admin by the fail-closed table), scheduler job `chain_scan` (off, `contacts_providers`), ADR-038 (Proposed). Finalized blocks only; cursor in `app_metadata`; bounded by blocks, receipts, call budget and deadline; each run writes a coverage manifest; no raw block payloads retained.

**Verification (exact).** `tests/collector/` 9 passed (creations and large transfers stored, small ones counted and dropped, resume and idle runs, block and receipt caps disclosed, missing receipt not turned into a transfer, no finalized tag means nothing read, analyst gets 403, job off by default, `DiscoveryService` raises a signal for a collected contract). Full `pytest -q`: **763 passed**. `ruff check` and `ruff format --check` clean, `pyright` 0 errors, `docs/API_REFERENCE.md` regenerated.

**Not done / still BLOCKED.** Never run against a live provider. No Solana collector, ERC-20 or event-stream ingestion, DEX/pool/liquidity detection, holder concentration or exchange attribution; these need a data source and cost decision from the owner.

## 2026-11-08 — Backend completion pass (discovery, entities, security, operations)

**Scope.** The "complete the backend" goal: inventory what exists, finish ingestion correctness, add autonomous discovery over stored data, entities and relationships, authentication and operations, and document the API. Offline only: no provider, VPS or paid service was contacted, and nothing is committed.

**Inventory at the start (verified in the repository, not from documents).** Present: 83 routes, ingestion, intelligence, ML (rules, scikit-learn training, Isolation Forest, HDBSCAN, calibration), AI research with a loopback-only Ollama provider, watchlists, cases. Missing: alerts, a signal registry, any authentication or CSRF, API throttling, a scheduler or workers, backups, persisted relationships, transaction/token/contract views, case notes and timeline, model drift monitoring, false-positive measurement.

**Built**
- Ingestion review fixes (see the Phase A entry): orphan exclusion and marking, bounded repair, partial Ethereum repair, token-scan coverage under a budget, listing finality.
- `bip/discovery/`: five detectors, scoring, deduplication, cooldown, expiry, alerts, notification abstraction, promotion, performance statistics; routes `/discovery/*`, `/signals/*`, `/alerts/*`, `/alert-rules/*`. Migration v7.
- Security: `bip/security/{auth,passwords,ratelimit}.py`, `bip/api/{access,protection}.py`, `bip/tools/users.py`; routes `/auth/*`, `/users`, `/audit`. Migration v8.
- Operations: `bip/ops/{metrics,backup,scheduler,jobs,health}.py`; routes `/system/*`. Migration v9.
- Entities: `bip/entities/{relationships,views,cases}.py`; routes `/search`, `/transactions/*`, `/tokens/*`, `/contracts/*`, `/entities/*/relationships`, `/relationships/*`, `/investigations/*/notes|timeline`. Migration v10.
- ML: `bip/ml/drift.py` (`/ml/models/{name}/{version}/drift`).
- Docs: `docs/API_REFERENCE.md` (generated, guarded by a test), `docs/OPERATIONS.md`, `docs/SIGNALS.md`, ADR-032 to ADR-037 (Proposed), `.env.example`.

**Defects found by the new tests and fixed** (each had a failing test first)
1. PSI drift used raw PSI, which is biased upward at small samples (about 0.6 at n=30), so it would have flagged healthy models. Fixed with bin-count adaptation and bias correction; the floor became 50 samples after measuring false-drift rates on seeded simulated data (400 pairs per size: 5.7% at 30 values, 1.0% at 50; a 1.5-sigma shift detected 100% at 50).
2. PSI bins closed on the wrong edge, so a constant reference could not see a shift upward. Fixed.
3. Same-second backups sorted wrongly (`bip-X.sqlite3` after `bip-X-2.sqlite3`), so pruning could keep the oldest. Fixed with a numeric sequence key.
4. The health report warned "no healthy provider" on a fresh install where nothing had been checked. It now warns only when providers were checked and none passed.
5. Ethereum transfer and balance listings showed the finality they were parsed with, not the transaction's current one (fixed earlier in this pass).

**Verification (exact)**
- `pytest -q`: **754 passed** (155.87 s). The suite was 512 at the start of the work.
- `ruff check bip tests`: all checks passed. `ruff format --check bip tests`: 195 files already formatted.
- `pyright --pythonpath .venv/bin/python bip tests`: **0 errors, 0 warnings** (whole project).
- Frontend: `tsc -b --noEmit` clean, `eslint .` clean, `vitest run` **96 passed** (15 files), `vite build` ok.
- E2E `npm run e2e` (offline): **49/49** checks passed; the live-provider step is skipped unless `BIP_E2E_REAL_NETWORK=1`.
- Migrations: a database frozen at v6 with data upgrades to v10 with the data intact; `PRAGMA integrity_check` ok and `foreign_key_check` empty; migrating again is a no-op; an interrupted upgrade resumes; a newer database is refused (`tests/api/test_forward_migration.py`).
- `gitleaks dir .`: no leaks found (8.75 MB scanned).

**Measurements (this sandbox, synthetic data at the configured caps; not the VPS).** 20 000 transactions and 20 000 transfers over 2 000 wallets: discovery pass 2.7 s (all five detectors, rows scanned 20 000 each, none capped); relationship rebuild 1.0 s first, 1.3 s second; verified backup 0.33 s for 54 MB; database 52.6 MB with a 54.1 MB WAL before a checkpoint; peak resident memory 113 MB. Random data produced few signals (9 deployments); these figures bound the cost of reading the cap, not the yield on real data.

**Not done / blocked** (also in `CURRENT_STATE.md`)
- Whole-chain discovery and any live collector; event streams; DEX/liquidity/pool detection; Solana program deployment; holder concentration; exchange attribution. Each needs a provider or data-source and cost decision.
- Frontend sign-in and CSRF header handling; until built, `BIP_AUTH__MODE=required` cannot be used with the bundled UI.
- Real reviewed labels; a real trained model; a live LLM.
- No load test on the VPS; SQLite limits are assessed, not measured there.
- Database changes: migrations v7 to v10 are additive and idempotent. Rollback of the code leaves the extra tables unused; nothing is dropped. To undo the data effects of orphaning, reset `transactions.finality`, `analysis_runs.status = 'stale'`, `signals.status = 'invalidated'` and `entity_relationships.status = 'invalidated'` for the affected rows.

## 2026-11-08 — Phase A: independent review and Stage 1 completion

**Independent review findings (bugs the 523 existing tests did not catch)**
1. Orphaned data reached intelligence: nothing excluded `orphaned` transactions, and `ORPHANED` was never set. Hash mismatches were only reported and re-checked on every run, so five mismatching blocks starved the finality check forever.
2. Placeholder rows had no retry bound: a permanently unavailable transaction was re-fetched on every run and could crowd out newer placeholders.
3. Ethereum rows stored without a receipt (`partial`) were never repaired once they fell out of the scan window, and `store_transaction` could not upgrade them.
4. Token-account scanning used the full per-run limit for every account (up to 5 x limit calls), and a call-budget stop skipped the token-account coverage fields entirely.
5. Transfer and balance-change listings reported the finality they were parsed with, so they showed `observed` after the transaction was finalized or orphaned.
6. Correction to the Stage 1 report: wallet-level aggregation of token-account activity already existed. `wallet_transfers`, `wallet_transactions` (through balance-change ownership) and `load_wallet` all include owned token accounts. A regression test now covers an incoming transfer whose key is not in the wallet's account keys.
7. The existing end-to-end smoke test made a real provider health-check request. It failed in this sandbox (no data returned), but it was an unintended live contact. That step now runs only with `BIP_E2E_REAL_NETWORK=1`.

**Changes**
- `bip/db/canonical.py`: shared orphan exclusion (`orphaned_tx_ids`, `canonical_transaction`), applied in `intel/dataset.py`, `intel/graph.py`, `intel/opportunities.py`, `intel/service.py` and `ml/rules.py`.
- `ObservationStore.mark_ethereum_block_orphaned`: a stored block whose hash differs from the chain at a finalized height is marked `orphaned` (rows kept). In the same transaction it adds an `invalidated` verification per transaction and marks succeeded analyses of every involved address `stale`. Orphaned rows are not re-checked.
- Retry bound: `repair_max_attempts` (default 5), counted from recorded ingestion errors, least-retried first. No schema change.
- `store_transaction` upgrades `failed -> complete|partial` and `partial -> complete`; `_repair_ethereum_partials` rebuilds the wallet's Transfer logs from the receipt (`_receipt_logs`, same filter as the scan).
- Token scan: per-account signature limit (`solana_token_account_signature_limit`, default 10) and coverage written in a `finally`, so a budget stop is reported.
- Listings use the owning transaction's finality (`tx_finality`).

**Verification (exact)**
- `pytest -q` -> 530 passed (baseline 512 at the start of Stage 1; 18 new tests across two files).
- `ruff check bip tests` -> all checks passed; `ruff format --check bip tests` -> formatted.
- `pyright --pythonpath .venv/bin/python bip tests` -> 0 errors, 0 warnings (whole project).
- Frontend: `tsc -b --noEmit` clean, `eslint .` clean, `vitest run` -> 96 passed in 15 files.
- E2E (`npm run e2e`, offline): 49/49 checks passed. The first run (51/51) included the live health-check step described above.
- SQLite on a populated fixture database: `PRAGMA integrity_check` ok, `foreign_key_check` empty, WAL mode, `migrate()` idempotent at version 6.

**Database implications**: no schema migration. New writes: `transactions.finality = 'orphaned'`, `evidence_verifications.outcome = 'invalidated'` rows, `analysis_runs.status = 'stale'`. Rollback: revert the code; rows already marked orphaned/stale keep that label and can be reset with an UPDATE if a block is later proven canonical.

**Remaining limitations**
- Orphaning trusts one provider's canonical hash at a finalized height. A second-provider confirmation is not implemented.
- ML features and classifications already stored are not recomputed after an orphan event.
- Dashboard counters still count orphaned transactions.

## 2026-11-08 — Stage 1: ingestion correctness

**Scope (from the owner's brief)**: Solana transaction-version compatibility, Solana token-account coverage, recovery of incomplete records, Ethereum adaptive log splitting, finality upgrades and reorg checks, and accurate tests. Stopped at the Stage 1 checkpoint.

**Verified before changing code**
- Official Solana docs: v1 transactions are live on mainnet (`txv1` active at epoch 1035, 2026-09-15). A request with `maxSupportedTransactionVersion` 0 fails with -32015 on v1 transactions. Source: https://solana.com/upgrades/larger-transaction-sizes
- `bip/chains/solana/adapter.py`, `prober.py` requested 0. `parse.py` accepted only legacy and 0. Wallet ingest stored a placeholder on -32015 (classified `not_available`).
- `store_transaction` returned early for existing rows, so placeholders could never be replaced.
- `transfer_logs` raised `ParseError` above 10,000 logs, and the splitter did not catch it, so the whole log stage aborted.
- The router refused `getTokenAccountsByOwner` because no provider declared it.

**Changes**
- Solana: version 1 requested in `adapter.py` (transactions and blocks) and in `prober.py`. `parse.py` accepts 0 and 1, rejects anything else, and keeps the v0 lookup-table check. Docstrings updated.
- Solana token accounts: `SolanaAdapter.token_accounts_by_owner` (`getTokenAccountsByOwner`, one call per token program: SPL Token and Token-2022). `WalletIngestor._scan_token_accounts` walks up to `solana_token_accounts_limit` (default 5) accounts with their own cursors. Discovery and per-account failures are reported in coverage, and `not_covered` says what is unknown.
- Solana recovery: `_repair_solana_placeholders` re-fetches placeholder rows for the wallet and its scanned token accounts (bounded by `solana_placeholder_repair_limit`, default 10). `Repository.store_transaction` replaces a `parse_status='failed'` row only with a complete or partial parse. Stored parsed rows are never overwritten.
- Solana walk: the cursor-based signature walk was moved unchanged into `_walk_signatures` and reused for wallets and token accounts.
- Registry: `getTokenAccountsByOwner` added to the global Solana allow-list, and to the public endpoint only. The official RPC reference lists it. Other providers are not assumed to support it (CLAUDE.md §2 item 3). Not probed live.
- Ethereum: `transfer_logs` reports an oversized result as `range_too_large`, so `transfer_logs_adaptive` splits the window. An unsplittable single block still fails the run, loudly.
- Ethereum finality: `_upgrade_ethereum_finality` checks up to 5 distinct stored blocks per run against the canonical hash (the finalized block directly, lower blocks with one `eth_getBlockByNumber` each). Matching blocks are upgraded in `transactions` (column and `provenance_json`) and `blocks`. Mismatches are recorded as `reorg_detected` and left unchanged. Invalidation stays with `intel/verify.py`.
- Settings: `solana_token_accounts_limit` (0–50, default 5), `solana_placeholder_repair_limit` (0–100, default 10). No schema migration; `SCHEMA_VERSION` is unchanged (6).
- Docs: `DATA_ACQUISITION_STRATEGY.md` and `DEVELOPMENT.md` no longer say to request version 0.

**Tests**
- New: `tests/stage2/test_stage1_ingestion.py` (11 tests): v1 parse, unknown version rejection, v1 request for transactions and blocks, v1 end-to-end ingestion, placeholder repair, token-account discovery and coverage, discovery failure not reported as complete, oversized log split, unsplittable block failure, finality upgrade when canonical, and reorg mismatch reported and not upgraded.
- Changed: `tests/stage2/test_parsers.py`: the invalid-version case now uses version 2 (1 is valid). `tests/stage2/fakes.py`: the Solana fake answers `getTokenAccountsByOwner` with no accounts by default.

**Verification (exact results)**
- Baseline before changes: `pytest -q` → 512 passed (94.28 s).
- After changes: `pytest -q` → 523 passed (89.53 s).
- `ruff check bip tests` → All checks passed. `ruff format --check bip tests` → 145 files already formatted.
- `pyright --pythonpath .venv/bin/python` on the changed modules and new tests → 0 errors, 0 warnings.
- Full-project pyright was not run in this stage.

**Not done (kept for the next stages)**
- Downstream intelligence (`bip/intel/dataset.py` and related) does not filter provisional events by finality. `ORPHANED` is still never set.
- Ethereum partial rows (no receipt) are not re-fetched. Provider-specific retry and rate tuning is unchanged.
- No live provider was contacted. Real Solana v1 behaviour, real token-account volume, and the public endpoint's acceptance of `getTokenAccountsByOwner` are unverified.
- Token-account history is stored under the token-account address. Wallet endpoints show only the wallet's own history. Intelligence merges owned token accounts where balance metadata links them.

## 2026-11-07 — Independent review of the UI integration and fixes

A fresh reviewer on the most capable model reviewed `61a3e49..d120e93`. It reported 0 Critical, 4 Important and 11 Minor findings, with a verdict of "With fixes". Two Minor findings were regraded to Important by their effect on a user: a broken ML wheel on Termux, and docs claiming redirects. All Important findings were fixed in `a679689`, each with a test that failed first.

- Final review: fresh reviewer (most capable model) on 61a3e49..d120e93 — 0 Critical, 4 Important, 11 Minor; verdict "With fixes"
- Final: fixed I1 provenance wording — runpage 'says classification did not run…' RED→GREEN, suite 96/96 (a679689)
- Final: fixed I2 contradictory limitations — test_missing_runtime_limitations_do_not_contradict_each_other RED→GREEN, pytest 512 (a679689)
- Final: fixed M8 (regraded Important: Termux broken wheel) — test_partly_installed_runtime_degrades_to_rules_instead_of_failing RED→GREEN (a679689)
- Final: fixed I3 single offline state + Retry recovery — App tests 'one offline notice' and 'recovers every panel' RED→GREEN (a679689)
- Final: fixed I4 workspace refresh unmount — 'keeps the tab mounted…' RED→GREEN (a679689)
- Final: fixed M13 (regraded Important: docs claimed redirects) — ADR-030/spec corrected; docs-only (a679689)
- Final: Ruling: test 'shows a connection error with a working retry' now expects a quiet panel status, not role=alert — that alert storm was finding I3 — cost if wrong: none
- Final: Ruling: earlier optional-ML test asserted runtime.backend() is never called; corrected to 'never successfully loaded' — classify now probes the runtime by design; subprocess test proves no ML import — cost if wrong: none
- Final: minor (deferred): malformed run id deep link shows generic 422 error instead of not-found
- Final: minor (deferred): wallet status endpoint uses _require_chain (stricter than sibling wallet endpoints)
- Final: minor (deferred): wallet_status data_origin vocabulary 'live' vs 'stored_observations' elsewhere
- Final: minor (deferred): watchlist skipped-entries hint says 'run again' though the backend always analyses the first N
- Final: minor (deferred): case Unlink failure is silent
- Final: minor (deferred): recent wallets are recorded but not displayed anywhere yet
- Final: minor (deferred): Copy does nothing on non-secure origins (LAN IP) — no feedback
- Final: minor (deferred): ADR-030/031 still Proposed though implemented (owner approval needed)
- Final: minor (deferred): label review buttons shown on already-trainable labels; counts() rescans labels per chain

**Checks after the fixes**
```
$ uv run pytest -q   -> 512 passed
$ npx vitest run     -> 96 passed
$ npm run e2e        -> 51/51 checks passed
$ node e2e/layout.mjs -> all checks passed (412x915 touch, 1360x900)
```

## 2026-11-07 — UI integration: phone fixes, Android backend path, Milestones B–D, e2e

**Starting point:** `61a3e49` (Milestone A), clean tree. Work followed the Superpowers workflow: systematic-debugging for the reported defects, then writing-plans (`docs/superpowers/plans/2026-11-06-bip-ui-integration.md`), executing-plans and test-driven-development.

**Root-cause fixes reported from the owner's phone**
- **Unstyled UI in `vite` dev** (`a64ab4b`).
  - Cause: the static CSP meta tag had `style-src 'self'`, which blocked the inline `<style>` elements Vite injects in dev. Production builds load CSS files, which is why CI and e2e never saw it.
  - Fix: the CSP is now injected per command (strict for builds), placed after Vite's inline dev preamble.
  - Verified in Chromium: computed font changed from Times New Roman to the system UI stack.
- **Repeated 502 errors** (`a64ab4b`). The Vite proxy answers 502 with no BIP error envelope when the backend is down; that is now recognised as "backend unreachable".
- **Backend could not start without scikit-learn** (`2c8725c`).
  - numpy and scikit-learn moved to a default-installed `ml` dependency group (same versions, same lock resolution).
  - `bip.ml.backend` loads lazily, so classification without the runtime uses rules only and says so; training returns `503 ml_unavailable`.
  - Verified with a venv built `--no-group ml --no-dev`: API and fixture backend start, ingest, classify (rules) and run research.
  - **Not yet verified on the ARM64 proot device itself.**

**Milestones B–D** (Tasks 1–11 of the plan; commits `d3cd627`..`99c01ae`)
- **Tokens and controls:** design tokens; contrast fixes (`--faint` raised to ≥ 4.5:1; control borders ≥ 3:1); 44 px touch / 32 px desktop controls; sentence-case headings; compact mobile header.
- **Navigation and search:** grouped navigation, skip link, page titles, focus on navigation, a single backend-offline banner; global Ethereum/Solana search.
- **Shared components:** Address, DataTable (cards below 600 px), Grade.
- **Backend:**
  - `GET /wallets/{c}/{a}/status`;
  - `404 route_not_found` (ADR-031);
  - report provenance fields, report v1.1.0;
  - migration v6 `investigation_research_runs` with link endpoints (ADR-030);
  - `trainable_wallets` in label counts.
- **Workspace and research pages:** a tabbed wallet workspace; a persisted research history and deep-linkable run page with a provenance panel, rerun and add-to-case.
- **Curation, cases and providers:** label curation; case research links; attaching findings from the Evidence tab; provider detail; watchlist filter and batch analysis.

**Defects found by verification against real processes, not unit tests**
- A text-node overflow (unbroken address) at desktop width once data existed. The layout check now seeds data.
- 16 px address links in table cells.
- Workspace tabs stacked vertically because of a global `nav` rule.
- The fake watchlist-analysis shape (`results`) did not match the real contract (`items`/`analysed`/`skipped`).
- Selects nested in labels had accessible names that included the selected option.

**Rulings** (decisions taken without a separate owner answer, with their cost if wrong)
- Setup: Ruling: work directly on claude/modest-fermi-0yklvj (no worktree) — owner mandates this branch — cost if wrong: none, branch is already isolated from main
- Task 2: Ruling: short menu labels with NAV.heading for page h1 — spec §2.1 shortens labels but keeps headings — cost if wrong: label rename
- Task 3: Ruling: search sits at the top of the main column on all widths, not in the mobile top bar — keeps the sticky header under 96px on the S24 FE — cost if wrong: move one element
- Task 4: Ruling: DataTable is applied to wallet tables in Task 8 (ActivityTab), not here — those tables move into the workspace — cost if wrong: none
- Task 5: Ruling: updated tests/api/test_api.py unknown-route assertion to route_not_found — that behaviour change is ADR-031's purpose — cost if wrong: a client relying on not_found for bad URLs
- Task 5: Ruling: walletStatus client test written with the method (not watched failing) — thin typed wrapper; endpoint proven RED→GREEN — cost if wrong: low
- Task 6: Ruling: separate REPORT_VERSION=1.1.0 instead of bumping ENGINE_VERSION — the planner/engine did not change, only the report shape — cost if wrong: one constant
- Task 7: Ruling: tests/stage4 v3-upgrade fixture also drops V6 tables first — FK to research_runs requires it — cost if wrong: none
- Task 8: Ruling: no redirect from /flows?..., /graph?..., /research?... — standalone pages keep working with their params; preserves existing behaviour/tests — cost if wrong: two entry points to the same view
- Task 8: Ruling: updated 4 existing tests for moved UI (copy 'No data stored'→'Nothing has been retrieved'; transactions under Activity tab; intel under Evidence tab) — features moved, none removed — cost if wrong: none
- Task 9: Ruling: history shown under the /research wallet picker instead of a new default tab — keeps existing 'Choose a wallet' behaviour — cost if wrong: one more click to reach history on desktop
- Task 10: Ruling: added counts.trainable_wallets + min_labelled_wallets to GET /ml/labels (backend) — the gate cannot be computed reliably client-side from a paged list — cost if wrong: one additive field
- Task 10: Ruling: provider detail reuses ProfileDetail (already shows reliability and consistency) plus capability history — avoids duplicating Stage 4 UI — cost if wrong: none
- Task 11: Ruling: fake watchlist-analyze shape corrected to the real contract (items/analysed/skipped) after a real-backend check exposed the mismatch — cost if wrong: none
- Task 12: Ruling: label/select a11y fix (explicit htmlFor) made in Task 12 — found only by real-browser accessible-name computation — cost if wrong: none

**Checks** (2026-11-07, Python 3.13 venv, Node 22)
```
$ uv run ruff check .            -> All checks passed!
$ uv run ruff format --check .   -> 164 files already formatted
$ uv run pyright                 -> 0 errors, 0 warnings, 0 informations
$ uv run pytest -q               -> 510 passed
$ npx vitest run                 -> Tests 92 passed (92)
$ npx eslint . ; npx tsc -b      -> clean
$ npm run build                  -> built
$ npm run e2e                    -> 51/51 checks passed (real backend + offline fixture backend + Chromium,
                                    incl. backend restart persistence and layout at 412x915 touch / 1360x900)
$ gitleaks dir . ; gitleaks git 61a3e49..HEAD -> no leaks found
```

**Not done:**
- No live RPC or LLM verification (network policy).
- Not run on the owner's phone.
- Graph list view and sticky table headers deferred.
- Independent code review: see the next entry.

## 2026-11-06 — UI integration, Milestone A: architecture and design specification

**Starting point:** `8e7fc29`, clean tree, same commit on the remote. Owner decisions: dark theme only; grouped navigation approved; Milestones A–E approved, with only A to be carried out now.

**Done (documentation only; no application code changed)**
- **`docs/FRONTEND_ARCHITECTURE.md`:**
  - scope and constraints;
  - grouped navigation and the route plan (old URLs redirect);
  - global wallet search (Ethereum and Solana by format, transaction hashes recognised and declined, never triggers ingestion);
  - wallet workspace with path-addressed tabs;
  - research history, run page and the report provenance panel;
  - dark design system with measured contrast and the evidence-grade component;
  - responsive and touch rules; states and copy; accessibility checklist; milestone verification plan.
- **`docs/FRONTEND_API_MAP.md`:** every page mapped to its endpoints. 79 backend route+method pairs; 58 client methods, of which 53 are called (unused: `provider`, `network`, `job`, `researchRuns`, `attachFinding`). Gaps and planned endpoints are listed.
- **ADR-030 and ADR-031 (Proposed):** workspace, research ↔ case link in an additive migration v6, and report traceability fields; wallet status endpoint and `route_not_found`.
- Roadmap progress row, `CURRENT_STATE.md` and the README document index were updated.

**404 investigation (reproduced in a browser against the offline fixture backend)**
- The browser sweep logged every response with status ≥ 400. The only one was `GET /api/v1/wallets/ethereum/0x…aa` (404) on the first visit to a wallet with nothing stored.
- The backend raises `RecordNotFoundError`, which becomes `not_found`. `WalletProfilePage` (`frontend/src/pages/Wallets.tsx:215`) shows "No data stored for this address yet". Verdict: expected, and handled.
- Latent defect: `GET /api/v1/nonexistent` also returns `404 not_found`, and the UI checks only the status. A missing endpoint would therefore read as "nothing stored". The fix is planned for Milestone C (ADR-031).

**Other measured findings**
- **Contrast:** `--faint` #5a6b71 is 3.0–3.5:1 (fails AA for text). The control border `--line-2` is 1.5:1 (fails the 3:1 non-text minimum). All other tokens pass (5.4–14.7:1).
- **Address validation:** BIP has no EIP-55 checksum validation (no Keccak). The spec states this instead of promising checksum checks.
- **Persistence:** research runs, reports and claims are already persisted (migration v5). The gap is UI only. Reports lack feature digest, taxonomy version, as-of and model dataset version; adding them is planned.

**Checks (docs-only change; full suites run to confirm no regression)**
```
$ uv run ruff check .            -> All checks passed!
$ uv run ruff format --check .   -> 157 files already formatted
$ uv run pyright                 -> 0 errors, 0 warnings, 0 informations
$ uv run pytest -q               -> 498 passed in 53.95s
$ npx vitest run                 -> Tests 49 passed (49)
$ npx eslint .                   -> no output (clean)
$ npx tsc -b --noEmit            -> exit 0
$ gitleaks dir . / git --staged  -> no leaks found
```
- Browser audit: the existing built frontend (`vite preview`) and `tests.stage2.serve_offline` (offline fixtures), 12 routes at 1360 px and 390 px. Scripts and screenshots are kept in the session scratchpad, not in the repository.

**Not done (by design)**
- No frontend or backend code changes; Milestone B waits for owner review.

## 2026-11-04 — Stage 5 (Build Prompt 06): ML wallet classification and AI research

**Starting point:** `4894453`, clean tree, same commit on the remote.

**Environment:** this development sandbox has 4 CPUs, 15 GB RAM and 29 GB free disk. The owner's VPS was not touched.

**Done**
- **Dependencies.** Added `scikit-learn==1.9.1` and `numpy==2.5.4` (uv lock; CPU only; ADR-027). No LightGBM, XGBoost, CatBoost or deep learning. No model downloads.
- **`bip/ml/`:**
  - taxonomy: 10 account types, 25 roles, sensitive-label caveats;
  - features: 27 features, version 1.0.0, availability per feature, as-of leakage control, coverage;
  - `bip-rules` baseline: account type and 4 roles with evidence;
  - label store: statuses and provenance, append-only reviews, no self-training;
  - training:
    - gate on distinct wallets;
    - explicit examples only; conflicting labels dropped;
    - wallet-grouped out-of-time split;
    - Random Forest per label with balanced weights;
    - sigmoid calibration only with ≥ 10 positives and ≥ 10 negatives;
    - thresholds validated on measured precision, never below 0.5;
    - Isolation Forest and an HDBSCAN summary;
    - dataset hash; sha256-verified artifacts kept in the data directory;
  - classification service: strict output contract, abstention, contradictions, append-only history.
- **`bip/research/`:**
  - LLM providers: none by default; loopback-only Ollama that never pulls models; optional https OpenAI-compatible provider with a connect-time peer check;
  - 10 typed, read-only tools with per-specialist permissions;
  - engine: deterministic interpreter and planner, 8 specialists, claims with statuses, LLM synthesis validated against existing claims and screened, budgets, cancellation, restart recovery, reruns, 30 KB reports.
- **Migration v5.** Additive: 13 tables.
- **API.** 11 `/api/v1/ml/*` routes and 8 `/api/v1/research/*` routes.
- **Frontend.** AI Research page:
  - classification with status pills, "model score … (not certainty)" and synthetic warnings;
  - research plan preview, progress, cancel, and a report grouped by claim kind;
  - model registry and evaluation;
  - AI provider status.
- **Docs.** `docs/ML_WALLET_CLASSIFICATION.md`, `docs/AI_RESEARCH_METHODOLOGY.md`, ADR-027 to 029; README, CURRENT_STATE, DEVELOPMENT, roadmap, SECURITY, RISK (R26–R28) and PROVIDER_REGISTRY updated.

**Honest results**
- **Real labelled wallets: 0.** Training on stored labels returns `insufficient_data`. **No real model was trained.**
- **Synthetic demo** (240 generated wallets, 6 roles, seed 7):
  - test-split F1 is about 0.78–1.0 per label;
  - these numbers measure recovery of the generator's own rules, not real-world performance;
  - the model is registered as `experimental_synthetic`, and its predictions are capped at `low_confidence`.
- **LLM:** none configured. LLM synthesis is tested only with a deterministic fake provider, including prompt-injection attempts.

**Independent review (code-review agent): 6 defects plus 3 lower-severity items, each fixed with a regression test**
1. Duplicate AI statements failed the run. Claims are now deduplicated after synthesis.
2. Failed or truncated tools vanished silently. Each now adds a limitation and a `missing_information` claim.
3. The 30 KB report cap was not guaranteed. A staged trim now re-measures after every step and falls back to a summary-only report.
4. A failed start leaked a concurrency slot. The slot is released on any error.
5. The training gate counted snapshots, and a wallet could appear in two splits. The gate now counts distinct wallets, the split is grouped by wallet, and conflicting labels are dropped.
6. The OpenAI-compatible host was not resolution-checked. It is now resolved at configuration time, and the peer address is re-checked after connecting.

Lower-severity items fixed:
- feature importances were missing for uncalibrated models;
- synthetic models could report high confidence; they are now capped at low;
- LLM reads had no total deadline.

Also fixed:
- research state transitions can no longer leave a terminal state;
- a time-dependent pipeline test was made deterministic.

**Verification (actual output)**
```
uv run ruff check . / format --check   -> All checks passed / 155 files already formatted
uv run pyright (strict; bip/ml/backend.py basic) -> 0 errors, 0 warnings, 0 informations
uv run pytest -q                       -> 498 passed (Python 3.13)
venv312 python -m pytest -q            -> 498 passed (Python 3.12)
frontend: eslint, tsc                  -> clean
npx vitest run                         -> 49 passed
make e2e                               -> 45/45 (7 new AI Research checks on the offline fixture backend)
gitleaks dir .                         -> no leaks found
```

## 2026-11-04 — Stage 4 (Build Prompt 05): budgeted provider capability prober and reliability intelligence

**Network check first (no provider contacted).**
- The agent-proxy status on 2026-11-04 showed the same egress policy that refused CONNECT to the RPC hosts on 2026-11-03.
- As instructed, no live request was repeated just to reproduce that failure.
- **No live probe was run, and no real provider capability has been observed.**

**Done**
- **Read-only allow-list.**
  - Added `eth_getCode`, Solana `getVersion`, `getBlockHeight` and `getLatestBlockhash`.
  - `is_write_capable` pattern guard, checked at import and when a probe is sent.
  - Registry entries gained `documentation_basis` / `documentation_checked`.
- **`ProviderRouter.probe`.**
  - One request: pinned provider, no retries or failover, no identity side-call.
  - Shared throttle, with spacing ≥ `min_interval_s`.
  - Honours Retry-After cooldowns.
  - Rate limits are not recorded as lifecycle failures.
- **`bip/providers/`.**
  - `capabilities.py`: catalogue, Ethereum 12 and Solana 14 capabilities, including derived ones.
  - `prober.py`:
    - planning and validation;
    - budgets (ceilings 8/48/90 s/2/5 s/0/1 MB/≥ 1 s);
    - concurrency with a thread pool of at most 2;
    - classification: capability status separate from connectivity, environment restrictions explicit;
    - response validation;
    - stop rules;
    - live-derived samples only.
  - `intelligence.py`: run persistence, reliability (`reliability@1.0.0`), consistency checks, historical coverage labels.
- **Migration v4.** Additive: four tables.
- **API.** 9 routes under `/api/v1/providers/...` (profiles, profile, history, reliability, consistency, preview, start run, list runs, get run). They are registered before `/providers/{provider_id}`.
- **Dashboard.** A provider intelligence console on the Providers page: capability profiles, a per-provider capability matrix, reliability and historical coverage, a budget preview required before "Run bounded provider check", results, history, environment-restriction notes and the fixture banner.
- **CLI.** `make probe` (opt-in `BIP_LIVE_RPC=1`).
- **Fakes.** Extended to answer the new methods: a head-block transaction and log, synthesized history, `eth_getCode`, Solana version, block height and blockhash.
- **Docs.** `docs/PROVIDER_CAPABILITY_METHODOLOGY.md`, ADR-025/026; DEVELOPMENT, README, roadmap, PROVIDER_REGISTRY, SECURITY_ARCHITECTURE, RISK_REGISTER (R24–R25) and CURRENT_STATE updated.

**Verification (actual output)**
```
uv run ruff check . / format --check   -> All checks passed / all files formatted
uv run pyright (strict)                -> 0 errors, 0 warnings, 0 informations
uv run pytest -q                       -> 454 passed (Python 3.13)
venv312 python -m pytest -q            -> 454 passed (Python 3.12)
frontend: eslint, tsc                  -> clean
npx vitest run                         -> 44 passed
make e2e                               -> 38/38 (8 new provider-console checks on the offline fixture backend)
gitleaks dir .                         -> no leaks found
```
- Checked by screenshot (fixture backend):
  - the capability matrix opens under its row;
  - the never-probed providers show "No observations" rather than a reliability state.

**Independent review (code-review agent): 6 defects, each fixed with a regression test (follow-up commit)**
1. A capability filter could skip the identity check, so data from another network could be recorded as "supported". Identity is now always tested first.
2. Later runs' `not_tested` rows hid earlier evidence in the profile. The profile now uses the latest *tested* observation per capability.
3. The environment-restriction banner stayed after connectivity recovered. It is now shown only while no later observation reached a provider.
4. A finalized block that failed validation still fed consistency checks. Context values are now written only after validation.
5. Malformed preview queries returned 500. They now return 422.
6. `historical_access_failed` was applied to rejected parameters and oversized responses. It now applies only to null or not-available answers (skipped slots excluded).

After the fixes: pytest 460 passed (3.13 and 3.12), Vitest 44, e2e 38/38, ruff and pyright clean, gitleaks clean.

**Live validation:** none. Providers reached live: 0. The cause is the environment's egress restriction, not any observed provider failure.

## 2026-11-03 — Stage 3 (Build Prompt 04): wallet intelligence, cross-provider verification, relationship graphs

**Live connectivity retry (one bounded attempt, as instructed).** `make live-smoke` at 2026-11-03T17:25:24Z reached 0 of 2 keyless providers. Direct mode fails DNS; proxy mode gets 403 on CONNECT. It was not retried again, no proxy bypass was attempted, and no alternative endpoint was used. **No live observation was retrieved in this stage, and no evidence was corroborated live.**

**Done**
- **Verification model.**
  - `VerificationStatus.INVALIDATED` added; display labels are corroborated / conflicted / invalidated / unverified.
  - Every RPC provider declares an `infrastructure` group; `registry.independent()` (ADR-021).
  - `bip/intel/verify.py` fetches the same transaction from up to two independent providers, compares normalized fields, and records `provider_observations` and `evidence_verifications`. Source records are never modified.
- **Migration v3.** Additive and idempotent: five tables plus `transactions.created_contract`. Ingestion now stores `receipt.contractAddress`.
- **`bip/intel/`:**
  - dataset loader (scoped, hashable);
  - profile (scope-labelled metrics);
  - 9 descriptive behaviour rules;
  - 5 anomaly detectors with published specs;
  - capital flows (canonical source: `transfers`; no USD);
  - bounded evidence-backed graph;
  - `rising_participation` research leads;
  - analysis runner with input-hash reuse, per-detector outcomes and deterministic findings.
- **API.** `/api/v1/intelligence/*` (16 routes). Every response carries `data_origin`, scope and verification; inputs are validated and bounded.
- **Dashboard:**
  - wallet intelligence sections: analysis, verification with per-transaction verify, metrics, behaviour, detectors, findings, counterparties, interactions, limitations;
  - Capital Flows replaces the placeholder: per asset, chart, counterparties, largest transfers, repeated paths;
  - new Relationship Graph page (SVG: zoom, pan, node/edge evidence details);
  - Opportunities replaces the placeholder;
  - Overview shows verification and intelligence metrics;
  - a "Demo data: offline fixtures" banner appears whenever `data_origin = offline_fixture` (set by `tests/stage2/serve_offline.py`).
- **Docs.** New `docs/INTELLIGENCE_METHODOLOGY.md`; ADR-021 to ADR-024 (Proposed); README, CURRENT_STATE, DEVELOPMENT, roadmap and RISK_REGISTER (R21–R23) updated.

**Independent review (code-review agent) and fixes.** Each fix has a regression test:
1. The graph double-counted rows re-read at depth ≥ 2. Rows are now deduplicated by row id.
2. Silent truncation when the per-level row limit was hit. Now reported as `row_limit_reached`.
3. "Not found" by two providers was marked `invalidated`. History depth is unknown, so it is now `unverified`.
4. An unparseable provider answer was shown as `conflict`. It is now a failed lookup (`could_not_run` when nothing usable was returned).
5. Recomputed findings kept stale status after corroboration. They are refreshed in place, keeping the id and investigation link.
6. Wallet findings were filtered in Python over the latest 2,000 rows. They are now filtered in SQL with `json_extract`.
7. Verification was unbounded under concurrency. It is now serialized (409 `verification_busy`), capped at 20 transactions / 60 s per wallet, and stops early when no independent provider exists.
8. Provider error text was stored and returned. Now only exception class and BIP error class are kept. A test confirms endpoint keys never appear in responses.

Also fixed:
- flows report untimed transfers excluded by a window;
- lead event counts count one event per transfer, not per participant;
- the new-contract detector uses an exact, case-sensitive match.

Not changed:
- The `data_origin` marker is per database, not per record. It persists with the database, so a fixture database stays labelled.

**Verification (actual output)**
```
uv run ruff check .              -> All checks passed!
uv run ruff format --check .     -> 125 files already formatted
uv run pyright (strict)          -> 0 errors, 0 warnings, 0 informations
uv run pytest -q                 -> 404 passed   (Python 3.13)
venv312 python -m pytest -q      -> 404 passed   (Python 3.12)
npm run lint / typecheck         -> clean
npx vitest run                   -> 38 passed
make e2e                         -> 30/30 checks passed (Phase A real backend; Phase B "[offline fixture]")
gitleaks dir .                   -> no leaks found
```
- The e2e Phase B wallet corroboration check reports "Checked 1 of 2: 1 could not run", which is correct: the fixture backend has a single provider per chain.
- Palette for graph node kinds and flow in/out validated with the dataviz validator (dark surface `#11171b`): 3 categorical slots, all checks pass; shape is the secondary encoding.

**Candidate and verification counts in this environment**
- Research leads on real data: **0** (no real data exists). Offline-fixture evaluation: `insufficient_data` (1 ingested wallet per chain; the rule needs 3).
- Live observations: 0. Corroborated: 0. Conflicted: 0. Unverified: every stored record. All stored records are offline fixtures or test data.

**Not done (by design)**
- No accounts, keys, paid services, Parquet, graph database or new dependencies.
- No AI in any path. No transaction signing, no writes to any chain.

## 2026-11-03 — Stage 2 follow-up: independent review fixes and live retry

Each of these review findings was first reproduced by a failing test in `tests/stage2/test_review_regressions.py`, then fixed.

1. **Solana cursor could skip signatures.** This happened when more new signatures than `limit` arrived, or a run was interrupted mid-batch. Now the cursor's `newest` moves only after a whole batch is ingested. A batch that fills the limit resets the range (no false contiguity), and older history advances per transaction.
2. **Ethereum cursor advanced before ingestion.** Windows were marked scanned even when transactions were truncated or failed. Now the cursor is computed after ingestion, in a `finally`, and never moves past the newest unfinished transaction's block.
3. **Failed transactions produced transfers.** Failed Solana transactions no longer decode transfer instructions (counted and flagged instead). Failed EVM transactions yield no native-value transfer.
4. **Out-of-range RPC values crashed instead of being rejected.** Timestamps are bounded (≤ 9999-12-31), positions ≤ 2^63−1 (SQLite INTEGER), and token amounts must be ASCII digits. `run()` now always finishes a run, recording `internal_error`. The API maps malformed provider data to 502 `upstream_invalid`.
5. **Migration v2 was not crash-safe.** pysqlite auto-commits DDL. Each step is now idempotent (`PRAGMA table_info` check, `checkfirst`, `IF NOT EXISTS`), and is tested by migrating a database with a half-applied v2.
- Hardening: stored ingestion error text is redacted, and the transport has a total read deadline (3× the timeout).

**Verification (actual output):**
- ruff clean; pyright strict 0 errors; pytest **357 passed**.
- Vitest 30 passed; build OK; e2e 21/21.
- **Live retry 2026-11-03T16:59Z: 0/2 providers reachable.** Direct mode fails DNS; proxy mode is refused with 403. This is unchanged: the sandbox network policy still denies RPC hosts.

## 2026-11-03 — Stage 2: Provider registry and read-only data engine

**Scope.**
- Provider registry and lifecycle, router, hardened transport, and Ethereum and Solana adapters.
- Bounded wallet ingestion, migration v2, API and dashboard integration.
- An opt-in live smoke tool.
- Built on the existing `bip/` package, API, SQLite and UI. Nothing was rewritten.

**Plugins and tools.**
- Context7 and the GitHub plugin failed to connect (proxy refusal), so no library docs came from Context7.
- Web search was used for provider endpoints, auth and limits.
- Pyright ran via the CLI.
- An independent code-review agent (`feature-dev:code-reviewer`) reviewed the stage.
- Superpowers skills were available, but no skill-driven workflow was invoked.

**Live network results (real, 2026-11-03).**
- `make live-smoke` reached **0/2** keyless providers:
  - direct mode: DNS resolution failed;
  - proxy mode: the proxy refused CONNECT (403).
- The sandbox network policy blocks RPC hosts.
- The proxy transport itself was verified against an allowed host (pypi.org): the TLS tunnel worked, a 301 was refused and the size cap triggered.
- **No real provider has answered a BIP request yet.**

**Defects found and fixed during the stage.**
- Registry validator raised `UnsafeURLError` instead of `ValueError` (bad URLs escaped as raw exceptions). Fixed.
- A 429 with a single provider could retry during its cooldown. It now waits inline at most 5 s, otherwise stops.
- DNS failure was mislabelled `unsafe_destination`, and proxy refusal was labelled generic `connection`. These are now `connection` ("DNS resolution failed") and `proxy`.
- A reorg detected through a receipt block-hash mismatch was labelled `parse_error`. It is now `reorg_detected`.
- Signature-only Solana rows were not linked to the wallet. Added the `wallet_activity` table.
- SPL transfers between token accounts owned by the wallet were not shown for it. These are now included through owner metadata.
- e2e: stale `vite preview` children survived stop (now killed by process group); an Overview/Chains selector race was fixed.

**Verification (actual output).**
```
uv run ruff check . / ruff format --check .  -> clean
uv run pyright (strict)                        -> 0 errors (Python 3.12 and 3.13)
uv run pytest -q                               -> 345 passed (offline; RPC via fake nodes)
npm run lint / npm run typecheck               -> clean
npx vitest run                                 -> 30 passed
npm run build                                  -> built
node e2e/smoke.mjs                             -> 21/21 (Phase A real backend; real-network health
                                                  check observed "Check failed"; Phase B offline fixture)
gitleaks (staged)                              -> no leaks found
make live-smoke                                -> 0/2 providers reachable (network policy)
```

## 2026-11-03 — Stage 1b: BIP 0.1 integrated application foundation

**Scope.** A locally runnable prototype: FastAPI backend, SQLite persistence, REST API and a React/TypeScript dashboard connected to it. No blockchain, provider or AI integration. No authentication. No VPS changes.

**Reused from Stage 1 (not rebuilt)**
- `Settings`, extended with `api` and `database` groups.
- `StorageRoot` and `Layer`, extended with `OPERATIONAL`: they locate the SQLite file.
- `DiskGuard` gates every API write.
- `AddressRef` / chain registry validate every chain and address.
- The `Finding` contract is the findings request body, and its evidence rules are enforced.
- `Provenance` is stored as JSON on chain rows.
- The redacting logger is used for all API errors.

**Added**
- `bip/db/`: SQLAlchemy Core schema v1 (12 tables, natural-key unique constraints, foreign keys with cascades, indexes); versioned migrator that refuses newer schemas; UTC datetime column type; repository with typed errors (404/409/422/503 mapping).
- Chain-data writers `store_transaction` / `store_transfer`. These are idempotent and update wallet first/last observation. They are not exposed through the API.
- `bip/providers/catalog.py`: 14 providers from `PROVIDER_REGISTRY.md`. Configuration is detected by variable presence only. Every provider is `unavailable`.
- `bip/api/`: app factory and lifespan; 19 route handlers under `/api/v1`. It also adds an error envelope, a body-size limit (Content-Length and streamed), security headers, a CORS exact-origin allow-list, and `python -m bip.api`.
- `frontend/`: Vite + React 19 + TypeScript (strict, `exactOptionalPropertyTypes`).
  - Nine navigation sections and a typed API client, with loading, empty, error and retry states.
  - Address validation that mirrors the backend.
  - A restrictive CSP.
  - Capital Flows and Opportunities are explicit "Not built yet" placeholders.
- Tooling and docs: Makefile targets (`api`, `web`, `web-*`, `e2e`, `check-all`), CI frontend job, `.env.example` additions, ADR-015 (SQLite), ADR-016 (stack moved forward), roadmap Stage 1b, `docs/DEVELOPMENT.md` (database, endpoints, error codes, security).

**Dependencies added**
- Runtime:
  - `fastapi` 0.162 (brings `starlette` 1.10): the API framework requested.
  - `uvicorn` 0.54: ASGI server.
  - `sqlalchemy` 2.1: parameterized, dialect-portable SQL (ADR-015).
- Dev: `httpx2` 2.16, which Starlette 1.10's TestClient requires (plain `httpx` emits a deprecation warning).
- Frontend runtime: `react`, `react-dom`, `react-router-dom`.
- Frontend dev: `vite`, `@vitejs/plugin-react`, `typescript`, `vitest`, `jsdom`, `@testing-library/*`, `eslint` with `typescript-eslint` and `eslint-plugin-react-hooks`.
- No Postgres, Redis, Docker or chain SDKs.

**Defects found and fixed during this stage**
- Pyright strict flagged protected `Row._asdict()` use, untyped SQLite hooks and `Iterator`-typed context managers. Switched to `.mappings()`, typed the hooks and used `Generator`.
- The React Hooks v7 linter flagged a ref write during render and a synchronous `setState` in an effect in `useApi`. The hook was rewritten so state changes only in async callbacks, and loading is derived from a request key.
- The end-to-end run found that `frame-ancestors` is ignored in a meta CSP. It was removed rather than leaving a no-op.
- **Independent review, both confirmed by failing tests first:**
  - A chunked oversize body returned 400, not 413, because FastAPI rewraps body-parser exceptions. The limit exception is now an `HTTPException(413)`.
  - The findings API accepted self-asserted `cross_verified` evidence for transactions never stored, which the UI would show as verified. Verified evidence must now be a stored chain record, otherwise 422.
- Also from the review: the SQLite URL is now built with `URL.create`, so special characters in the path cannot corrupt it.

**Verification (actual commands and output)**
```
$ uv run ruff check .                 -> All checks passed!
$ uv run ruff format --check .        -> all files formatted
$ uv run pyright                      -> 0 errors, 0 warnings, 0 informations
$ uv run pytest -q                    -> 272 passed (213 Stage 1 + 59 new: 49 API/DB, 10 settings)
$ npm run lint / npm run typecheck    -> clean
$ npx vitest run                      -> 26 passed (3 files)
$ npm run build                       -> built (JS 296 kB, 91 kB gzip)
$ node e2e/smoke.mjs                  -> 15/15 checks passed
```
The end-to-end run used the real backend on a temporary data root, the built UI through `vite preview` and headless Chromium. It covered:
- health through the proxy, and zero counts on an empty database;
- chains shown as not connected;
- watchlist create and add, with address normalization;
- duplicate rejection, and investigation create plus status change;
- the SQLite file under the data root, and persistence across a **backend restart**;
- dashboard counts;
- 422 and 404 wallet responses, no paths in settings, and CORS refusing a foreign origin;
- no horizontal overflow at 390 px, and no unexpected console errors.

**Not done (by design or blocked)**
- Authentication and authorization (documented as future work, API bound to loopback).
- Chain adapters and ingestion, so the database holds no chain data, and the UI and API say so.
- The end-to-end test is not in CI (it needs Chromium).

**CI on GitHub (`09bb27c`):** `checks`, `frontend` and `secret-scan` all succeeded (push and pull_request runs).

## 2026-11-03 — Stage 1: Repository foundation & core engineering contracts

**Scope.** Offline foundation only. No RPC, provider, database, LLM or frontend code. No network access at runtime. No VPS changes.

**Done**
- Project: `pyproject.toml` (hatchling build, `requires-python >=3.12`), `uv.lock`, `Makefile`, `.env.example`, `.gitignore` (ignores `.env`, `var/`, `data/`, `*.parquet`, `*.car`), `.pre-commit-config.yaml`, `.github/workflows/ci.yml`.
- `bip/config/settings.py`: `Settings` / `load_settings()`. `BIP_*` env vars and nested `__`. Environment, log level/format, data root, temp dir, byte-size quotas (`5GiB` syntax), free-space floors, enabled chains (comma list validated against the chain registry), router feature flags, optional `SecretStr` Postgres/Redis URLs. Production rules: absolute data root, JSON logs. Errors list variable names and reasons, never values.
- `bip/security/secrets.py`: `read_secret()` (`$NAME` or `$NAME_FILE`; both set is rejected), exact-value registry, pattern redaction (URL userinfo, bearer/basic tokens, sensitive `key=value`/JSON pairs, opaque URL path segments). `0x`-hex identifiers are left readable. Every pattern is bounded, and input over 256 KiB is truncated before redaction.
- `bip/core/logging.py`: JSON/text logging. A mandatory filter redacts message, args, `extra=` fields (values under sensitive key names are masked) and exception text.
- `bip/security/url_validation.py`: `validate_url()`, `check_ip()`, `validate_redirect()`, with limitations documented (DNS rebinding, redirects, pinning required in Stage 3).
- `bip/storage/paths.py`: `StorageRoot` (traversal, absolute, NUL and symlink-escape rejection; `create_exclusive()` uses `O_EXCL` and never overwrites). `bip/storage/disk_guard.py`: `DiskGuard` (BIP quota, free bytes, free ratio; fails safe when usage cannot be measured; never deletes).
- `bip/contracts/`: chain registry (`ethereum`, `solana`, `base`, `arbitrum`, `bnb`), `BlockPosition` vs `SlotPosition`, EVM hex and Solana base58 identifiers (decoded to exact byte length), `AddressRef` (no identity field), `Provenance`, `EvmBlock`/`SolanaBlock`, `EvmTransaction`/`SolanaTransaction`, `EvmLog`/`SolanaInstruction`, `TokenTransfer` (raw amounts, unknown decimals stay `None`, `unclassified` by default), `ContractDeployment`, and `Finding`/`Claim`/`EvidenceRef`/`Attribution` with evidence rules.
- Docs: `docs/DEVELOPMENT.md` (new), README setup, CURRENT_STATE, roadmap progress, ADR-014.

**Dependencies added**
- Runtime: `pydantic` (2.14.2), `pydantic-settings` (2.16.2). These were already chosen in ADR-004. `pydantic-settings` provides typed env loading and `NoDecode` for comma lists.
- Dev: `pytest` (9.2.0), `ruff` (0.17.3), `pyright` (1.1.414).
- The lockfile resolves 17 packages in total. No HTTP client, web3 or Solana SDK has been added yet.

**Defects found and fixed during this stage**
- Logging leaked values passed as `extra={"api_key": ...}`. Fixed by masking by key name.
- JSON-style `"password": "..."` was not redacted. Fixed in the key-value pattern.
- A substring match on `token` would have masked `token_address`. Changed to suffix matching.
- Two redaction regexes were super-linear on hostile input (2–45 s on 65–80 KB strings). The URL-userinfo scheme is now bounded, the URL-path rule is a linear parser, the key-prefix is bounded, and there is a 256 KiB input cap. Regression tests cover these cases.

**Independent review (code-review agent) — findings confirmed by running them, then fixed**
- Passwords containing `@` or `/` partly survived URL-userinfo redaction (`postgresql://user:p@ss@host` left `ss`). The pattern now ends at the last `@`. It may over-redact, which is the safe direction.
- Non-string `extra=` values (e.g. exceptions) reached JSON logs unredacted through `default=str`. `redact_value` now renders and redacts every non-scalar object.
- `https://[fec0::1]/` (deprecated IPv6 site-local) was accepted. `fec0::/10` is now blocked.
- Orphaned (reorged-out) evidence counted as verified. `EvidenceRef.is_verified` now excludes `finality=orphaned`.
- A parent path that is a file raised a raw `FileExistsError`. It now raises `StoragePathError`.
- Not changed (by design, documented): keys whose sensitive word is not at the end (`secret_key_base`, `password1`) are not pattern-masked. Unresolved hostnames such as `127.0.0.1.nip.io` pass when `resolve=False`, and connection-time pinning (Stage 3) must catch them.

**CI on GitHub (commit 663e949)**
- `checks` (ruff, pyright, pytest on Python 3.12) passed.
- `secret-scan` failed on a false positive: the synthetic `X-API-KEY` fixture in `tests/unit/test_secrets.py`. Reproduced locally with gitleaks 8.24.3. Fixed with a `.gitleaksignore` fingerprint for that historical commit, and the fixture is now assembled at runtime. The local scans `gitleaks git` (PR range), `gitleaks dir .` and `gitleaks git --staged` each report "no leaks found".
- CI re-run on `3342f86`: `checks` and `secret-scan` both succeeded (push and pull_request runs).

**Verification (actual commands and output)**
```
$ uv run ruff check .            -> All checks passed!
$ uv run ruff format --check .   -> 46 files already formatted
$ uv run pyright                 -> 0 errors, 0 warnings, 0 informations
$ uv run pytest -q               -> 213 passed
$ (Python 3.12.3 venv) pytest    -> 213 passed; pyright -> 0 errors
$ gitleaks dir . / git --staged  -> no leaks found
```
- `.env.example` was loaded through `uv run --env-file .env.example` and validates.
- Gitleaks 8.24.3 was run locally from a binary in the session scratchpad; nothing was installed in the project. The full `pre-commit` hook set was not run locally.

**Not done (by design)**
- No provider registry, router, HTTP client, adapters, Parquet writer, database, AI or UI.
- No installs on the VPS, no downloads beyond Python dev dependencies into the project `.venv`.

## 2026-11-03 — Stage 0: Blueprint

**Done**
- Replaced the placeholder README with the BIP overview.
- Created CLAUDE.md, CURRENT_STATE.md, DECISIONS.md (13 Proposed ADRs), this log.
- Created docs: MASTER_SPECIFICATION, DATA_ACQUISITION_STRATEGY, PROVIDER_REGISTRY, DATA_ARCHITECTURE, SECURITY_ARCHITECTURE, IMPLEMENTATION_ROADMAP, RISK_REGISTER.

**Research method**
- Web searches on 2026-11-03 for provider free tiers (Alchemy, Infura, dRPC, Ankr, QuickNode, Chainstack, Helius, PublicNode, Solana public RPC), datasets (BigQuery sandbox, crypto_ethereum, goog_blockchain, crypto_solana, Old Faithful, era1), indexers (Etherscan V2, The Graph, DefiLlama, Dune), GitHub API limits, EIP-4444 status, node disk sizes, object storage free tiers.
- Direct WebFetch of provider sites failed (DNS blocked by session network policy). All figures are graded `V-search` / `Conflict` / `3rd-party` / `Unknown` accordingly.

**Verification**
- Cross-reference check of stage numbers between docs (fixed mismatches between DATA_ARCHITECTURE/README and the roadmap).
- No code, so no tests were run.

**Not done (by design)**
- No installs, downloads, accounts, API calls to providers, or VPS changes.

## Final-review fix wave (providers-expansion)

- `validationcloud-eth` and `validationcloud-sol` set to `documentation_basis = "search_derived"`; test now expects every new entry to be `search_derived`.
- Validation Cloud Solana archive wording in `docs/PROVIDER_REGISTRY.md` changed to unknown.
- Chainbase uses the conservative 2 req/s in the registry and onboarding row; the 3 credits/s figure stays in notes.
- `CURRENT_STATE.md` now gives the `.env`-loading probe command and notes `make probe` does not load `.env`.
- `dwellir-eth` sources now include https://www.dwellir.com/ (the source of its second allow-listed host).
- `ankr-sol` set to `archive = "no"` (facts: ledger keeps about 16 hours of history).

- Checks: ruff "All checks passed!"; pyright "0 errors, 0 warnings, 0 informations"; pytest tests/stage2 "137 passed in 139.20s (0:02:19)".
