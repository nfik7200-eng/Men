# Source discovery addendum: open datasets, feeds and electronics retailers (2026-11-07)

Companion to `docs/SOURCE_DISCOVERY.md`. **Research only.** No product data was stored, the collector was not run, `data/watchtower.db` and `data/source_policies.json` were not touched. Nothing here is applied; section 6 is a *proposal*.

"Approved" below means **no prohibition found at review time. It is not legal permission.** Reviewer: engineering research agent (not legal).

## 0. Method, evidence quality and the review environment's limits

Requests: User-Agent `WatchtowerBot/0.1 (research review)`, one request at a time, at least 3 s between requests, at most 2 sample pages per candidate, no proxy rotation, no login/CAPTCHA bypass.

**Two kinds of failure, kept apart.** The review session reaches the web through an egress proxy with a host allowlist. For many hosts the proxy answered `CONNECT ... 403` (`000` in curl). That is the *session proxy refusing the host*; the site was never contacted and it is **not** a site 401/403/429/451. Those candidates are marked **Temporarily unavailable (not reachable from this session; site not contacted)** and need the host allowed before the review can be finished. No site returned 401/403/429/451 during this review.

Not reachable (site not contacted): `huggingface.co`, `www.kaggle.com`, `www.wikidata.org` and other Wikimedia hosts, `icecat.biz`, `www.ons.gov.uk`, `data.gov.uk`, `data.gov`, `data.europa.eu`, `www.bls.gov`, `zenodo.org`, `shop.pimoroni.com`, `www.kiwi-electronics.com`, `frame.work`, `backmarket.com`, `refurbed.com`, `newegg.com`, `microcenter.com`, `api.upcitemdb.com`, `openproductsfacts.org`, `openbeautyfacts.org`, `openfoodfacts.github.io`, `support.openfoodfacts.org`.

**Evidence labels used in every row**
- **[READ]** I fetched and read the primary page this session.
- **[SNIPPET]** only a web-search summary was seen. These are *not quotes*, not verified at source, and are treated as UNVERIFIED. Status for a reachable-in-principle source with snippet-only evidence is **Awaiting legal clarification**.

## 1. Summary table

| # | Candidate | Categories | Evidence | Status |
|---|---|---|---|---|
| 1 | Open Food Facts (bulk exports; product identity) | groceries (identity, GTIN); no prices; no laptops/phones | READ | **Approved for limited testing** (bulk dump only; not the API) |
| 2 | Open Prices (prices.openfoodfacts.org) | groceries only; crowdsourced EUR-mostly prices | READ (API samples, OFF terms); licence text for Open Prices itself [SNIPPET] | **Approved for limited testing** (segregated, attributed); production = **Awaiting legal clarification** |
| 3 | Cool Components (UK, Shopify) | maker electronics (GBP) | READ | **Awaiting legal clarification** |
| 4 | Pimoroni | maker electronics | READ (apex only) | **Temporarily unavailable** |
| 5 | Kiwi Electronics (NL) | maker electronics (EUR) | not reachable | **Temporarily unavailable** |
| 6 | Wikidata / Wikimedia Commons | entities, some product-model items; no prices | SNIPPET only | **Temporarily unavailable** (hosts blocked; licence claim UNVERIFIED) |
| 7 | Open Icecat | laptops/phones/tablets identity + specs (GTIN/MPN), no prices | SNIPPET only | **Awaiting legal clarification** (registration + terms conflicts) |
| 8 | Web Data Commons Product Corpus (from Common Crawl) | broad, stale snapshots, incl. electronics offers | SNIPPET only | **Rejected** as a price source (no licence, third-party rights, stale) |
| 9 | Amazon Reviews 2023 (McAuley Lab, Hugging Face) | broad incl. electronics; price field | SNIPPET only, licence not found | **Rejected** (scraped from Amazon, no licence found) |
| 10 | Kaggle / GitHub laptop-price datasets | laptops/phones | not reachable (Kaggle); no licence-clean one identified | **Rejected** (generic) / **Temporarily unavailable** to check specific ones |
| 11 | ONS UK consumer-price datasets | CPI item indices/price quotes (some tech items) | SNIPPET only | **Awaiting legal clarification** |
| 12 | UPCitemdb API (free tier) | UPC/EAN lookups, offers | SNIPPET only (third-party copy of terms) | **Awaiting legal clarification** |
| 13 | Refurbished-computer retailers (Back Market, refurbed), big electronics retailers (Newegg, Micro Center), Framework | laptops etc. | robots not reachable | **Temporarily unavailable** |

