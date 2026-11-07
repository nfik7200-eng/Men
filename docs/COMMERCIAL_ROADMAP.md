# Commercial roadmap (updated 2026-11-06) — hypotheses, gates and costs

Evidence grades as in `DATA_SOURCE_RESEARCH.md`: **[P]** primary page read · **[S]** search summary only · **[U]** unverified.

## 1. What is true today
* Engine, consumer UI, eBay adapter *scaffold* and landed-cost/FX foundations exist and are tested (143 tests).
* **No real retailer data exists.** The only collected data is two scraping-practice sandboxes. The eBay adapter has run only against mocked/synthetic data. No credentials, approvals, or affiliate accounts exist.
* No paid service was purchased. No revenue stream is live.

## 2. Gates (in order — nothing public before gate 1)
| # | Gate | Owner | Blocks |
|---|---|---|---|
| 1 | Read eBay API License Agreement: may we store prices/price history, show them, and later sell a data API? | You | Public launch, data-product revenue |
| 2 | eBay developer account + sandbox keys; allow `api.sandbox.ebay.com` in the environment | You | Live adapter test |
| 3 | Production keyset (approval steps [U]) | You/eBay | Real listings |
| 4 | eBay Partner Network membership; country eligibility [U]; campaign id | You/eBay | Affiliate revenue |
| 5 | ≥4 weeks of daily observations | Time | First *verified* deals |
| 6 | One destination's customs tariff verified from the official authority | You/research | Landed-cost claims |

## 3. Affiliate readiness (mission §11) — what was verified
| Item | Finding |
|---|---|
| Amazon eligibility | Need an Associates account for the marketplace **and ≥10 qualifying sales in the past 30 days** to use the Creators API; PA-API 5 is deprecated and answers `403` [P]. ⇒ not available at launch; revisit after traffic exists. |
| eBay eligibility / countries / rates | EPN homepage read but contains no eligibility or rate table [P]; reported electronics ≈1.5–4 % [S]; country rules [U]. |
| Link attribution (eBay) | Send `X-EBAY-C-ENDUSERCTX: affiliateCampaignId=<10-digit id>[,affiliateReferenceId=…]` and the API returns `itemAffiliateWebUrl` carrying `campid`/`customid` [P]. Implemented (`EBAY_CAMPAIGN_ID`) and tested with mocks. |
| Click recording | Every outbound click goes via `/go/{id}` and is logged (listing, network, time). **Limit:** stored provider URLs have a fixed `customid`, so per-click sub-ID reconciliation against EPN reports is *not* possible without rewriting the URL per click (permission [U]). Conversions/revenue are visible only in the network dashboard; we do not receive them. |
| Disclosure | Amazon: exact sentence *"As an Amazon Associate I earn from qualifying purchases."* is required [P] and wired in. EPN wording [U] — generic disclosure shown whenever any affiliate link is active. |
| Ranking independence | Ranking/scoring modules contain no reference to affiliate data (static test) and rankings are unchanged when affiliate config + clicks are enabled (behavioural test). Sponsored placements: not built; must stay outside ranking code. |

## 4. Economics (estimates — validate; no vendor was contacted)
| Item | Now | At real scale (~50k listings, daily) |
|---|---|---|
| Hosting | $0–10/mo (SQLite, small VM) | $10–40/mo (Postgres/VPS) |
| Data access | $0 (sandboxes). eBay API fees [U] | [U]; feeds/APIs may be free for approved partners |
| Proxies | $0 — and **do not use**: API/feeds make proxies unnecessary and scraping retailers via proxies is a compliance risk | $0 |
| LLM | $0 (rules; optional local Ollama, untested) | $0–50 if hosted model is ever used for match review |
| Email/push alerts | not built | $0–20/mo |
| Storage | <100 MB/month | ~1–3 GB/year |
Margin is determined by traffic and affiliate conversion, not infrastructure: at a 2 % commission on electronics, $1,000/month revenue needs ≈$50k/month of referred sales [arithmetic on a reported rate, S].

