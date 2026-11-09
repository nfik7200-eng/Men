# Commercial data-source research (2026-11-06)

**Decision: integrate eBay's Browse API first (scaffolding built, NOT live).** One provider only. Reasoning below.

## 0. How reliable is this document?
Evidence grades: **[P]** read directly on the provider's own page this session · **[S]** only seen in a web-search result summary (not read directly) · **[U]** unverified / from memory / could not be read.
Pages we could **not** read (returned HTTP 403 from eBay's servers — we did not try to bypass this): eBay *API License Agreement*, *Application Growth Check*, *Authorization*, *Get Started*, *API call limits*. These hold exactly the terms that matter most (data retention, limits, production approval). **Every row depending on them is [U] and is a pre-launch gate, not an assumption.**

## 1. Candidate sources

| Criterion | **eBay Browse API** | eBay Feed API (bulk) | **Amazon Creators API** (replaces PA-API 5) | Best Buy Products API | Affiliate-network feeds (e.g. Awin) | Jumia (East Africa) | Manufacturer specs / licensed data providers |
|---|---|---|---|---|---|---|---|
| **Access** | OAuth client-credentials → `GET /buy/browse/v1/item_summary/search` (keyword, category, GTIN, EPID, filters; ≤10,000 results) [P] | Daily/weekly bootstrap + hourly change files per L1 category (Feed Beta API) [P]; access terms [U] | REST: SearchItems, GetItems, GetVariations, GetBrowseNodes [P] | Registration → API key [S] | Join network, apply to each advertiser; product feeds via the network tools [S] | No public API or official affiliate page found [S] | Varies per vendor; not evaluated |
| **Eligibility** | eBay developer account + keys [P]; production keyset approval / "Growth Check" [U] | [U] (commonly restricted) | Active Amazon Associates account for the marketplace **and ≥10 qualifying sales in the past 30 days** [P] | Free registration; key non-transferable [S] | Network vets publisher; each advertiser approves individually [S] | Third-party blogs disagree on eligibility (one says US-resident) [S] | Commercial contract |
| **Cost** | API cost [U]; affiliate = commission | [U] | Free API, commission-based; but gated by sales | Cost [U] | Free to join (current FAQ) [S]; commission-based | Commission reported 4–13% by blogs [S, unreliable] | Paid (unknown) |
| **Coverage** | eBay marketplaces (many countries; header `X-EBAY-C-MARKETPLACE-ID` selects) [P/S]; new + used + refurbished; ~"80% new items" per EPN homepage claim [P, marketing] | Whole categories | Amazon catalogue per marketplace (list of 20+ countries in docs) [P] | US retailer only | Merchants that run affiliate programmes | Jumia countries | Specs only / prices |
| **Update frequency** | On-request; getItem `COMPACT` for price/availability refresh [P] | Hourly deltas [P] | On-request [U] | "near real-time" [S] | Feed schedule per merchant | n/a | n/a |
| **Restrictions** | API License Agreement **unread (403)** [U]. Must not show items past `itemEndDate` [P] | [U] | Operating Agreement requires the exact disclosure *"As an Amazon Associate I earn from qualifying purchases."* [P]; IP licence per locale [P]; price-caching rules [U] | **Terms limit storing/caching to ≤72 hours** [S] → incompatible with price history | Per-merchant terms | n/a | Licence terms |
| **Monetisation** | EPN: header `X-EBAY-C-ENDUSERCTX: affiliateCampaignId=<10-digit id>` makes the API return `itemAffiliateWebUrl` [P]; EPN approval, rates (reported 1.5–4% electronics) and country eligibility [S/U] | same | Associates commission (reported 1–4% electronics) [S]; 180-day/3-sale rule from a third-party summary only [S] | Best Buy affiliate separate [U] | Commission per merchant | KOL programme reported [S] | none |
| **Engineering effort** | Low–medium: JSON, OAuth, `next` pagination. **Done** (adapter + tests) | High (TSV files, large) | Medium, but blocked | Low, but blocked by 72h rule | Medium per merchant | n/a | Medium |

Direct quotes/facts used: eBay Browse guide (developer.ebay.com/api-docs/buy/static/api-browse.html); Amazon Creators API introduction and *PA-API 5 Deprecation Notice* (affiliate-program.amazon.com/creatorsapi/docs/en-us/…); Amazon Associates Operating Agreement (affiliate-program.amazon.com/help/operating/agreement); EPN homepage (partnernetwork.ebay.com).

### Findings that change the plan
1. **Amazon is closed to a new business today.** PA-API 5 is deprecated and returns `403 AccessDenied`; its successor needs an existing Associates account *and* 10 qualifying sales in the last 30 days [P]. We can only get there after we already drive purchases — Amazon is a *later monetisation partner*, not a data source for launch.
2. **Best Buy's terms (per search summary) forbid keeping data >72 hours**, which would make price history — our core asset — impossible. Must be read directly before any use [S].
3. **eBay's own license is the single biggest unknown.** Whether storing observations for price history, displaying them, or later selling a data API is allowed is governed by the (unread) API License Agreement. If it forbids this, the eBay route is limited to live comparison without history, and the "data products" revenue stream is off the table.
4. **Affiliate networks (Awin etc.) are the natural second source** (feeds list names, prices, images, deeplinks [S]) but each merchant must approve us, so they depend on having a live site with traffic.

## 2. Selection: eBay Browse API
| Criterion (weight) | eBay Browse | Amazon | Best Buy | Awin feeds | Jumia |
|---|---|---|---|---|---|
| Data availability (3) | **High** (documented API, sandbox) | None until sales | High but 72h cap | Medium (per-merchant) | None found |
| Legal permission (3) | **Unknown → gate** (license unread) | Gated | Likely blocks history | Per merchant | Unknown |
| Affiliate fit (2) | **Built into API** (`itemAffiliateWebUrl`) | Gated | Separate | Native | Unclear |
| Integration effort (1) | **Done** | — | Low | Medium | — |
| International reach (2) | **Many marketplaces; ships globally (varies by seller)** | Marketplace-bound | US only | Per merchant | East Africa only |
| Cost (1) | Free keys likely [U] | Free but gated | [U] | Free to join | n/a |
eBay wins on *access we can actually obtain next*, not on certainty. It is selected **conditionally**: reading the API License Agreement is step 0.

## 3. Market: US/UK electronics + East-Africa landed cost — does it make sense?
**Hypothesis:** buyers in Uganda/Kenya who import electronics lack a tool that shows the *true delivered price* (shipping, duty, VAT, clearance) across stores; ordinary comparison sites show only the sticker price. That is a real, differentiated pain **if** (a) sellers ship there, (b) duty/tax data can be verified, (c) the affiliate programme will pay on such traffic.

| Question | Status |
|---|---|
| Do eBay sellers ship to Uganda? | Item-level; `Listing.ships_to` column exists; Browse filter for delivery country must be validated [U] |
| Verified Ugandan duty/VAT rates? | **No.** Shipped rates are a labelled placeholder. Needs the customs authority's tariff, per product class [U] |
| Will EPN approve/pay a Uganda-based publisher or traffic? | **Unknown** — country eligibility not readable [U]. If not, the monetisation story for the home market fails |
| Existing competition for "landed cost" | Not researched (no verified data) |
| Engineering cost of the idea | Low: already built (v2 estimator, FX store, `ships_to`) |
**Conclusion:** do **not** bet the company on the East-Africa angle yet. Start with US/UK electronics (data + affiliate path exists) and treat landed-cost-to-East-Africa as a *validated-by-users* feature: collect interest via a waitlist, verify one destination's tariff end to end, check EPN country rules. If any of those three fails, the platform still stands as a general price-intelligence product.

## 4. Exact activation checklist for the eBay adapter (nothing below is done)
1. **Read the eBay API License Agreement** (data storage/retention, display, aggregation, resale). Record the answer here. *Blocks everything public.*
2. Create an eBay developer account (user action) and a **sandbox** keyset. Set `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` (env only), leave `EBAY_ENV=sandbox`.
3. Ask for the environment network allowlist to add `api.sandbox.ebay.com` (and later `api.ebay.com`) — currently **not allowed** in this environment.
4. Run `python -m watchtower.cli collect --source ebay_browse` → expect sandbox test listings flagged *sandbox*. Verify token flow, field names and `next` pagination against the real sandbox; adjust the parser (it is defensive but was built from documentation + synthetic fixtures).
5. Request a **production** keyset (steps/approval [U]); set `EBAY_ENV=production`, choose `EBAY_MARKETPLACE` (`EBAY_US`/`EBAY_GB`).
6. Join **eBay Partner Network** (check eligibility and country rules); set `EBAY_CAMPAIGN_ID` (10 digits) so `itemAffiliateWebUrl` is returned; confirm EPN's disclosure wording.
7. Determine and encode the daily call budget (limit not readable [U]); the collector is capped by `max_pages` and a 1.5 s delay by default.
8. Add `getItem` COMPACT refresh for stock/price of watched items (summaries do not state availability; we store `unknown`).

## 5. Costs (estimates, mark as such)
Infrastructure stays ≈$0–10/month. eBay API access cost [U]; EPN is commission-based. No paid service was purchased or subscribed.

## 6. Review status of the eBay adapter (this build)

What is verified in this repository, and how:

| Claim | Evidence |
|---|---|
| Not collected live without a reviewed policy, even with credentials and a real contact | `tests/test_acquisition_gate.py` (production eBay refused; sandbox not gated) |
| Unconfigured source does nothing and says what is missing | `tests/test_ebay_adapter.py::test_unconfigured_source_does_nothing_and_says_what_is_missing` |
| Token request uses Basic auth and the right scope; token is cached | `tests/test_ebay_adapter.py::test_oauth_basic_auth_scope_and_token_cache` (mocked transport) |
| Marketplace and affiliate headers are sent as documented | `tests/test_ebay_adapter.py::test_request_headers_marketplace_and_affiliate_context` |
| Pagination link to a foreign host is blocked | `tests/test_ebay_adapter.py::test_next_link_to_foreign_host_is_blocked` |
| Rejected credentials stop without retry and do not leak the secret | `tests/test_ebay_adapter.py::test_rejected_credentials_block_without_retry_or_leak` |
| Currency, condition and unknown fields are preserved or flagged, never guessed | `tests/test_ebay_adapter.py::test_end_to_end_ingest_preserves_currency_condition_and_flags_unknowns` |

What is NOT verified, and therefore not claimed: the adapter has never talked to eBay (no credentials, no network
allowance); the OAuth endpoint and scope are implemented from the documented client-credentials flow and are unconfirmed
against the sandbox; the API License Agreement has not been read, so storing price history and displaying data from eBay
is unapproved. The adapter remains **scaffolding** in every user-facing statement. Activation is the checklist in
section 4 and needs the owner's decisions and accounts.
