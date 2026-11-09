# Acquisition and source compliance: merchant-by-merchant report

Scope: every acquisition path in `watchtower/sources`, `watchtower/policy.py`, `watchtower/fetcher.py` and `data/source_policies.json`
as of commit `90adc00`. Live collection is **disabled**; nothing here was run against a live merchant. Evidence labels:
**verified** = checked by a test or by reading code; **recorded** = stated in the reviewed policy file; **assumed** = not checked.

## Gate that every live run must pass (`watchtower/policy.py`)
1. A real `WATCHTOWER_CONTACT` (placeholder domains such as example.com are refused). **verified**: `tests/test_acquisition_gate.py`.
2. A policy row with status `approved` and a review date not expired. **verified**: `tests/test_acquisition_gate.py`, `tests/test_policy_hardening.py`.
3. robots.txt readable, and every required path still allowed. An unreadable or temporarily failing robots.txt refuses the run. **verified**: `tests/test_robots.py`, `tests/test_policy_hardening.py`.
4. Practice sites and API sandboxes (`demo`, `sandbox` kind) are not gated, and every row they produce is labelled as test data. **verified**: `test_ebay_sandbox_is_labelled_and_not_gated`.

## Fetcher controls
| Control | Value | Evidence |
|---|---|---|
| Minimum delay per host, real merchants | ≥ 2.0 s (Crawl-delay can only raise it) | `config.MIN_LIVE_DELAY_S`, recorded |
| Product pages per source per run | default per adapter, ceiling 500 | `config.MAX_PAGE_BUDGET`, `WATCHTOWER_BUDGET_<KEY>` |
| Response size | 16 MB pages, 512 KiB robots | `fetcher.MAX_BYTES`, `MAX_ROBOTS_BYTES`; **verified** by `test_fetcher_limits.py` |
| Redirects | at most 4; every hop must stay on the source's host allowlist; no credentials on host change or https→http | `fetcher.MAX_REDIRECTS`; **verified** by `test_security_fixes.py` |
| Timeouts and retries | 20 s timeout, 3 retries with backoff | `config.HTTP_TIMEOUT_S`, `MAX_RETRIES`; recorded |
| 401, 403, 451 | stop immediately, no retry, no bypass | recorded in CLAUDE.md; **verified** by fetcher tests |

## Merchant by merchant

| Merchant | Kind | Policy status | Evidence on file | Gaps and risks | Live now? |
|---|---|---|---|---|---|
| **Adafruit** | live adapter | approved (reviewed 2026-11-06) | robots and ToS hashes recorded; required paths `/product/`, `/category/`; crawl delay recorded | "approved" means no prohibition was found, **not** legal permission. A legal read of the terms is still required. Re-review due within 90 days (about 2027-02-04). | No: contact not configured |
| **SparkFun** | live adapter | approved (reviewed 2026-11-06) | same structure as Adafruit | same as Adafruit | No: contact not configured |
| **eBay** | adapter, kind depends on `EBAY_ENV` | **no policy row** | adapter is mock-tested; sandbox by default | In `production` the adapter becomes live and the gate refuses it (no policy). Scaffolding only; not connected to live data. | No |
| **books.toscrape.com** | practice site | not required | labelled `demo` | Not commercial data; never in real views | Not applicable |
| **webscraper.io test site** | practice site | not required | labelled `demo` | As above | Not applicable |
| **thepihut.com** | candidate, rejected | rejected | Shopify ToS prohibit scraping and automated extraction | Correctly excluded. Do not revisit without a new written permission. | Never |
| **coolcomponents.co.uk** | candidate, unverified | unverified | robots allows product pages; ToS shows no prohibition found; `agents.md` on the site restricts agent access | Needs the full text of that restriction read and a decision before any use | Never |
| **pimoroni.com** | candidate, unverified | unverified | no robots.txt (404); storefront is a JavaScript SPA | Extraction would need a browser, which we do not use. Needs a permitted data route first. | Never |
| **kiwi-electronics.com** | candidate, unverified | unverified | redirects to a host not on the allowlist; robots and terms not read | Needs review, then an allowlist entry | Never |

## Missing evidence before any live collection
1. A real contact address for `WATCHTOWER_CONTACT`. **Owner decision.**
2. A legal read of the Adafruit and SparkFun terms. The policy records "no prohibition found", which is not permission. **Owner decision.**
3. A policy row for eBay production, if it is ever wanted. **Not planned.**
4. For coolcomponents, the full text of the agent-access restriction.

## Verified facts vs assumptions
- **Verified:** the gate's refusals and its sandbox exemption (new isolated tests, 4 cases). The existing policy, robots, fetcher and security suites pass on this commit.
- **Recorded, not verified by us:** the robots and terms hashes, and the review date. These come from the earlier review.
- **Assumed:** that the merchants' sites have not changed since review. The gate re-reads robots.txt on every run, but terms are only re-checked at the 90-day review.

## Verdict
Acquisition is **ready to run only after the two owner decisions above**. Until then the gate refuses every live source. This is the intended state.
