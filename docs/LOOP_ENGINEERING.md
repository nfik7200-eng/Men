# Loop engineering — how this platform improves itself (mission 004, 2026-11-06)

The goal is not more code. It is a platform that measurably gets better at collecting trustworthy real data,
understanding products and finding genuine price opportunities, at minimal cost. Every change follows one loop:

```
RESEARCH → IDENTIFY WEAKNESS → PRIORITISE → DESIGN → TEST (red) → IMPLEMENT → VERIFY (green + full suite)
→ INDEPENDENT REVIEW → MEASURE (before/after on a snapshot copy) → DOCUMENT → REPEAT
```

## 0. Ground rules for every iteration (all loops)
1. **One weakness per iteration**, backed by evidence (a failing check, a real-data example, a reproduced bug).
   No evidence → it goes to the backlog, not into code.
2. **Measure on a snapshot, never on the real DB.** `python -m watchtower.cli backup --out <scratch>/snap.db`
   (WAL-safe, verified, read-only), then copy the snapshot for each experiment and point the app at it with
   `WATCHTOWER_DB_URL=sqlite:///<copy>`. A plain `cp data/watchtower.db` is **wrong** (see iteration 1).
3. **Red before green.** The regression test must fail on the old code for the reason found. A test that passes
   on the bug is a bad test (iteration 5 found two of those in my own iteration-2 tests).
4. **Full suite + `verify` before commit.** `python -m pytest` and `python -m watchtower.cli verify --db <copy>`.
5. **Independent review** by a read-only subagent for every iteration; high-confidence findings are fixed in the
   next iteration, declined findings are recorded with the reason.
6. **Never weaken a test, never manufacture data.** No synthetic observations in the real DB, no back-dating,
   no relaxing the verified-deal rule (≥3 collections over ≥7 days) without a separately approved decision.
7. **Stopping conditions:** stop an iteration when the target metric is met and nothing regressed; abandon it
   (and record why) when the fix needs external data/permission, or the evidence does not reproduce.
   Hard cap for an autonomous mission: 6 iterations.

Reusable tooling: `watchtower.cli backup` (snapshot), `watchtower.cli verify` (13 invariants + metrics),
`tests/` (316 tests, real captured fixtures), `scripts/loop_bench.py` (fixed query/deal bench on a snapshot copy), the project skill `.claude/skills/improvement-loop/SKILL.md`.

## 1. The loops

| Loop | Objective | Inputs | Outputs | Verification | Success metrics | Safety boundaries |
|---|---|---|---|---|---|---|
| **A Research & discovery** | Find better methods and new *permitted* sources | papers/docs/open-source projects; robots.txt + terms of candidate merchants | written findings in `docs/`, backlog items, `source_policies.json` entries | a human reads the terms; robots parsed by our own parser; decisions dated | sources with a recorded decision; no source live without approval | never treat robots as permission; no paid tools without approval; unclear → `unverified` |
| **B Acquisition** | More real, fresh, complete listings per request | approved sources, crawl state (`crawl_urls`), run metrics | observations with provenance | fixture tests from real pages; run metrics (pages/s, fail %, bytes); `verify` | coverage of discovered URLs, unknown-stock %, failure rate, staleness | crawl-delay/robots/allowlist are floors; no bypass of 401/403/451; no uncontrolled live crawls; never infer removal from a sampling crawl |
| **C Product intelligence** | Same product ↔ same product, nothing else | listings, specs, identifiers, review decisions | products, match candidates | hard-veto tests from real false merges; mirror/queue invariants; human spot-checks of auto pairs | correct auto pairs (hand-checked), pending queue size, false-merge count (target 0) | never auto-merge on weak evidence; human decisions are final; one listing per merchant per product |
| **D Price intelligence** | Genuine drops only, misleading deals exposed | observations per listing, peers | deal scores/labels | rule tests; `verified_history_listings` metric | verified deals only when history allows; 0 stale/OOS peers | never fabricate history; stale never ranks; no FX without a sourced rate |
| **E Assistant** | Right product, best real price, honest caveats | parsed query, live listings, deal scores | ranked picks + caveats | query tests on real titles from **both** merchants; before/after query bench on a snapshot | exact product from every merchant that sells it in top picks; different product classes not substituted | works without an LLM; unavailable shown flagged, never hidden |
| **F Quality, security, reliability** | Keep the system trustworthy as it changes | code, DB snapshots, test runs, reviews | fixes, tooling, docs | `verify` invariants, full suite, coverage, reviews | 0 invariant violations, suite green, review findings closed | fail-closed admin; no secrets in code/logs; real DB never mutated by experiments |

