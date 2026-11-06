# Free-source discovery & compliance (2026-11-06)

Goal: real product/price data from **free, publicly accessible, permitted** sources — scraping first, no paid APIs, proxies or data contracts.
Everything below was checked from this environment on 2026-11-06 with the crawler's own identifying User-Agent, one request per page, honouring `Crawl-delay`. Nothing was bypassed.

## 1. Rubric (what "approved" means — and does not mean)
| Status | Requires |
|---|---|
| **approved** | `robots.txt` allows our User-Agent on every path class we fetch **and** the terms of service were read with no prohibition on automated access/extraction/reuse found (or explicit permission) **and** a named reviewer/date |
| **unverified** | robots allows, but terms unreadable/ambiguous, or the site imposes an access condition we cannot meet |
| **rejected** | `robots.txt` disallows, **or the terms prohibit** automated access/extraction, or login/CAPTCHA/anti-bot |

**"Approved" = "no prohibition found at review time". It is not legal advice and not a legal permission.** Risks that remain even for approved sources: (a) terms can change (we hash and re-review every 90 days, and re-check robots on every run); (b) **EU/UK database rights** can restrict repeated extraction of a substantial part of a catalogue even when each fact is free — we sample a bounded subset and never mirror catalogues; (c) **product descriptions and images are copyrighted** — we store neither (only name, price, currency, availability, identifiers, and a hash of the description for change detection) and link out to the merchant; (d) commercial use of the *aggregated dataset* (e.g. selling it as an API) is a separate question that needs legal review before launch. Enforcement is in code: `watchtower/policy.py` refuses to run any non-demo source unless its policy is `approved`, current, robots-compliant, and `WATCHTOWER_CONTACT` is a real contact.

## 2. Public online stores evaluated
| Source | Homepage / product pages | robots.txt (for `*`) | Terms of service | Sitemap | Structured data | Identifiers | Price / currency / stock | Pagination / discovery | **Decision** |
|---|---|---|---|---|---|---|---|---|---|
| **Adafruit** (adafruit.com, US) | 200 / 200 | Allows `/product/`, `/category/`; disallows `/api/ /ajax/ /search /i/ /includes/` | Read in full: ordering/shipping/returns; **no automated-access or reuse clause found** | `sitemap.xml` (6,400 product URLs, no names) | JSON-LD `Product`+`Offer` (name, sku, price, currency, availability, condition) | merchant SKU (numeric product id) only; **no GTIN/MPN/brand** | USD, ex-tax; stock in JSON-LD | `/category/N` lists product links (no pagination needed for scope used) | **APPROVED** (policy 2026-11-06) |
| **SparkFun** (sparkfun.com, US) | 200 / 200 | **`Crawl-delay: 10`**; product pages allowed; disallows `/catalog/ /search* /api/ /checkout`; query-string URLs avoided | Terms of Sale + Privacy Policy read: **no automated-access or reuse clause found** (absence of a prohibition, not a permission) | `/media/sitemap.xml` (5,745 URLs, products + categories mixed) | JSON-LD `Product`+`Offer` (price, currency); stock only in HTML (`div.stock`) | SKU like `DEV-18713`, **`mpn` field just repeats the SKU** (rejected as an MPN); no GTIN | USD, ex-tax | sitemap discovery, scope-filtered by slug | **APPROVED** (policy 2026-11-06), 10 s pacing honoured |
| **The Pi Hut** (thepihut.com, UK) | 200 / 200 | Allows crawling (`Allow: /`) | **Prohibits it**: "reproduce, duplicate, copy, extract, sell, resell or exploit any portion of the Services" and "use any robot, spider, scraping, data gathering and extraction tool … (agentic AI) or automated or manual means to access the Services" | `sitemap.xml` | — | — | — | — | **REJECTED** — robots allows it, the contract forbids it |
| **Cool Components** (coolcomponents.co.uk, UK) | 200 / 200 | Allows product pages | No prohibition found, **but** `agents.md` requires agent traffic to identify itself via Shopify *Web Bot Auth* (cryptographic request signing) and prefer their UCP/MCP interfaces | `sitemap.xml` | Shopify | — | GBP | — | **UNVERIFIED** — we cannot meet the identification requirement; next step: ask `bots@shopify.com`/the merchant |
| **Pimoroni** (pimoroni.com, UK) | apex 200 (130-byte JS shell); `www.` did not resolve here | **No robots.txt** (404) | not located | none | JS single-page app: HTTP-first extraction yields nothing | — | — | — | **UNVERIFIED** — would need Playwright + a clearer permission signal; not worth it now |
| **Kiwi Electronics** (NL) | redirects to a host not in the allowlist | not read | not read | — | — | — | EUR | — | **NOT EVALUATED** |
| webscraper.io test site, books.toscrape.com | fine | permit | practice sites | — | CSS | — | demo prices | pagination | **DEMO fixtures only** (flagged `demo`, excluded from real results) |
Lesson recorded: `robots.txt` is necessary but **not sufficient** — the Pi Hut's terms contradict its robots file; the standard Shopify merchant terms (used by many UK shops) forbid scraping outright, so "it's a Shopify store" is a reason for caution, not a green light.

## 3. Real data actually obtained (see `docs/PROJECT_PROGRESS.md` for live counts)
Adafruit and SparkFun are the two approved real merchants. Their catalogues genuinely overlap in Raspberry Pi, Arduino, RP2040/Pico and micro:bit products; both price in **USD**, so same-currency comparison is valid. **Neither publishes GTINs or manufacturer part numbers**, so matching relies on brand + normalised name signature (stage 3 of the matcher) with hard vetoes for generation/RAM/condition/bundle differences; ambiguous pairs go to the review queue. No price is converted across currencies.

## 4. Public catalogues, feeds and APIs (evaluated, not integrated)
| Source | Licence / terms (read) | Use | Decision |
|---|---|---|---|
| **Open Food Facts** (world.openfoodfacts.org) | Database: **ODbL**; individual contents: Database Contents Licence; images: **CC BY-SA** — free for commercial use *with attribution and share-alike on derived databases* | Product identity/GTIN metadata for **groceries** — **not prices**, not our maker-electronics niche | **Postponed.** Re-evaluate if we add grocery categories; share-alike obligations need a deliberate decision |
| Open Prices (prices.openfoodfacts.org) | robots reachable; crowd-sourced prices (groceries); licence not reviewed beyond Open Food Facts' terms | Possible free real grocery prices | **Not evaluated** beyond reachability |
| Sitemaps / schema.org JSON-LD | Intended for machine consumption | Used (Adafruit, SparkFun) | **Integrated** |
| Merchant product feeds / affiliate-network feeds | Need merchant or network approval | — | Out of scope by strategy (no affiliate approvals) |
| Free APIs | eBay Browse etc. need keys + licence acceptance | Optional only | eBay adapter remains scaffolding (see `DATA_SOURCE_RESEARCH.md`) |

## 5. Re-review triggers
`robots.txt` hash change (auto-detected each run; auto-reject if a needed path is now disallowed), terms hash change (re-read manually every 90 days — `tos_sha` is stored), any 403/429 block (run stops, status `blocked`), complaint or opt-out request (disable the source via `python -m watchtower.cli source disable KEY`).