## 2. Detail: candidates that were actually read

### 2.1 Open Food Facts (OFF), bulk exports
- **URL:** https://world.openfoodfacts.org/data , terms https://world.openfoodfacts.org/terms-of-use , exports on https://static.openfoodfacts.org/data/ . [READ]
- **Coverage:** food/beverage products worldwide: name, brand, GTIN (`code`), categories, quantity. **No prices. No laptops, tablets or phones.** (Sister projects Open Products/Beauty/Pet Facts exist but were unreachable and are small.)
- **robots.txt** (`world.` and `static.`, identical, read 2026-11-07): `User-agent: *` -> `Disallow: /api`, `Disallow: /cgi`, `Disallow: /facets`. No `Crawl-delay`. Bulk files under `/data/` are not disallowed. **Important conflict:** OFF's own docs invite UA-identified API use, but robots disallows `/api` for generic agents. Our fetcher would refuse `/api`. So any integration must use the **nightly bulk exports**, not the API (which is also what OFF asks bulk users to do).
- **Licence text [READ]:** "The Open Food Facts database is available under the Open Database License. Individual contents of the database are available under the Database Contents License. Products images are available under the Creative Commons Attribution ShareAlike licence." (terms-of-use). Terms: licences "authorize the use and reproduction of the content for all purposes, including commercial use, under certain conditions, especially the attribution and the sharing under the same condition of derivative works."
- **Attribution [READ]:** re-users "have to mention the licence and to attribute the authorship to Open Food Facts with a link to https://openfoodfacts.org ... or the product page"; the data page suggests "Contains data from Open Food Facts, available under the Open Database License" and asks for clickable attribution in the UI.
- **Share-alike [READ]:** "If you publicly use any adapted version of this database, or works produced from an adapted database, you must also offer that adapted database under the ODbL and share your modifications back." **Decision needed:** merging OFF rows into the same database as Adafruit/SparkFun price rows could make the combined DB an "adapted database". Keep any OFF data in a **separate store/tables**, never join it into ranking or peer statistics, until counsel decides.
- **Images:** CC BY-SA. We never store images. Do not store them.
- **Rate rules:** the API rules page was unreachable; [SNIPPET] says 100 requests/min for product reads and a required custom `User-Agent` of form `AppName/Version (contact)`. Irrelevant if only bulk files are used. Dumps are generated nightly (MongoDB dump, CSV, Parquet, JSONL; sizes not checked).
- **Identifiers:** GTIN/EAN for food. Currency/region: n/a (no prices).
- **Effort:** low (one file download adapter + CSV/Parquet parse, ~2-3 days) but **low value for the maker/electronics niche**; only useful if we add grocery.
- **Status: Approved for limited testing** (bulk files only, segregated, attributed). **Human/legal step:** ODbL share-alike decision; choose attribution wording; confirm bulk download frequency (nightly at most) and use a contact UA.