## 2. Architecture audit (pipeline, current state)

| Stage | Implementation | Tests | Known weaknesses / failure scenarios | Missing verification → added? |
|---|---|---|---|---|
| Source discovery | `policy.py` gate + `source_policies.json`; 2 approved, 1 rejected, 3 unverified | `test_policy_hardening`, `test_robots` (real robots files) | terms re-review is manual; database-right risk unassessed | — (needs human/legal) |
| Fetching | `fetcher.py`: allowlist every hop, own RFC 9309 robots, crawl-delay, 2 s floor, retry 2/4/8 s, 429 Retry-After (seconds only, ≤60 s), STOP on 401/403/451, conditional GET | `test_engine`, `test_audit_engine`, `test_real_sources` | after a 429 the pace is not lowered for the rest of the run; Retry-After HTTP-date ignored; body size checked after full download | backlog B3 |
| Extraction | JSON-LD Product/Offer (`structured.py`), merchant HTML for stock (SparkFun) | real reduced fixtures | 20/90 SparkFun listings have *unknown* stock; brand/specs from titles only (no descriptions stored by rule) | metric `unknown_stock_live` added |
| Normalisation | `specs.py`, `identifiers.py`, `canon_brand` | spec/brand tests | neither merchant publishes GTIN/MPN: 0/282 identifiers; RAM parsed for 33/282 | coverage metrics in `verify` |
| Storage | SQLite WAL, `sync_columns` forward-only migration | `test_international` (migration) | **plain-copy backups lost committed WAL data** (fixed, it. 1); CLI commands other than backup/verify run `init/ensure_*` writes even for "status" | `backup`, `verify` added |
| Matching | staged scorer + hard vetoes, review queue, prune/revalidate | `test_matching_*`, `test_review3`, `test_review_queue` | **mirrored review questions** (fixed, it. 2/5); remaining 12 questions need a human | invariants `mirrored_pending_candidates`, `review_status_has_pending_question` |
| Observations | one per listing per run, 304 reconfirm, two-step removal | `test_real_sources`, `test_audit_engine` | only 1 day of real history; 1,241 of 1,526 discovered URLs never fetched | `live_observation_days`, `verified_history_listings` |
| Deal detection | `deals.py` explainable score; verified rule ≥3 collections/≥7 days | `test_peers`, `test_review3`, `test_audit_engine` | cannot produce a verified deal before ~8 days of collection (by design) | — |
| Assistant | rule-based `assistant.py`, optional local LLM summary | `test_shopping`, `test_audit_assistant`, `test_assistant_units` | **unit spelling decided relevance; different product classes displaced the asked-for model** (fixed, it. 4/6) | `scripts/loop_bench.py` |
| Frontend | FastAPI + Jinja2, server-rendered, Android + desktop checked in mission 003 | `test_audit_web`, `test_dashboard` | review UI shows candidates one by one (no pair view) | backlog C2 |

