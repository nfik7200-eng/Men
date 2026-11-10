# Implementation Log

Entries are newest first. Each entry records what was done, how it was verified, and what was not done.

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

## 2026-11-08 — Bounded Ethereum block collector (discovery without supplied addresses)

**Why.** The goal review said discovery only sees wallets BIP was asked to ingest. This adds the one slice that can be built and proven offline: contract creations and large native transfers read from finalized blocks.

**Built.** `EthereumAdapter.block_full`, `bip/ingest/collector.py` (`ChainCollector`), settings `collector.*` and `workers.collector_enabled`, routes `GET /discovery/collector` and `POST /discovery/collect/ethereum` (admin by the fail-closed table), scheduler job `chain_scan` (off, `contacts_providers`), ADR-038 (Proposed). Finalized blocks only; cursor in `app_metadata`; bounded by blocks, receipts, call budget and deadline; each run writes a coverage manifest; no raw block payloads retained.

**Verification (exact).** `tests/collector/` 9 passed (creations and large transfers stored, small ones counted and dropped, resume and idle runs, block and receipt caps disclosed, missing receipt not turned into a transfer, no finalized tag means nothing read, analyst gets 403, job off by default, `DiscoveryService` raises a signal for a collected contract). Full `pytest -q`: **763 passed**. `ruff check` and `ruff format --check` clean, `pyright` 0 errors, `docs/API_REFERENCE.md` regenerated.

**Not done / still BLOCKED.** Never run against a live provider. No Solana collector, ERC-20 or event-stream ingestion, DEX/pool/liquidity detection, holder concentration or exchange attribution; these need a data source and cost decision from the owner.

## 2026-11-08 — Pool-creation events and the `new_liquidity_pool` detector

**Why.** The goal review still named new pools as unmet. Factory creation events are rare and filterable by topic, so a bounded unfiltered `eth_getLogs` over the planned finalized range is feasible; ERC-20 transfers chain-wide are not (volume).