### 2.2 Open Prices
- **URL:** https://prices.openfoodfacts.org (API `https://prices.openfoodfacts.org/api/v1/prices`, docs `/api/docs`). Two API sample calls made (limit used up: do not make more in review). [READ]
- **Coverage:** crowdsourced **grocery** prices with a shop location and proof image; observed 341,797 prices total on 2026-11-07. **Nothing for laptops, tablets or phones.** This is food only.
- **robots.txt:** `https://prices.openfoodfacts.org/robots.txt` returns the SPA HTML shell (HTTP 200), i.e. **no real robots file**. `policy.py` must be checked for how it treats an HTML body (it might parse it as no rules, or as unreliable) before any policy entry is trusted. `world.openfoodfacts.org` disallows `/api` for `*`, and a different host's absence of rules is not obviously an invitation; ask OFF (reuse@ address on the data page) to confirm API use for Open Prices.
- **Licence:** [SNIPPET] (data.gouv.fr and the Hugging Face dataset card summarised via search, not read at source): data under ODbL, proof images CC BY-SA 4.0, a daily `prices.parquet` bulk file exists. I attempted the GitHub README for the data licence; it contained no licence statement. **Treat the Open Prices data licence as UNVERIFIED until read on the official site.** The code repository's licence is a different thing and not relied on.
- **Fields seen:** `product_code` (GTIN), decimal `price` (e.g. 2.99), `currency` (EUR), `date`, `price_is_discounted`, `price_without_discount`, location (OSM node), `source` (app name), `owner` (a username), `proof_id`. No JSON-LD; REST JSON with pagination.
- **Data hygiene if ever used:** do **not** store `owner` (a username is personal data) or proof images; convert decimal price to integer minor units; missing price is NULL; label every figure **crowdsourced / unverified**, not merchant-verified; EUR only, never convert; do not use for "Verified" deal labels.
- **Effort:** medium (~3-5 days) because the model is shop-location prices, not online merchant offers; would need a new price-observation concept.
- **Status:** **Approved for limited testing** (segregated, attributed, nothing in ranking), **Awaiting legal clarification for production** (ODbL share-alike on a combined database; licence unread at source; robots ambiguity). **Human step:** counsel opinion + email OFF about API/bulk use.

### 2.3 Cool Components (coolcomponents.co.uk), re-evaluated
- Pages read 2026-11-07: robots.txt, `/agents.md`, `/policies/terms-of-service`, `sitemap.xml`, one product sitemap, one product page (2 samples max respected).
- **robots.txt [READ]:** `User-agent: *` `Allow: /`; disallows only Shopify internals and filtered/sorted collections (`/collections/*sort_by*`, `*filter*`). No `Crawl-delay`. Comment header: "Public product, collection, page, blog, policy, cart, and localized HTML is crawlable." and "Terms of Service: https://www.shopify.com/legal/terms ... Contact: bots@shopify.com".
- **Agent requirements [READ] (`/agents.md`):** "Agents must identify their requests as agent-initiated. Server-to-server requests and hosted or remote browsers must use Web Bot Auth ..." and the robots header says agents must use "supported UCP interfaces and provided WebMCP tools ... without scripted workarounds". It is **ambiguous whether a price-monitoring crawler counts as an "agent"**; the text is about shopping agents acting for buyers. We cannot supply Web Bot Auth signatures. New since the earlier note: robots now points to Shopify's own terms (https://www.shopify.com/legal/terms), which I did **not** read.
- **Merchant terms [READ]:** keyword search for scraping/spider/automated/data-gathering/exploit found **no prohibition** in the merchant's own terms (earlier keyword hits were navigation menu text only and are discounted). This is absence, not permission.
- **Structured data [READ]:** page JSON-LD `Product` + `Offer` (`name`, `sku` = "39", `brand`, `offers[].price` 1.3, `priceCurrency` GBP, `availability`). **No GTIN/MPN** in the sample; merchant SKU only (never an identifier for us). Description present (we would not store it).
- **Currency/region:** GBP, UK. Rate limit: none stated; use >= 2 s floor.
- **Effort:** low (generic JSON-LD adapter already exists for Adafruit/SparkFun); **value low** (maker electronics we mostly already cover, no GTIN).
- **Status: Awaiting legal clarification.** **Human step:** email the merchant / `bots@shopify.com` asking if low-rate, identified, price-only retrieval of public product pages is acceptable without Web Bot Auth; read Shopify's terms.

### 2.4 Pimoroni
- `https://pimoroni.com/` serves a 130-byte page that JS-redirects to `http://shop.pimoroni.com`; `/robots.txt`, `/sitemap.xml` and two terms guesses return 404 from an old nginx. `shop.pimoroni.com` is **blocked by the session proxy** (site not contacted). robots/terms/structured data unknown.
- **Status: Temporarily unavailable.** Human step: allow `shop.pimoroni.com` and re-run the review.

