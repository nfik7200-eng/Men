# Project progress — updated 2026-11-07 (completion programme)

## Completion programme — verified now
| Item | Evidence |
|---|---|
| Preservation | all 26 Mission 004/005 commits present at start; git bundle (sha256 `9ef4c00f…`, first 26 commits) + DB snapshot (`7f9419c3…`) delivered to the owner; the later commits are re-bundled in the final handover; real DB sha256 unchanged throughout; **54 commits ahead of origin, nothing pushed**, PR #3 untouched |
| Tests | system Python (no sklearn): **448 passed, 4 skipped** (the optional ML modules); ML venv: **499 passed**; pinned production venv (`requirements-lock.txt`): **448 passed, 4 skipped** |
| Coverage (three different figures) | core without the optional ML package, system Python: **93 %**; the ML package in system Python: **0 %** (its tests are skipped without sklearn); everything including ML, in the ML venv: **92 %** (5,205 statements) |
| Static checks | Ruff clean (`ruff.toml`); Pyright **75 errors = the pre-programme baseline, 0 new** (run with `--pythonpath .venv-ml/bin/python`); shellcheck 0.11.0 clean on `deploy/*.sh`; `systemd-analyze verify/security` on the units (paths exist only after install) |
| E2E | `e2e/run.sh` (Playwright, Chromium) desktop + Pixel 7 on a disposable cleaned copy: all checks pass, 0 horizontal overflow, 0 5xx |
| Security | independent review: 0 critical/high. Fixed + tested: robots ReDoS, gzip-bomb/size caps, robots cap, credentials on redirect/downgrade, signed expiring admin cookie, non-ASCII cookie, id overflow, per-client /watch and /go limits, API docs off, fixed-code messages, schedule NaN/bool, numeric loopback host forms, hardlink-safe live-DB guard, refused manual collections shown to admin. **Open (stated plainly):** DNS-rebinding to private IPs is not checked (hosts are allowlisted); `.env.example` `TRUST_PROXY=1` is only safe behind the provided nginx; rate limiter keys IPv6 per address (not /64) |
| Deployment package | `deploy/` + `docs/DEPLOYMENT.md`, exercised only in a temp prefix (install/update/rollback/restore/backup/healthcheck); independent review found 4 medium + several low defects, all fixed with tests (`tests/test_deploy_scripts.py`). **Not verified:** nginx config (no nginx binary; syntax untested), systemd runtime/hardening, useradd/permissions, Python 3.11/3.12, real VPS disk/RAM. Nothing was installed on this machine |
| ML | scikit-learn 1.9.1 in an isolated `.venv-ml` (pins == `pip freeze`); advisory harness only; independent review fixed 2 high + 6 medium + 7 low findings; **verdict on the real data: insufficient for supervised models, anomaly models and forecasting** (0 human labels, 1 day of prices, rule-derived labels are circular: single-feature AUC ≥ 0.99). No model feeds matching or ranking |
| Product / assistant / UI | audited review decisions; side-by-side review UI with confirmation and decision log; provenance card; admin budgets/schedule; honest "Price observed" label; market questions (new, price drops, history gaps) answered only from stored evidence; discovered-but-uncollected pages disclosed; performance: assistant 2.5-6.5x faster at 5,000 listings (docs/PERFORMANCE.md) |
| Advisor / reviews | Advisor consulted once in this programme (before closing), plus the Mission 005 calls; independent reviews were `general-purpose` subagents in specialist roles (security, frontend/UX, ML, deployment, source research). **`/review-council` is user-only and was never run.** Two agents hit a rate limit and were resumed. |

## Not met — and why (engineering vs external)
* **Real permitted data acquisition (external):** no live run: the policy gate requires a real `WATCHTOWER_CONTACT` (not invented) and owner approval. No free source exists for laptops/phones/tablets (docs/SOURCE_DISCOVERY_ADDENDUM.md).
* **Verified deals (external/time):** need ≥ 8 days of collection; the data has 1 day.
* **Useful ML models (data-limited):** insufficient data; harness and honest verdict only.
* **VPS deployment (external):** needs VPS access and approval; package tested in a temp prefix only.
* **Human decisions:** 12 review candidates; legal read of Adafruit/SparkFun terms; approval of the production cleanup (docs/DB_CLEANUP_RUNBOOK.md).
* **Push approval:** 54 local commits exist only in this temporary container (bundle delivered).

---

## Mission 005 record (2026-11-07: acquisition intelligence)

