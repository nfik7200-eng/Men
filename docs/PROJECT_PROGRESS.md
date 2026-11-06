# Project progress — updated 2026-11-06 (mission 004: loop engineering)

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