### 2.5 Kiwi Electronics
- `kiwi-electronics.com/robots.txt` redirects to `www.kiwi-electronics.com`, which the proxy refuses (and the earlier repo note says the redirect leaves our allowlist). Not evaluated.
- **Status: Temporarily unavailable.** Human step: allow host, re-review robots, terms, JSON-LD, EUR pricing.

## 3. Detail: candidates judged on search snippets only (UNVERIFIED, not read at source)

None of the statements below is a quote from the source; they are what search summaries reported.

- **Wikidata / Wikimedia Commons.** [SNIPPET] structured data in the main/property/lexeme namespaces is CC0; Commons file licences vary per file. No prices exist in Wikidata; product-model coverage is patchy; I did **not** confirm a GTIN property ID or any robots/API etiquette (hosts blocked). Value for us: low (identity hints only). **Status: Temporarily unavailable.** Human step: allow `www.wikidata.org`, `query.wikidata.org`, read Wikimedia's robot/User-Agent policies.
- **Open Icecat.** [SNIPPET] free registered tier for brand content (spec sheets, GTIN/MPN for many laptop/phone/tablet brands); data via XML/JSON with registration; usage rules summarised as: automated access through documented APIs permitted; request only the data you need; data may not be passed to ad networks or data brokers without written consent; Icecat may require deletion; selling a brand's product data prohibited; brand permissions can be revoked. No prices. **Registration is a human step**, and the "no brokers / no selling product data" clauses may conflict with `COMMERCIAL_ROADMAP.md` (data resale/API). Realistic **identity/spec** candidate for the electronics gap, but also stores descriptions/images we do not keep. **Status: Awaiting legal clarification.** Human step: read the Open Icecat licence and Fair Use Policy at source, register under the company contact, get counsel's reading on our commercial plans.
- **Web Data Commons Product Corpus.** [SNIPPET] schema.org Product/Offer extracted from Common Crawl (GTIN/MPN/price present) but from fixed historic crawls (e.g. 2020), and a WDC post says a valid licence is hard to define; underlying rights stay with the sites. Stale snapshots can never rank. **Status: Rejected as a price source.** Possible research-only use for matcher benchmarking, with legal review.
- **Amazon Reviews 2023 (McAuley Lab).** [SNIPPET] metadata includes price; I found no licence statement for the 2023 release, the 2014 release was reported as non-commercial research; data originates from Amazon pages. **Status: Rejected.**
- **Kaggle/GitHub laptop and phone price datasets.** Kaggle unreachable. Such datasets are almost always scraped copies of retail sites with weak or missing licences and unknown dates; none was identified as licence-clean. **Status: Rejected** unless a specific dataset with a clear open licence and documented provenance is found (then re-review individually).
- **ONS consumer price data.** [SNIPPET] ONS publishes item indices and price quotes and has research on web-scraped technology goods (laptops, desktops, phones, tablets). Licence (likely Open Government Licence) unverified; the data are anonymised category-level quotes with no product identity, so unusable for product matching and weak for "deals". **Status: Awaiting legal clarification** (low priority). Allow `www.ons.gov.uk` to verify.
- **UPCitemdb free API.** [SNIPPET] free tier about 100 requests/day (20 searches), one request about every 10 s, terms from a third-party copy say a "limited, non-exclusive, non-transferable ... license ... solely for Customer's operations". Identity lookup (UPC/EAN), not a price feed. **Status: Awaiting legal clarification.** Read live terms at source (host blocked).

## 4. Big retailers and refurbished marketplaces
Back Market, refurbed, Newegg, Micro Center and Framework: robots.txt requests were refused by the session proxy (site not contacted). Unknown, not evaluated. These are large sites that commonly run anti-bot protection; if a host is later allowed and shows CAPTCHA/anti-bot or 401/403/429/451, the answer is Rejected with no workaround. **Status: Temporarily unavailable.**