## Mission 005 — verified now
| Claim | Evidence |
|---|---|
| Mission 004 preserved | branch `claude/sweet-franklin-dsta71`; the 8 Mission-004 commits (7d3b1a7 … 7dda481) intact; new local commits on top (`git log 7dda481..HEAD`); nothing pushed; PR #3 untouched (draft) |
| Real DB untouched | sha256 of `data/watchtower.db` (`b5c68461…`) and `-wal` (`7c1b5baa…`) identical at start and end; every experiment/server ran on copies of the verified snapshot (sha256 `7f9419c3…`); `status`/`rematch`/migrations never run against the real DB |
| Tests / quality | **368 passed** (316 baseline); coverage **92 %** (= baseline); Pyright **75** errors (= pre-mission baseline, 0 new); ruff has no project config (default rules report ~150 pre-existing style findings; not a project standard) |
| Count reconciliation | 282 listings / 343 obs = **live scope only** (Adafruit 192 + SparkFun 90; 207 + 136 obs). 468 / 901 = **whole snapshot** (adds 186 demo listings / 558 demo obs). 440 / 831 = **stale plain `cp`** of the WAL-mode file (lost run 14: 28 SparkFun listings, 70 obs). All three are the same database at different scope/consistency; reports use the snapshot sha above |
| Why 1,241 of 1,526 URLs were unfetched | a per-run page cap (Adafruit 60, SparkFun 40; 60 % of it reserved for re-checks) with an (adapter tier, alphabetical) order; 8 runs, several small/manual. Not rejection or failure: 0 failed, 0 non-product among the 285 fetched. Confirmed from `crawl_urls` (974 + 267 never attempted) |
| Acquisition prioritisation | `watchtower/acquisition.py`: tier + cross-merchant name evidence (after matcher vetoes) + new coverage + bounded retry + bounded aging; every decision recorded per URL; budgets configurable with a ceiling. **Planned, not fetched**: on the snapshot, of the first 16/20 SparkFun discovery slots, URLs with strong/exact counterpart evidence: legacy order **1** → new order **8** (all 8 such URLs; 40 slots: 4 → 8) |
| SparkFun Pi Zero 2 W | discovered, in `required_urls` (robots-checked every run → eligible), tier 0, never fetched; legacy rank 27 of 267 → new rank 2 (exact name match with Adafruit listing #329). **Not collected**: live fetching is blocked by the policy gate (no real `WATCHTOWER_CONTACT`; I did not invent one) |
| Coverage (snapshot) | Adafruit 192 acquired of 1,166 eligible (16 %), 3 duplicate addresses; SparkFun 90 of 357 (25 %); extraction success 100 %; cross-merchant products **6** (unchanged: no live run was possible); pending review 12 after `rematch` on a copy |
| Honest limits | Adafruit's unfetched URLs are opaque ids: no name hints until a discovery run stores card names, so 0 of its 974 can be ranked by counterpart offline. Cross-merchant coverage improvement is *engineering-ready and measured as planned selection*, not measured live |
| Fixes found by review / UI | one disallowed URL no longer stops a run (only 401/403/451 do); disallowed URLs cost no budget; retry limit is a cooldown; scheduler skips a source whose last run was blocked; legacy `self` labels on shared products healed (6 on the snapshot) + `verify` invariant; research card shows history depth / other shops / unverified matches |
| Append-only history | ORM guard refuses updating/deleting an `Observation` (no DB trigger: that would be a migration on the live DB) |
| Scheduler | `data/schedule.json` committed **disabled**; runs only with config `enabled` **and** `WATCHTOWER_SCHEDULER_ENABLED=1`, through the policy gate; installs nothing (docs/SCHEDULING.md) |
| UI | Playwright desktop (1280×900) + Pixel 7: 10 pages each (home, search, product, coverage, 3 research queries, deals, watchlist, 404): 0 px overflow, 0 server errors; screenshots inspected; server ran on a snapshot copy |
| **Acceptance criteria NOT met** | #8 (existing merchants collected live) and #9 (cross-merchant acquisition improves) — blocked on a real contact + owner approval; cross-merchant products still **6**, not more; the 1 → 8 figure is *planned selection*, not acquisition; §17D repeat collection is **fixture-based on a disposable DB**, not live; the real DB still fails `verify` on 2 invariants (11 mirrored review pairs, 6 `self` labels) until the owner-approved backup + `rematch` |
| Cost | $0 additional; no paid service, plugin, daemon or cron installed |

**Tooling and model use (honest).** Primary model: Sonnet 5.5 (session was briefly switched to Opus 5.5 earlier by the user). The native
Advisor tool was available: **3 consultations in this mission**: (1) before the acquisition architecture; (2) one combined call before scheduling/DB changes and before finalising the coverage metrics (it caught 3 metric defects, fixed before reporting); (3) before declaring done. The mission's four decision points were therefore covered by three calls. Reviews: ordinary read-only
reviewer/simplifier subagents (`feature-dev:code-reviewer`, `code-simplifier`); `/review-council` **could not be run by me** (it is
user-invocation only), and `/frontend-design` was not needed (the one new page reuses the existing styles). `/feature-dev` was invoked
but its approval gates were skipped because the mission said to proceed autonomously. Pyright was run from the CLI (the LSP server was not exposed as a tool). Context7/GitHub MCP failed to connect (proxy 403) and were unused.

**Blockers / human decisions.** (1) Real `WATCHTOWER_CONTACT` + approval to run live collection (nothing live was fetched in this mission);
(2) approve a backup + `rematch` on the real DB (collapses 11 mirrored pairs, heals 6 labels) and note the first normal open adds nullable `crawl_urls` columns;
(3) legal read of Adafruit/SparkFun terms; permission outreach for a third merchant; (4) approve a scheduler (docs/SCHEDULING.md) for ≥ 8 days of history;
(5) review the 12 remaining match candidates.

---

## Mission 004 record (2026-11-06: loop engineering)

## Mission 004 — verified now
| Claim | Evidence |
|---|---|
| Baseline re-verified | HEAD bef6b7e = origin; clean tree; **284 passed**; coverage 90 % |
| Now | **316 passed** (~35 s); coverage 92 %; 8 local commits on `claude/sweet-franklin-dsta71`, **not pushed** (mission rule); PR #3 untouched |
| Real DB untouched | sha256 of `data/watchtower.db` and `-wal` identical before/after; all experiments on snapshot copies |
| Backup defect found & fixed | the DB is WAL; a file copy lost 28 listings, 70 observations, 1 run (mission-003 backups were made this way). `watchtower.cli backup` = verified online backup |
| Review queue | 23 pending → **12** on a rematched snapshot copy (22 were 11 mirrored pairs); rejecting a pair can no longer be undone by accepting its mirror |
| Assistant (real data, snapshot) | "pi 5 8gb", "pi 5 2gb", "pi 4 4gb" now return the exact board from **both** merchants (before: 1 merchant and/or Compute Modules); out-of-stock exact products shown + flagged, left-out listings reported |
| Integrity | `watchtower.cli verify`: 13 invariants hold on the snapshot except the 11 mirrors (fixed by `rematch` with the new code — on a copy: all OK) |
| Independent review | 3 read-only reviewer subagents (iterations 1–3, 2, 4–6); 2 + 1 + 5 high-confidence findings, all reproduced by failing tests and fixed; 1 claim did not reproduce as stated |
| Unchanged (needs time / permission, not code) | 2 merchants, 282 live listings, 1 day of history, 0 verified deals, no laptop/phone data, no third merchant |

**Human decisions needed:** (1) approve `backup` + `rematch` on the real DB to collapse the 11 mirrors; (2) decide the 12
remaining review questions; (3) approve a daily collection schedule with a real contact address (≥8 days → first
verified deals possible); (4) legal read / outreach for a third merchant. Details and backlog: `docs/LOOP_ENGINEERING.md`.

---

## Mission 003 record (2026-11-06: free-source scraping & price intelligence)

## Verified now (commands run in this session)
| Claim | Evidence |
|---|---|
| Tests | `python -m pytest` → **284 passed**. Real captured pages are in `tests/fixtures/real/`; mocked transport is used for HTTP, never for parsing. |
| Real data, real merchants | **2 merchants**: Adafruit (192 listings) and SparkFun (90 listings), all USD. **282 live listings, 343 live observations**, every live listing has a full provenance record (extraction method, content hash, HTTP status). Demo listings (books.toscrape, webscraper.io: 186) are flagged `demo` and are excluded from real views, deal ranking and peer statistics. |
| Collection runs (live) | Adafruit 4 runs / 4 ok; SparkFun 4 runs / 2 ok (2 were killed/discarded by me — one orphaned process, one run discarded after I found the stock-selector defect; both recorded as `failed`, not hidden). Throughput: Adafruit ≈0.5–0.65 pages/s (2 s delay floor), SparkFun ≈0.1 pages/s (**10 s Crawl-delay honoured**). 4–40 MB per run. |
| Integrity on real repeat crawls | 0 duplicate listings, 0 duplicate observations (one observation per listing per run). Product-page crawls are sampling crawls and are **never complete**, so absence never marks anything removed; removal now needs two consecutive 404/410 answers on the listing's own canonical URL. |
| Cross-store matching | **6 auto-matched products across the two merchants** (Pi 4 Model B 2/4/8 GB, Pi 5 2/4/8 GB), each checked by hand: same model, same RAM, no accessories. After the third review the review queue dropped from 84 to **23** pending candidates (mostly genuine ambiguity: "Pi 3 A+" vs "Model 3 A+", camera packs vs camera modules, Pi 500 kit naming). Nothing is auto-merged on weak evidence; neither merchant publishes GTINs/MPNs, so matching is brand + normalised name + unit values with hard vetoes. |
| Prices / stock | Adafruit: 73 in stock / 119 out of stock; SparkFun: 43 in stock / 27 out of stock / 20 unknown (stock is only in HTML; "unknown" is kept as unknown). |
| Deals | **No "Verified" deal exists** and none can yet: all live history is a single day (3+ collections over 7+ days required). Deals shown are labelled estimates from same-product peers only; out-of-stock, backorder, pre-order and stale peers are ignored. |
| UI | Server-rendered pages screenshotted and inspected at 1280×900 and Pixel 7 widths (home, search/research, product page with match status and history, deals, watchlist, admin with health + policy): 0 px horizontal overflow; demo / real / estimated / verified / stale are visibly distinct. |
| Cost | **$0** infrastructure and data: no paid API, proxy, LLM or hosted service. The app works with the LLM disabled (default). |

## Sources (see `docs/SOURCE_DISCOVERY.md`)
* **Approved (live):** Adafruit, SparkFun — meaning *no prohibition found in robots.txt and the terms/privacy pages read on 2026-11-06*. **This is not legal permission**; a legal review is needed before any commercial use, and EU/UK-style database-right questions are not assessed.
* **Rejected:** The Pi Hut (terms prohibit scraping although robots.txt allows it).
* **Unverified / awaiting:** Cool Components (needs Shopify Web Bot Auth identification), Pimoroni (JS shell, no robots.txt), Kiwi Electronics (not evaluated).
* **Demo only:** books.toscrape.com, webscraper.io test site.
* **Scaffolding, not live:** eBay Browse adapter — mock-tested only, unconfigured (no credentials, license agreement unread). It has never run against eBay.

## Tooling & model usage (honest)
* Plugins/tools used: the session's enabled plugins (superpowers skills, Playwright with local Chromium for screenshots, serena available). The Context7 and GitHub MCP plugin servers **failed to connect** (proxy 403) and were not used; GitHub operations went through the built-in GitHub tools.
* **The built-in Advisor feature is not active in this session** (it exists as `/advisor` in the CLI but is not enabled here). I did **not** use it and do not claim it.
* Opus was consulted **three times** only through read-only subagents launched with `model: "opus"` (I cannot independently verify the serving model): matcher/engine review, second review, third review. Findings were verified against the code/real DB before fixing; a few were declined where they did not reproduce. Everything else (implementation, tests, docs) was done by this Sonnet session.

## Not done / blockers (read before relying on anything)
* Only **2 real merchants**; both sell mostly maker electronics (the original laptop/tablet/phone categories have no real data). No third compliant source was found among free stores checked.
* No verified deals until history spans 7+ days; run the collection daily (`WATCHTOWER_CONTACT=<real contact> python -m watchtower.cli collect`) to build it.
* SparkFun is slow by design (10 s crawl-delay): a full catalogue pass takes many hours; only sampled/prioritised pages are fetched. Adafruit does not support conditional GET, so re-checks re-download pages.
* Legacy demo rows from mission 001 lack provenance fields and store descriptions; live rows store neither descriptions nor images (facts only).
* Lingering minor items: review queue still contains ~23 ambiguous pairs needing a human; Pyright reports 62 type warnings (Optional/bs4/SQLAlchemy noise, no crash-level bug); landed-cost FX rates are placeholders until a sourced rate is recorded; eBay not live; Ollama untested with a real model.
* Not production-ready. Security open items remain in `AUDIT_REPORT.md` §5. PR #3 stays **draft**; not marked ready, not merged.

## Next highest-value milestone
Run daily collection for 8+ days (cron/scheduled task with a real contact address) so the first genuinely *verified* price drops can appear, and in parallel obtain a legal read of the Adafruit/SparkFun terms and ask Cool Components/Pimoroni for explicit permission to add a third and fourth merchant (and a second currency).

## Round: frontend islands, currency correctness, JSON API (local, unpushed)
Commits (local branch `claude/sweet-franklin-dsta71`, not pushed): `2d3eb7a` deal radar and browse islands; `1d56926` currency exponents; `29de621` watchlist, match-review and admin JSON API.
* **Built:** `/deals` (server scores and evidence, no invented labels), `/search` browse island (real data by default, demo rows labelled, URL-driven), `GET/POST/DELETE /api/v1/watchlist` (per browser, 401 without a session), `GET /api/v1/match-candidates` and `POST …/decision` (admin token), `GET /api/v1/admin/status` (admin token, read-only).
* **Currency fixes:** price bounds, parsed prices, display text, watch targets and research shipping now use each currency's ISO 4217 exponent. Previously every amount was scaled by 100. Frontend and engine lists are pinned by tests.
* **Verified:** pytest 469 passed / 4 skipped; ruff clean; pyright 78 (was 81, none new); vitest 58; bundle fresh; Playwright journeys 44/44 on a scratch copy of the snapshot.
* **Not done:** UIs for watchlist, match review and admin status; live acquisition (no real `WATCHTOWER_CONTACT`); Contabo VPS inspection and deployment test (no access); specialist-agent orchestration (not requested); push of the six commits.
* **Decision:** an API client without the browser owner cookie cannot write a watchlist (401). Silently accepting writes that vanish was judged worse.

## Round: notifications, UX audit, restock history, security and architecture audits (local, unpushed)
Commits on `claude/sweet-franklin-dsta71` after `2ce6adf`: `90adc00` notifications; `52e0c31` acquisition compliance; `3228168` deployment record; `a3235fd` review fixes; `fe9c291` alerts test; `5203bee` browse island and UX fixes; `5467a42` restock history; `6ea0dfe` security review; `58448f3` architecture audit.
* **Built:** in-app alerts (target reached, back in stock from the observation history), read state, owner-scoped, explicit migration and rollback (`docs/NOTIFICATIONS.md`).
* **UX audit** (`docs/UX_AUDIT.md`): 16 pages at two widths, 32 views. Fixed the browse price-limit error, added a currency choice, fixed a checkbox layout and small targets. The audit now flags nothing except expected 404 console errors.
* **Security review** (`docs/SECURITY_REVIEW.md`): cross-origin refusals, owner isolation, forged cookies and admin CSRF checked by regression tests. No new exploitable defect.
* **Architecture audit** (`docs/ARCHITECTURE_AUDIT.md`): two small duplications removed; the `create_app` closure is the main debt, recommended for later splitting.
* **Verified on latest code:** pytest 506 passed, 4 skipped; ruff clean; pyright 78 (unchanged); vitest 61; typecheck clean; bundle fresh; ML venv 51 passed; browser journeys 50/50 on a fresh snapshot copy.
* **Not done:** VPS testing (no access); live collection (disabled, no contact, policy review pending); live database migration (needs approval); Review Council (guard refuses; not bypassed). A single independent reviewer covered the notification work, and found three defects, all fixed.

## Round: Watchtower completion goal (local, unpushed)
Commits after `2ce6adf`, on `claude/sweet-franklin-dsta71` (18 unpushed in total at the time of writing; nothing pushed):
- `cc32afb`: verified deals need eight days of history (`MIN_HISTORY_SPAN_DAYS = 8`); a never-observed listing no longer crashes deal scoring.
- `7e430ea`: one currency source. `watchtower/currency_exponents.json` is read by Python and generated into the frontend by `scripts/gen_currency_table.py`; a test checks the generated file is current.
- `bf9a489`: browse price-limit inputs. A typed limit needs a currency; a limit from the page's own link is not carried into another currency.
- `d190852`: unread-alert badge in the navigation, computed safely per page.

**Done and tested:** the eight-day rule, the single currency source, browse price limits with the currency rule, the badge.
**Not done in this round (recorded, not hidden):**
- A dedicated product comparison page (merchant comparison is on the product page).
- Interactive islands for the watchlist, match review and admin pages (server pages work without JavaScript).
- Splitting the route closure in `shopping/web/app.py`.
- Pyright: 78 errors remain (30 argument-type, 22 optional-access, 6 missing-import). Deal scoring holds 22 of them and needs careful review, not mechanical fixes.
- eBay: no sandbox or production credentials are available here, so nothing live is verified. The adapter stays mock-tested.
- Local Ollama: existing tests cover the optional path (off unless configured). No real model was tested.
- Landed-cost duties and taxes remain labelled assumptions, because no verified rate source is configured.
- Live collection and the live notifications migration stay off, pending the contact address, the legal review and your approval.

### Watchtower completion goal: continuation (local, unpushed)
Commits after `dd56639`: `f2fb5dc` interactive watchlist (save target, remove, signed-out state); `7512353` interactive match review on the admin page, and the admin session cookie now reaches the admin JSON it needs (scoped to three narrow paths). 21 commits unpushed at the time of writing.

Verified on this round's code: pytest 519 passed, 4 skipped; ruff clean; pyright 78 (unchanged); vitest 74; typecheck clean; bundle fresh; browser journeys 62/62 on a fresh copy (populated watchlist and review queue included); automated UX audit 32 views with nothing flagged.

Still not done: a dedicated product comparison page; splitting the route closure in `shopping/web/app.py`; Pyright's 78 errors; an admin status island (the server page already shows health and budgets); deployment-readiness documentation beyond what `docs/DEPLOYMENT.md` already records (no VPS access); a populated alerts browser journey; a contrast and screen-reader check; eBay live verification (no credentials here). Live collection and the live notifications migration remain off, pending the contact address, the legal review and approval.

### Watchtower completion goal: second continuation (local, unpushed)
Commits after `9f30334`: `c5c3ef5` the comparison page at `/compare?ids=...` (up to eight listings, each price in its own currency, with a note when currencies differ; linked from the product page), a populated-alerts browser journey, and `e2e/run.sh` migrating its own temporary copy so that journey runs.

Verified: pytest 522 passed, 4 skipped; ruff clean; browser journeys 68/68 on a migrated disposable copy (populated alerts, the badge and mark-read included); the live database hash is unchanged.

Still open, and why:
- Pyright: 78 errors remain. Deal scoring (`watchtower/deals.py`) holds 22: the type checker cannot see that missing prices are filtered first. Each fix narrows a type in scoring code, so it needs review as a behaviour change, not a mechanical edit.
- `shopping/web/app.py` route closure is not split. This is the largest debt and needs its own reviewed change.
- eBay live verification: no credentials are available. The adapter stays mock-tested.
- Local model: no real Ollama model was run. Existing tests cover the optional path.
- Admin status stays server-rendered. The page already shows health and budgets, and it is read-only.
- Contrast and screen-reader checks are not measured.
- Landed-cost duties and taxes remain labelled assumptions. No verified rate source is configured.

### Watchtower completion goal: third continuation (local, unpushed)
Commits after `c5c3ef5`: `7bb738f`/`5e78b58`/`b0a7c21`/`16a966f`/`9c04f05`/`c11c246`/`0efb34a` (Pyright and the defects it exposed: no-product matching, missing CLI and collector rows, optional scraper and assistant values, the ML path resolved from the isolated environment); `7269c92` the first route module (`shopping/web/routes_alerts.py`); `f3f87f0` contrast measured in both themes.

Verified on the latest code: pyright watchtower shopping reports **0 errors** (78 at the start of this goal); pytest 528 total, 524 passed, 4 skipped; ruff clean; vitest 74; typecheck clean; bundle fresh; generated currency table current; browser journeys 68/68 on a migrated disposable copy.

Real defects found and fixed along the way: `_members(None)` matched every unmatched listing; `_queue` could write a question with no product; a listing with no observation time crashed deal scoring; the admin session cookie never reached the admin JSON.

Still open:
- Route split: four modules moved out of `create_app` (`routes_alerts.py`, `routes_compare.py`, `routes_watchlist.py`, `routes_admin.py`; the watchlist module owns the per-browser owner check the alert routes reuse, and the admin module owns sign-in, the admin page, the review JSON and the status JSON). The search and product routes remain in the closure.
- E2E runner: `e2e/run.sh` now runs the migration on its copy with the override unset, so the live-database guard compares against the real path; and its cleanup trap no longer signals the process group when the server never started. Verified: 68/68 journey checks on a fresh migrated copy.
- eBay: no credentials are available; the adapter stays mock-tested.
- Ollama: no local model is configured or reachable here; existing tests cover the optional path.
- Deployment: rehearsed in a temporary prefix only; no VPS access.
- Admin status: server-rendered and read-only (the page already shows health and budgets).
- Screen-reader behaviour is not measured here; contrast is (see docs/UX_AUDIT.md).
