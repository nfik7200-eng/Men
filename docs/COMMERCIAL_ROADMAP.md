# Commercial roadmap (hypotheses, not facts)

## Economics (estimates — validate)
| Item | MVP | At ~100 real sources / 50k listings |
|---|---|---|
| Hosting | $0–5/mo (free tier VM or small VPS; SQLite) | $10–40/mo (VPS + Postgres) |
| Scraping | $0 (polite, no proxies) | Proxies are a *compliance smell*: prefer APIs/feeds. If needed $50+/mo |
| LLM | $0 (rules; optional local Ollama) | $0–50/mo if hosted LLM used for match review only |
| Notifications | $0 (in-app) | Email ~$0–20/mo (transactional tier), push free |
| Storage | <100MB/mo observations | ~1–3GB/yr at daily observation for 50k listings |
Margins are dominated by customer acquisition and data access, not infrastructure.

## Affiliate programmes (secondary sources only; official terms NOT yet read — verify before relying)
- Amazon Associates: reports say new accounts must generate 3 qualifying sales within 180 days or be closed; electronics ≈1–4%. Scraping Amazon is not permitted by its terms; use the official Product Advertising API only after approval.
- eBay Partner Network: approval not guaranteed; electronics reportedly ~1.5–4%; country eligibility unverified.
Implication: affiliate on electronics is thin-margin — it needs real traffic volume. The platform ships provider-agnostic `affiliate.json` templating + click log; nothing is enabled.

## Staged plan
1. **Now (week 0)** — pipeline + demo. Gate: reproducible install, tests green. ✅
2. **Weeks 1–4: real data access** — apply to eBay Partner Network + obtain Browse API keys; pick ONE market/category (recommendation: electronics, US/UK sources, import-cost view for East Africa). Add 2–3 compliant adapters with overlapping products so cross-store matching is proven live. Daily scheduled collection. Gate: ≥4 weeks of history → first *verified* deals.
3. **Weeks 4–8: acquisition** — SEO pages per product/price history; weekly "real vs fake discount" content; free watchlist + email alerts (add accounts). Metric: weekly active watchers, alert→click rate.
4. **Months 2–4: monetise** — affiliate links live (disclosed); Pro $5–10 (more watches, 6h alerts, long history, import calculator) — test willingness to pay with a waitlist before building billing.
5. **Months 4–8: Business monitoring** — pilot with 3–5 local retailers/importers at $29–99 on manually onboarded competitor lists; weekly competitor report from Events. Gate: 3 paying pilots.
6. **Later** — API/data products, sponsored (labelled, never affecting rank), price prediction once ≥6 months history.

## Key risks
Data-access legality (largest), affiliate rejection, thin margins, long time-to-trust (needs history), cross-currency without sourced FX.