**Baseline (2026-11-06, before mission 004 changes, measured on a verified snapshot):** 284 tests passing in ~28 s;
statement coverage 90 % (`watchtower/cli.py` 0 %); 282 live listings / 2 merchants / 343 live observations / 1 day;
6 cross-merchant products (hand-checked correct); 23 pending review candidates (**22 of them 11 mirrored pairs**);
0 price-change events; 20 live listings with unknown stock; 0 listings with verified-grade history; DB 0.9 MB + 3.3 MB WAL;
`top_deals` 146 ms, assistant answers 70–290 ms, peak memory < 3 MB. All 13 data invariants hold except the mirrors.

## 3. Iteration log (mission 004)

| # | Loop | Weakness (evidence) | Change | Before → after (measured) | Review |
|---|---|---|---|---|---|
| 1 | F | Real DB is WAL; `cp` of the `.db` lost **28 listings, 70 observations, 1 run** and showed 71 instead of 23 candidates. My mission-003 backups were made this way. | `watchtower.cli backup` → `db.snapshot()`: online backup API, read-only, one read txn, O_EXCL, verify integrity + per-table counts | naive copy 440 listings / 831 obs → snapshot 468 / 901 (= live DB) | 1 important (partial file on error) → fixed in 5 |
| 2 | C | 22/23 pending candidates were mirrors; rejecting one side left the mirror acceptable (contradictory decisions) | ask once per pair; a decision closes its mirror; prune collapses legacy mirrors | pending 23 → **12**; 6 auto products unchanged | 1 important (partner status not updated with a single candidate; my tests only passed because they injected mirrors) → fixed in 5 |
| 3 | F | integrity probes were ad hoc | `watchtower.cli verify`: 13 invariants + metrics, read-only, exit 1 on violation | pre-fix snapshot: FAIL (11 mirrors); post-fix copy: OK | 1 important (currency false positive) → fixed in 5 |
| 4 | E | "pi 5 8gb" dropped Adafruit's identical "8 GB RAM" board; "pi 5 8 GB" lost the RAM constraint (4 GB ranked 1st) | unit-normalised keywords/text; digit tokens match whole values | "pi 5 8gb": 1 merchant → **both merchants**; "8 GB": 4 GB board no longer ranked 1st | see 6 |
| 5 | C/F | review findings of 1–3 | partner/mirror status recomputation, never downgrade confirmed/auto; snapshot cleanup; latest-obs currency check | 6 new regression tests; queue still 23 → 12; verify OK | — |
| 6 | E | "pi 5 2gb" answered with 2 Compute Modules (boards OOS at both shops, hidden); "pi 4 4gb" ranked 2 CM4s above the Pi 4 Model B | named model: listings with an un-asked product-type word (compute, case, cable…) do not displace the exact product; OOS exact shown + flagged | "pi 5 2gb": CM5×2 → Pi 5 2GB from **both** merchants (OOS caveat); "pi 4 4gb": CM4×2 + board → board from **both** merchants; accessory queries unchanged | 3 important regressions + 2 status gaps → fixed in 6b |
| 6b | E/C | review of 4–6 (each reproduced by a failing test first): '2.4ghz' split into '4ghz'; '8 GB RAM' became a literal keyword and excluded a 16 GB laptop; product-type rule also hit laptops ('Touchscreen Display'), ignored plurals ('cases') and dropped listings silently; accept left a 'review' listing whose question pointed at the vanished product, and a lone 'self' counterpart unconfirmed | decimal-preserving keywords; spec requirements not keywords; type rule only for maker-electronics, only when no type is asked, with a caveat; plural fallback; status fixes | 316 passing; bench unchanged except the new 'left out' caveats; final copy: rematch → `verify` OK, 12 pending | — (findings closed; one reviewer claim, '5.1V'/'2.5A', did not reproduce because 2-character tokens are already ignored — the longer-unit form did, and is fixed) |

**After (final code, same snapshot):** 316 tests passing in ~35 s; coverage 92 % (cli.py 53 %); `verify` on a
rematched copy: all invariants OK, 12 pending candidates, 6 cross-merchant products unchanged; assistant answers
18–65 ms, `top_deals` ~40 ms (machine noise is large at this size; treat timings as "no regression", not a speed-up).
**What did not change (and cannot by code):** 282 listings, 2 merchants, 1 day of history, 0 verified deals.