**Built.** Migration v11 (`pool_events`, additive), `bip/chains/ethereum/pools.py` (v2/v3-style decoders), `EthereumAdapter.pool_logs_adaptive`, pool scan in `ChainCollector` (runs before blocks so a failure leaves the cursor unchanged; checks the log's block hash against the canonical header; stores the creating transaction as evidence), `collector.max_pools_per_run`, detector `new_liquidity_pool` (confidence cap 0.4, flags tokens with no earlier stored pool), pool entities in `/search`.

**Verification (exact).** Event-signature constants checked against a test-only keccak-256. `tests/collector/` 14 passed (decode v2/v3, store with transaction, rerun dedupes, wrong block hash rejected, cap disclosed, detector scoring and search). Full `pytest -q`: **768 passed**. `ruff check`, `ruff format --check` clean; `pyright` 0 errors; `docs/API_REFERENCE.md` regenerated; forward-migration test now ends at v11.

**Not done / still BLOCKED.** Never run live. No Solana collector, chain-wide ERC-20 transfer ingestion, event streams, liquidity/reserve/holder changes, exchange attribution, frontend sign-in, real labels, live LLM.

## 2026-11-09 — ERC-20 coverage, pool reserves, holder concentration (migration v12)

**Why.** The new master goal asks for ERC-20 coverage, liquidity/reserve changes and holder concentration. The earlier "volume exceeds provider limits" claim was unmeasured, so the feed was built to split windows instead.

**Built.** `bip/ingest/ethereum_events.py` (`EventFeeds`: pools, tokens, reserves; collector now orchestrates blocks only), `EthereumAdapter.event_logs_adaptive` (topic and optional address filter, adaptive split), `decode_sync`, migration v12 (`token_stats`, `pool_reserves`), detectors `new_token_activity`, `pool_reserve_change`, `holder_concentration`, `RawCandidate.evidence_finalized`, settings caps (`ingest`/`collector` limited to 150 calls and 90 s by validators).

**Verification (exact).** Full `pytest -q`: **776 passed**. `ruff` and `pyright` clean; `docs/API_REFERENCE.md` regenerated. Synthetic fixtures only; the Sync topic is checked against a test-only keccak-256.

**Not done.** Never run live, so real log volume per block is unmeasured. v3 pool reserves, real balances (`eth_call`), Solana, reviewed labels and exchange attribution are still to do.

## 2026-11-09 — Solana finalized-slot collector (migration v13)

**Why.** The master goal asks for Solana finalized-slot collection with program/mint/pool discovery.

**Built.** `bip/chains/solana/programs.py` (wallet-agnostic decoders; every program id, tag and account position checked against Agave, solana-program/token, anza-xyz/solana-sdk, raydium-amm and orca-so/whirlpools sources on 2026-11-09; docs.raydium.io and dev.orca.so were not reachable from the session), `SolanaAdapter.block_full`, `bip/ingest/solana_collector.py`, migration v13 (`solana_discoveries`), detector `solana_new_entities`, routes `GET /discovery/collector/solana` and `POST /discovery/collect/solana`, job `solana_scan` (off), `solana_collector.*` settings (150 requests, 90 s ceilings), fake node support for full synthetic blocks.

**Verification (exact).** Full `pytest -q`: **783 passed**; `ruff` and `pyright` clean; API reference regenerated. All blocks are synthetic fixtures.

**Not done.** Never run live (block size and latency on a free endpoint unmeasured); Orca `initialize_pool_v2`, Raydium CPMM/CLMM and other pool programs unrecognised; no SPL transfers, holders or reserves for Solana.

## 2026-11-09 — Reviewed entity labels and attribution (migration v14)

**Built.** `bip/entities/labels.py` (`EntityLabelService`), table `entity_labels`, routes `POST/GET /entities/labels`, `POST /entities/labels/{id}/review`, `POST /entities/labels/suggest`, `GET /entities/{chain}/{ref}/labels`, role policy (propose/review = analyst), `attribution` on every signal, ADR-040. Ollama check: provider `none`, port 11434 refused; no model contacted.

**Verification.** `tests/entities/test_labels.py` 6 passed (evidence rules, human-only approval, separate reviewer, attribution only after approval, ungrounded AI suggestions dropped, unconfigured model reported). ruff and pyright clean.

## 2026-11-09 — Deployment kit and pre-flight check

**Built.** `deploy/` (production env template, hardened systemd unit with `ExecStartPre` pre-flight, Caddy and nginx examples, off-disk backup timer, README with install/verify/upgrade/rollback and the procedure for turning collection on), `bip/tools/preflight.py` (PASS/WARN/FAIL; exit 1 on FAIL, 2 on invalid config; prints names and reasons, never values).

**Verification.** `tests/unit/test_preflight.py` 12 passed, including that the shipped template loads through the real settings with no FAIL and with collection off. Nothing was installed or started anywhere; `systemd-analyze` and the proxies were not run.

## 2026-11-09/10 — Live verification of both collectors (free public endpoints, throwaway database)

**What ran.** Each invocation used a temporary data root, the registry's no-cost public providers (publicnode-eth, solana-public), read-only methods, and at most 110 requests (limit 150) and well under 90 s. No live database was touched.

**Ethereum.** One block: 20 requests, 315 transactions, 285 Transfer logs, 69 tokens. Twenty blocks (26376729 to 26376748): 6,201 transactions, 15,088 Transfer logs (about 750 per block), 448 tokens, 8 creations, 25 large native transfers stored, 110 requests. **Finding:** PublicNode answers an address-less `eth_getLogs` over several blocks with "Please specify an address in your request"; it was classified as transient and retried three times, which aborted the feed. Fixed: the message is a too-broad-query error, and the adapter goes straight to single-block windows (no bisecting) and remembers it; the feed call reserve scales with the block count; regression test reproduces the behaviour in the fake node. Pools and reserves: none occurred in the window.

**Solana.** 2 slots (3 requests), 40 slots (41 requests, 10,097 transactions, 2,680 failed), plus two read-only diagnostics (101 and 26 requests). Transaction versions seen in one 25-slot sample: legacy 2,296, v0 2,419, v1 2,012. Instruction decoding worked on every transaction (the 59 with none decoded were zero-instruction version-1 no-ops). No InitializeMint, program deployment or pool initialisation occurred in about 142 slots, so those layouts are not confirmed on a live occurrence.

**Not done.** Scheduled collection stays off. Nothing was run against a paid or keyed endpoint.

## 2026-11-10 — Frontend sign-in (delegated to a subagent owning `frontend/`, verified independently)

**Built.** `frontend/src/auth/` (in-memory session store, actions, provider, sign-in form with `Retry-After` countdown, user menu, change-password dialog, role gating via a TypeScript port of `bip/api/access.py`), API client changes (credentials, `X-CSRF-Token` on writes, 401 returns to sign-in, 403/429 messages), `frontend/e2e/auth.mjs` (real backend in `required` mode on a throwaway data root with four SYNTHETIC users).

**Verification (run by the integrating session, not taken from the report).** `tsc` clean, `eslint` clean, `vitest` 179 passed (was 96), `npm run build` ok, `npm run e2e` 165/165 (was 49): the 116 new checks cover anonymous gate, wrong password, login, cookie flags, reload, CSRF-protected write, missing-token 403, role 403, sign-out, viewer controls disabled, and no overflow with 44 px touch targets at phone, tablet and desktop sizes. No backend change was needed.

**Notes.** `GET /auth/status` returns no CSRF token (the UI uses `/auth/me`). A locked account and a wrong password both return 401 `invalid_credentials`. `/auth/password` accepts a new password of any length from 1 to 256 in the schema while user creation requires 12; the dialog enforces 12 locally. Not exercised: a real deployment behind HTTPS.

## 2026-11-10 — Independent review of the collectors and labels, and the fixes

**Review.** A correctness reviewer and a security reviewer (read-only, a different agent type because the review-council agents refuse a checkout whose CLAUDE.md or CI file differs from `origin/main`; the guard was not bypassed) read the new backend and deploy files. Findings were checked against the code before acting.

**Fixed (each with a failing-then-passing test in `tests/collector/test_review_fixes.py`, `test_solana.py`, `test_labels.py`, `test_preflight.py`).** Ethereum: cursor and feeds after an exception, per-block give-up with a persistent gap list, receipt cap no longer half-stores a block, budget arithmetic from the caps (validated), reverted creations (also fixed at the parser), pool kept when its evidence transaction is missing, atomic token counting, oversize blocks skipped and recorded, trusted-factory ranking and restriction, token row cap, redacted messages. Solana: -32004 not treated as skipped, outage never burns attempts, continuity across runs (slot:hash), one gap per lag jump, discoveries outside the transaction cap, missing meta not success, base58 decode only for known programs and short data. Detectors: reserve truncation by pool name, per-row queries in the pool detector. Labels: requester recorded (migration v15), bundle-only citations, unverified evidence not reported as verified, review race, re-proposal after rejection, name normalisation, one model call at a time. Deploy: no env values on a command line, reviewed-commit checkout, `UMask=0077`, restart limits, no `--delete` off-site copy, `/srv` permissions, preflight `--use-environment`, stricter file mode.

**Verification.** `pytest -q` 828 passed; ruff and pyright clean; live re-check of both collectors on the throwaway database (60 and 21 requests): no continuity mismatches, nothing rejected.

**Not changed.** Serving the API on a Unix socket (loopback callers can still set the client address; documented). A second provider to confirm finalized blocks. Moving gaps to a table (the newest 50 and an evicted counter are kept).

## 2026-11-10 — Dashboards for signals, alerts, coverage, attribution and system health (subagent owning `frontend/`, verified independently)

**Built.** Routes `/signals`, `/signals/:id`, `/alerts`, `/coverage`, `/attribution`, `/system` and nav entries (with an unread-alerts badge that never raises an alert), shared `Modal`, `ExternalLink` (http(s) only, `rel="noopener noreferrer"`), `EvidenceEditor`, discovery components, role gating through the existing `useCan`/`RoleHint`. "Run once" on Coverage never fires on page load or Cancel; Confirm sends exactly one request and shows the returned manifest.

**Verification (re-run by the integrating session).** `tsc` clean, `eslint` clean, `vitest` 238 passed (was 179), `npm run build` ok, `npm run e2e` 217/217 (was 165), including layout (no overflow, tap targets) for the five new routes at three viewport sizes and a viewer seeing both collector buttons disabled with the admin hint.

**Gaps.** No signal-creating API exists and the offline fake nodes' timestamps fall outside the detector lookback windows, so the browser suite shows empty states for signals and alerts; populated paths are unit-tested with SYNTHETIC data. No "Run discovery" control yet. The unit tests, not real collected data, are the only evidence the pages render real signals correctly.

## 2026-11-10 — Populated SYNTHETIC preview and small contract fixes

**Built.** `tests/preview/serve_preview.py` (`make preview`): throwaway database, invented data near "now", real detectors (13 signals from 9 of 10 detectors), two SYNTHETIC labels, three SYNTHETIC users with a one-off password, `offline_fixture` marker so every page shows "Demo data". Checked in a real browser: sign-in, Signals at 390 px wide (13 rows, no overflow, banner and role badge visible). The Ethereum collector's `not_covered` no longer says Solana has no collector, and its `limits` include `run_deadline_s`.

**Not done.** The preview shows invented data, so it proves the pages work, not that real collected data looks like this. No "Run discovery" button in the UI yet.

## 2026-11-10 — Preview walkthrough found pages without the demo banner

**Finding.** A walkthrough of the populated preview (every new page at phone, tablet and desktop sizes) showed that Attribution and System displayed synthetic records with no "Demo data" banner, contradicting the documentation. Cause: `/entities/labels` and `/system/health` did not return `data_origin`.

**Fix (backend test first).** Both endpoints, and `GET /entities/{chain}/{ref}/labels`, now return `data_origin`; both pages render the existing banner; four unit tests and two browser checks pin it.

**Verification.** `pytest` 829 passed; frontend `tsc`, `eslint`, build, `vitest` 242 passed, `npm run e2e` 219/219. `systemd-analyze verify` on the three unit files reports only that `python` and `rsync` are not installed on this machine (no unknown directives).

## 2026-11-10 — "Run discovery now" and readable record lists

**Built.** Signals page: an analyst-gated "Run discovery now" that calls `POST /discovery/run` (reads the database only; reports new, merged and expired counts and alerts; refreshes the list). Signal detail: a list of records (for example the top addresses of a concentration signal) renders as rows with explorer links instead of raw JSON.

**Defect caught by its own test.** The first version of the button used `!can` on the object `useCan` returns, so it was never disabled for a viewer. The viewer test failed; fixed to `can.allowed`, with the reason as the button title.

**Verification.** `tsc` and `eslint` clean, `vitest` 245 passed, build ok, `npm run e2e` 221/221 (adds a check that a pass runs against the offline backend and sends no collector request). CI on the four previous head commits (80a255f, 13ad93c, b39e492, 851f0a9) reported complete with nothing failing.

## 2026-11-10 — Pool and reserve decoders checked on real events; a design flaw found by real data

**Live checks (free endpoint, throwaway database, 6, 71 and 72 requests; all within the 150-request limit).** Querying the documented Uniswap v2 and v3 factory addresses directly: 62 real `PairCreated` and 14 real `PoolCreated` logs over 5,000 blocks, every one decoded; sampled pools have contract code and pair with real WETH; fees 100 and 500 decoded. 20 real `Sync` logs of a new pair decoded to plausible reserves (an initial 4.5e27 / 0.5 ETH, then a small swap). The real collector, pointed at a window containing a real new v2 pair, stored the pool, watched its reserves and stored 7 (then 4, on a second pair) real reserve updates; no rejection and no continuity mismatch.

**Flaw found only by real data.** `token_transfers_stored` hit its cap of 500 because the new pair was TOKEN/WETH, so every WETH transfer counted as a pooled-token transfer and crowded out the new token. Fixed test-first: base assets (WETH9, from Uniswap's Ethereum deployments page) and hubs (tokens in at least `hub_pool_threshold` = 25 stored pools) are excluded from per-event storage. Re-run on a second real pair: 4 transfers stored, not 500.

**Still not seen live.** Solana mint, program-deployment and pool-initialisation events; factories other than Uniswap v2/v3.

## 2026-11-10 — Solana mint layout seen on a real event

**Scans (solana-public, read-only, throwaway database, 141, 81, 34 and 23 requests; one hit the 85 s deadline, as designed).** About 100,000 transactions across about 350 finalized slots. Two real `InitializeMint2` instructions were seen. The first produced no discovery, which I treated as a possible decoder miss until an independent cross-check (System `CreateAccount`, 82 bytes, Token program) and a printout at the moment of detection showed the second one decoded correctly (`mint`, 9 decimals, `InitializeMint2`); the first was a failed transaction, which the decoder skips by design. Raydium AMM v4 and Orca instructions occurred (tens and hundreds) but none was a pool initialisation, and no loader deployment occurred.

**Conclusion.** The SPL mint layout is verified live. Program deployments and pool initialisations are still verified only against official sources and synthetic fixtures.

## 2026-11-10 — Unix-socket listener (closes the proxy-header limit from the security review)

**Why.** A TCP listener on loopback lets any local process on a shared host connect and spoof the client address used for throttling and audit (the uvicorn proxy-header rewrite plus BIP's own trust flag).

**Built.** `BIP_API__UNIX_SOCKET` (absolute path); `python -m bip.api` serves on it with `proxy_headers=False`; `bind_unix_socket` binds under umask 0117 and clears only a stale *socket* (a regular file is never removed). Found by a test, not assumed: uvicorn `chmod`s its own Unix sockets to 0666, so BIP binds the socket itself and passes it in. The exposure guard accepts a socket listener, `preflight` treats it as a pass, the production env template, systemd unit (`RuntimeDirectory=bip`, mode 0750), Caddy and nginx examples and the README use it.

**Verification.** `tests/unit/test_unix_socket.py` 5 passed (config shape; a real server answering over a real socket with mode exactly 0660; stale-socket handling; a regular file refused; guard and preflight). `pytest` 836 passed; ruff and pyright clean; `systemd-analyze verify` on the edited unit reports only that `python` is not installed here.

**Not verified.** Proxy-to-socket access with a real Caddy or nginx (neither is installed here).

## 2026-11-10 — Deployment rehearsal

**Built.** `deploy/rehearse.sh`: in a temporary directory, with the real entry points, it writes the env file from `deploy/bip.env.production.example`, runs the pre-flight check (no FAIL, scheduled provider use off), creates an administrator from a root-only password file, starts `python -m bip.api` on a Unix socket, then checks: socket mode 660, public health 200, anonymous protected route 401, sign-in with an HttpOnly, SameSite=Strict, Secure cookie, system report 200 with a session, a write without the CSRF token 403, a database-only discovery pass with the token 201, and a backup that verifies.

**Result.** 11 of 11 steps passed on the first run; nothing contacted a provider and everything was removed afterwards.

**Not covered.** systemd (only `systemd-analyze verify` was run on the unit files), a reverse proxy reaching the socket, TLS and certificate renewal, a real host's disk and users, off-site backup copies. The kit has still never run on a real host.

## 2026-11-10 — A CI failure I had misread as a pass, and its cause

**What happened.** The backend `checks` job failed once, on commit `a08a911`: `tests/discovery/test_pipeline.py::test_per_run_signal_cap_is_applied_and_reported` raised a `UNIQUE constraint failed: transactions.tx_id` while seeding its own data (830 passed, 1 failed). I had not seen it: I read "check suite completed" events as "passed", but that event only says the suite finished. Only the explicit `check_run ... conclusion: failure` event carries the result, and I had not read that notification until later.

**Cause (reproduced).** The test helper `baseline()` built transaction ids from Python's built-in `hash(token)`, which is randomised per process, so two of the three tokens occasionally fell into the same bucket (`hash % 1000`) and collided: about a 1-in-300 chance per run. In 1,501 hash seeds, 5 collide (0.33%); the old line fails on the colliding seeds I ran (184, 592, 659) and the fixed line passes on all five. It never failed locally because the chance per run is small.

**Fix.** The helper now uses `int(token, 16)`, which is stable. The same pattern was not found anywhere else in the tests.

**Process change.** From now on a green CI means an explicit success conclusion on the head commit's check runs, not a completed suite.


## 2026-11-10 — CI result and CURRENT_STATE correction

**CI.** Head `cf07957`: backend `checks`, `frontend` and `secret-scan` each concluded `success` in both the push run and the pull-request run (explicit check-run conclusions, read after the jobs completed). Local re-run on the same commit: `uv run ruff check .` and `ruff format --check .` clean, `uv run pyright` 0 errors, `uv run pytest` 836 passed in 161 s. (A first attempt used the system Python instead of the project `.venv` and showed meaningless errors; it was discarded.)

**Documentation fix.** `CURRENT_STATE.md` still carried statements from earlier passes that the finished work contradicts: "nothing is committed", "the frontend has no sign-in screen", "no authentication", the Solana collector and reserve detection listed as blocked, "commit, push, PR not done", and the RPC hosts listed as denied. They were corrected to match the code and the live checks already recorded. No code changed.
