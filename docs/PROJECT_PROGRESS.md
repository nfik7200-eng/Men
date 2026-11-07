# Project progress — updated 2026-11-07 (mission 005: acquisition intelligence)

## Mission 005 — verified now
| Claim | Evidence |
|---|---|
| Mission 004 preserved | branch `claude/sweet-franklin-dsta71`; the 8 Mission-004 commits (7d3b1a7 … 7dda481) intact; 14 new local commits on top; nothing pushed; PR #3 untouched (draft) |
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
| Cost | $0 additional; no paid service, plugin, daemon or cron installed |

**Tooling and model use (honest).** Primary model: Sonnet 5.5 (session was briefly switched to Opus 5.5 earlier by the user). The native
Advisor tool was available: **3 consultations in this mission** (before the acquisition design; before scheduling/DB/metric changes,
which caught 3 metric defects; one more before declaring done, see LOOP_ENGINEERING). Reviews: ordinary read-only
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