Failed/declined experiments: a monkeypatched "concurrent write during backup" test was abandoned (the
`sqlite3.Connection` C type cannot be patched); consistency rests on the single read transaction, confirmed by review.

## 4. Prioritised backlog (value × confidence ÷ risk)

| ID | Loop | Item | Evidence | Kind | Priority |
|---|---|---|---|---|---|
| B1 | B | **Prioritise fetching URLs whose names overlap the other merchant's catalogue** (cross-store coverage), within the same crawl budget | SparkFun's Zero 2 W URL discovered but never fetched; 1,241/1,526 discovered URLs unfetched; only 6 cross-merchant products | engineering | **1** |
| O1 | D | Run the approved daily collection for ≥8 days (needs a real contact address and a scheduler the owner approves) | 1 day of history; verified deals impossible until then | **operational/human** | **1** |
| C1 | C | Human decisions on the 12 remaining review questions (admin UI) | `pending_review_candidates` = 12 after rematch | **human** | 2 |
| H1 | F | Apply iteration 2 to the real DB: `backup`, then `rematch` (collapses the 11 mirrors) | real DB still holds mirrors (`verify` FAIL) | **human approval** (this mission must not mutate the real DB) | 2 |
| B2 | B | SparkFun unknown stock (20/90): inspect real pages offline from fixtures to find the missing stock marker | metric `unknown_stock_live` = 20 | engineering (needs fixtures of those pages → one polite fetch each) | 3 |
| F1 | F | Make read-only CLI commands (`status`, `health`, `policy`) not run `init/ensure_*` writes | cli.py runs them for every command | engineering | 3 |
| E1 | E | Cross-merchant answer text: "same product at N merchants, best price X at Y" from matched products | picks now include both merchants but the text does not say they are the same product | engineering | 3 |
| B3 | B | Adaptive pacing after 429 (slow the host for the rest of the run); Retry-After HTTP-date | fetcher code review | engineering | 4 |
| C2 | C/UI | Review UI: show the pair side by side with the veto/evidence list; one click decides the pair | 12 questions, mirrors gone | engineering | 4 |
| D1 | D | Misleading-discount detector: flag `original_price` claims never observed as an actual price in history | needs history (O1) | engineering, after O1 | 5 |
| A1 | A | Third merchant: ask Cool Components / Pimoroni for permission; legal read of Adafruit/SparkFun terms | only 2 merchants, both maker-electronics | **external permission / legal** | 1 (non-engineering) |
| A2 | A | Laptop/tablet/phone real catalogue | none of the evaluated free sources is a permitted electronics retailer | **external** | — |

**What code cannot fix:** the number of merchants, product categories (laptops/phones), the length of real price
history, and legal permission. Those need the owner: approvals, outreach, a scheduler, and time.


## 5. Mission 005 iteration log (acquisition intelligence, 2026-11-07)