## 5. Staged plan
1. **Weeks 0–2 — remove the unknowns:** gates 1, 2, 4. Decide by evidence whether eBay is viable for price history. Run the adapter in the eBay sandbox; fix parser mismatches.
2. **Weeks 2–6 — real data:** production keys; one marketplace (US or GB), 3–5 electronics categories, daily collection; add `getItem` refresh for watched items. Gate 5 starts here.
3. **Weeks 6–10 — trust + traffic:** public read-only site (accounts first if watchlists are exposed), SEO price-history pages, a waitlist for "landed cost to East Africa". Measure: waitlist size, alert→click rate.
4. **Months 3–5 — monetise:** affiliate links live and disclosed; free/Pro tiers enforced (limits already coded; no billing); validate $5–10 willingness to pay before building payments.
5. **Months 5–9 — business monitoring pilot:** 3–5 retailers/importers at $29–99, using the same Events/Watch engine; **requires the retailers' own competitor lists and terms review**, scraping only where permitted.
6. **Later:** API/data products (only if gate 1 allows), second provider (affiliate-network feeds), price prediction after ≥6 months of history.

## 6. Key risks (ranked)
1. Data-license terms may forbid price history (gate 1). 2. Affiliate approval/country eligibility. 3. Thin electronics commissions. 4. Matching quality on messy real titles. 5. Time to accumulate history. 6. Unverified duty/tax data for the East-Africa angle.

---
# Update — 2026-11-06 (mission 003): free-source scraping strategy
**Strategy shift:** independent data collection from free, permitted public sources instead of API/affiliate dependence. Affiliate, subscriptions, business monitoring, API/data products and sponsored placements remain *compatible* (schema and link infrastructure unchanged) but none is a prerequisite.

## What this changes economically
| Item | Before (API-first) | Now (free-source scraping) |
|---|---|---|
| Data cost | API fees [U] + approvals | **$0** (public pages, 1 request per 1.5–10 s per merchant) |
| Infrastructure | $0–10/mo | **$0 measured** (SQLite, one process, this sandbox); a $5 VM when hosted |
| Proxies / scraping services / LLM | — | **none** (and never used to evade blocks) |
| Main risk | approval & license | **legal/permission per merchant**: terms can forbid scraping even where robots.txt allows (The Pi Hut), merchants can opt out, EU/UK database rights, catalogue size per merchant is bounded by politeness (SparkFun's 10 s crawl-delay ⇒ ≈360 pages/hour) |
| Coverage speed | fast once approved | **slow by design**: ~100–400 pages/merchant/day; breadth comes from more merchants, not faster crawling |

## Gates (replace the API gates for the scraping path)
1. **Per-merchant permission review** (`data/source_policies.json`): robots + terms read; re-review every 90 days; opt-out honoured immediately (`source disable`).
2. **Legal review before any public launch or data product**: scraped *facts* (price, availability) are lower-risk than descriptions/images (not stored), but database-right and contract exposure for a *public comparison site or API* is a lawyer's call, not ours.
3. **≥4 weeks of daily observations** before any "verified deal" claim (rules already enforced in code).
4. Merchant outreach: ask approved merchants for a product feed or written permission — a feed beats scraping on every axis (accuracy, stock, GTINs, legality).

## Next highest-value milestones
1. **Daily scheduled collection** for Adafruit + SparkFun (cron/systemd; ~25 min/day) to start the history clock, then run until week 4.
2. **Add 2–3 more approved merchants** in the same categories (each needs a policy review; many UK/EU shops use Shopify terms that forbid scraping — prefer merchants with explicit permission or feeds).
3. **Review-queue UX** for ambiguous matches (accept/reject exists in admin) and a GTIN/MPN enrichment source if a licensed/open one fits.
4. Only then: accounts + alerts → pro tier; business monitoring pilot on the same engine.