## 5. Honest coverage statement
- **Not obtainable for free from anything I could verify:** retail prices for laptops, tablets, phones and mainstream consumer electronics. No reachable, verified-permitted free source for them turned up. Open datasets that exist (WDC, Amazon-2023, Kaggle) are scraped, stale or unlicensed; ONS lacks product identity.
- **Realistic for the electronics gap:** product *identity/spec* via Open Icecat (GTIN/MPN, needs registration + legal reading), and *prices* only via approved affiliate/merchant feeds or marketplace APIs (eBay Browse etc.), which are out of the current free-only strategy and need keys, licence acceptance and, for feeds, merchant/network approval.
- **Groceries:** Open Food Facts (identity) and Open Prices (crowdsourced EUR prices) are the only large free open sources found, with ODbL share-alike obligations.
- **Maker electronics:** Adafruit and SparkFun remain the only approved real merchants.

## 6. Prioritised recommendation (max 3) and PROPOSED policy entries (NOT applied)

1. **Complete the Pimoroni / Kiwi / Cool Components reviews once hosts are allowed and the two merchant emails are answered.** Lowest engineering effort (existing JSON-LD adapter) and same category as today, so it adds merchants for peer comparison. Cool Components is blocked on the Web Bot Auth question.
2. **Open Icecat (identity/spec, no prices).** The only plausible way to get GTIN/MPN for laptops/phones/tablets, which would enable real matching when a price source arrives. Blocked on registration and legal reading of the data-broker and resale clauses.
3. **Open Food Facts bulk + Open Prices as a segregated, attributed grocery pilot**, only if the product strategy wants groceries; blocked on the ODbL share-alike decision. Otherwise defer.

Proposed `data/source_policies.json` entries. They use statuses that `policy.py` will **refuse to run** (`unverified`); hashes are left to the policy tooling (not computed here), and `approved` must only be set by a human after the legal steps. Review fields are placeholders.

```json
{
  "coolcomponents.co.uk": {
    "status": "unverified",
    "required_paths": ["/products/", "/sitemap.xml"],
    "tos_url": "https://coolcomponents.co.uk/policies/terms-of-service",
    "tos_finding": "2026-11-07: merchant terms contain no scraping/automated-access prohibition found; Shopify terms (https://www.shopify.com/legal/terms) not read; robots.txt allows /products/ but /agents.md requires Web Bot Auth for agent traffic (applicability to a price crawler ambiguous). Absence of prohibition, not permission.",
    "crawl_delay_s": null,
    "robots_sha": "<compute with policy tooling>",
    "tos_sha": "<compute with policy tooling>",
    "reviewed_by": "engineering research (not legal)",
    "notes": "JSON-LD Product/Offer, GBP, merchant SKU only, no GTIN. Do not enable until the merchant/bots contact confirms low-rate identified access is acceptable."
  },
  "icecat_open": {
    "status": "unverified",
    "required_paths": [],
    "tos_url": "https://icecat.biz/ (Open Icecat licence + Fair Use Policy: URL to be confirmed at source)",
    "tos_finding": "Search-snippet only; not read at source. Registration required; reported clauses on data brokers/resale may conflict with commercial plans.",
    "notes": "Identity/spec only (GTIN/MPN), no prices. API adapter must expose configured() and never log credentials. Never store descriptions/images."
  },
  "openfoodfacts_bulk": {
    "status": "unverified",
    "required_paths": ["/data/"],
    "tos_url": "https://world.openfoodfacts.org/terms-of-use",
    "tos_finding": "Read 2026-11-07: ODbL database, DbCL contents, CC BY-SA images; attribution and share-alike apply; robots.txt disallows /api, /cgi, /facets for *, so use nightly bulk files only.",
    "notes": "Groceries, no prices. Keep in a segregated store; share-alike decision pending. Open Prices (separate host) has no real robots.txt and its data licence was not read at source."
  }
}
```

## 7. Re-review triggers and follow-ups
- Allow the blocked hosts listed in section 0 in the review session's network settings, then finish sections 2.4, 2.5, 3 and 4.
- Read at source: Open Prices data licence, Shopify merchant terms, Open Icecat licence, UPCitemdb terms, Wikimedia robot policy.
- Verify how `watchtower/policy.py` handles a robots.txt URL that returns HTML before any Open Prices entry is considered.
- Legal: ODbL share-alike scope for a combined database; commercial reuse of aggregated data (already open in `SOURCE_DISCOVERY.md` section 1).