| # | Loop | Weakness (evidence) | Change | Measured |
|---|---|---|---|---|
| 1 | B | one robots-disallowed URL stopped the whole run (`FetchBlocked` meant both disallow and 401/403/451) | `FetchDisallowed` subclass; per-URL record + 7-day re-check; server blocks still STOP | 2 tests red→green |
| 2 | B/C | 1,241 of 1,526 URLs unfetched, fetch order = (tier, alphabetical), no reasons stored | `acquisition.py` scorer + `plan_run`, 10 new `crawl_urls` columns, `cli plan` | SparkFun first-20 slots with counterpart evidence 1 → 8 (planned) |
| 3 | B | discovered vs acquired not measurable | `coverage.py`, `cli coverage`, `/coverage`, `verify` metric | real: Adafruit 192/1166, SparkFun 90/357 |
| 4 | E | assistant could not say what is uncollected / unverified | `_uncollected`, also-sold-at, possible-match, history depth | 6 tests; Zero 2 W answer discloses the uncollected SparkFun page |
| 5 | F/D | advisor review: acquired counted URLs (195 vs 192 listings); 1-letter suffix = "strong"; unverified match printed as "no other shop"; trigger-based immutability would migrate the live DB | duplicates by claimed listing; len≥3 rule; ORM append-only guard | metrics corrected before reporting |
| 6 | B/F | scheduler absent | disabled-by-default double opt-in scheduler | 9 tests; no cron/service |
| 7 | F | reviewer: retry_limit permanent; scheduler retried blocked sources; disallowed URLs ate budget; all-disallowed run reported ok; O(U×L) ranking; public /coverage cost | 7 fixes, each reproduced red first | suite 368 green |
| 8 | C/UI | Playwright: "match self 0.60" next to another shop's listing of the same product (6 rows) | `prune_candidates` heals `self`→`auto` when the matcher agrees; invariant in `verify` | verify OK on rematched copy |

Declined/limits: `/review-council` is user-only (not run); live collection impossible without a real contact (no fabricated
contact); Adafruit offline ranking by counterpart impossible until names are stored by a discovery run; ruff has no project
config so only Pyright (75, unchanged) is used as the type gate.

Backlog after mission 005: (a) run one live, policy-gated discovery+collection once a contact exists, then re-measure
cross-merchant products (target > 6) with `coverage`/`verify`; (b) bare 3-digit model numbers ("pi 500") are ignored by assistant keywords;
(c) price-history stats/volatility on the product page need ≥ 8 days of data (O1); (d) pair-view review UI (C2); (e) a DB-level
append-only trigger as an approved migration.


## 6. Completion programme iteration log (2026-11-07)

Orchestrator: main session + project skill `improvement-loop` (no external loop plugin installed). Parallel agents owned DISJOINT
paths; shared modules were integrated by the orchestrator. Budget per unattended run: <= 8 iterations, <= 6 concurrent agents.

| # | Loop | Weakness (evidence) | Change | Measured |
|---|---|---|---|---|
| 1 | F | temporary container; 26 unpushed commits | git bundle + verified WAL-safe snapshot delivered | bundle verify ok |
| 2 | C | review decisions not auditable | decided_at/by/note + evidence snapshot | 3 tests |
| 3 | F | production cleanup unprovable | `cleanup-preview` on a temp copy + runbook | 23 -> 12 pending, 6 labels healed, 0 regroups |
| 4 | E | assistant could not answer market questions | new / price-drop / history-gap intents from stored evidence | 5 tests |
| 5 | E/UI | independent Playwright review: misleading labels, no provenance, one-line review, hidden budgets | 13 defects + 3 gaps fixed | e2e 30 checks both viewports |
| 6 | F | independent security review | ReDoS, size caps, cookie, limits, SSRF forms (14 fixes) | 40+ tests red->green |
| 7 | M4 | independent ML review: 2 high, 6 medium, 7 low | circularity/trust-tier/leakage/anomaly fixes, honest verdict kept | 51 ML tests |
| 8 | M10 | independent deploy review: 4 medium + lows | umask, silent tar, rollback guards, restore units, nginx, docs | 8 temp-prefix tests |
| 9 | F | perf benchmark at 5,000 listings | one history query per answer | 5.2 s -> 2.1 s; 11 s -> 1.7 s |
| 10 | F | Advisor close-out | rollback auto-revert regression, hardlink guard, prod-pin suite run, lock file, coverage honesty | tests added, 448 pass on pins |

Declined / limits: `/review-council` (user-only); live collection (no contact); nginx syntax test (no binary); fallback of the
nested-threshold ML caveat is documented, not fixed. Next loop: run one policy-gated live discovery + collection when a contact
exists, then re-measure cross-merchant products and history depth; decide the 12 review candidates; apply the approved cleanup.
