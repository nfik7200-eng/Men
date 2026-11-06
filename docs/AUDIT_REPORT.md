# Independent audit report — Watchtower (mission 002)

Audited commit: `a60ce15` (PR #3 head at the start). Fixes: `05a8de4`, `c8b6040`, plus the commit containing this file.

## 1. Method and honest limits
* **Environment verified first:** clean tree, HEAD = remote branch, PR #3 draft/open with no checks or reviews, 46 tests passing, no services running.
* **Two evidence sources.** (a) A read-only reviewer subagent launched with the model override `opus` wrote probe scripts and reported 22 findings with output. (b) I re-ran the highest-severity probes myself on a throwaway worktree of the original commit before trusting them (see "Reproduced by").  I cannot independently verify which model actually served the subagent beyond the override I set; treat "Opus reviewed" as "a subagent configured for Opus reviewed". This session itself ran on Sonnet; there was no model switching.
* Nothing here is a penetration test, a dependency/CVE scan, or a formal security review. Production security is **not** claimed.
* Reproduced-by legend: **both** = reviewer probe + my independent run on the old code; **reviewer** = reviewer probe only (I verified via the regression test failing before / passing after, not on old code); **regression** = found by a new test during fixing.

## 2. Summary
| ID | Sev | Area | Finding | Status | Reproduced by | Regression test |
|---|---|---|---|---|---|---|
| F1 | High | collector | HTTP-200 page with 0 parsed items ⇒ "complete" run ⇒ whole category marked removed | Fixed | reviewer | `test_f1_*` (2) |
| F2 | High | deals | Spike-then-return-to-normal scored **80, "Verified price drop", high confidence** | Fixed | **both** | `test_f2_*` |
| F3 | High | ingest/deals | Duplicate / burst observations satisfy "3+ observations" | Fixed | **both** | `test_f3_*` (2) |
| F4 | High | matching | Auto-merges of S21 vs S21 Ultra, S21+ vs S21, bundle vs single, refurbished vs new, colour variants | Fixed | **both** (3 of 6) | `test_f4_*` (10) |
| F5 | Med | matching | Short/no-brand MPN match returned auto before the category check | Fixed | reviewer | `test_f5_*` |
| F6 | High | web security | Loopback-only admin trusted `client.host`; same-host reverse proxy ⇒ everyone admin; `"testclient"` in prod allowlist | Fixed (fail-closed) | **both** | `test_admin_disabled_without_token…`, `test_weak_admin_token…` |
| F7 | High | web security | No CSRF protection; cross-origin POST succeeded | Fixed (Origin/Referer check + SameSite=Strict admin cookie) | **both** | `test_cross_origin_posts_are_refused` |
| F8 | High | web security | Admin UI broken with token set; token in URL/Referer; non-constant-time compare | Fixed | reviewer | `test_admin_login_flow_*` |
| F9 | Med | fetcher | SSRF / politeness bypass: off-host `next` links and redirects followed, robots only on first hop | Fixed | reviewer | `test_f9_*` (3) |
| F10 | Med | collector | Run stuck `running` after any unexpected exception | Fixed | reviewer | `test_f10_*` |
| F11 | Med | collector | `Source.enabled` never honoured | Fixed (+ CLI/UI toggles) | reviewer | `test_f11_*`, `test_admin_collect_respects_disabled…` |
| F12 | Med | ingest/monitor | Silent currency change; watch targets compared across currencies | Fixed | reviewer | `test_f12_*` |
| F13 | Med | deals | Stale data could be "Verified" and in top deals | Fixed | reviewer | `test_f13_*` |
| F14 | Med | deals | One-observation spike "verified" a claimed original price | Fixed | reviewer | `test_f14_*` |
| F15 | Med | performance | `GET /` scored whole catalogue: 7.9 s, 5,002 SQL statements @5,000 listings | Fixed (measured: **0.26 s, 3 statements**) | reviewer; re-measured by me | `test_f15_*` (3) |
| F16 | Med | assistant | Wrong currency/budget/keyword/negation handling gave confident wrong answers | Fixed | reviewer | `tests/test_audit_assistant.py` (23) |
| F17 | Low | assistant | Scraped titles flow verbatim into LLM prompt | Mitigated | reviewer (data flow) | `test_f17_*` (2) |
| F18 | Low | affiliate/ingest | `/go` redirected to any scraped URL incl. `javascript:`; template `{url}` unencoded | Fixed | reviewer | `test_f18_*` (3) |
| F19 | Low | input | `inf`/`1e400` ⇒ HTTP 500 | Fixed | **both** | `test_f19_*` (8) |
| F20 | Low | search | `%`/`_` matched everything; no term/length limits | Fixed | reviewer | `test_f20_*` |
| F21 | Low | specs | Parser dropped RAM/storage when storage listed first | Fixed | reviewer | `test_f21_*` (4) |
| F22 | Low | collector | Concurrency guard per-process only | Mitigated (DB-level guard; tiny start race remains) | reviewer | `test_f22_*` |
| N1 | Med | adapters | One malformed product card (`href="javascript:…"`) raised `IndexError` and discarded the entire page | Fixed (per-card guard, counted as invalid) | regression | `test_f18_ingest_rejects_offsite…` |
| N2 | Med | UI | Disabled "Collect now" looked active; 422 page titled "Something went wrong"; "0 day(s)" wording | Fixed | found by **visual inspection** | screenshots re-checked |
| N3 | Med | security | Short admin tokens allowed | Fixed (≥20 chars) | regression | `test_weak_admin_token_disables_admin` |

## 3. Detail of the most important confirmed defects
**F2 — fake "verified drop" (High).** `watchtower/deals.py` (old lines 83–84, 129): baseline was the median of all earlier observations. Probe: prices 100 (d0) → 200 (d1) → 100 (d8) returned `Verified price drop 80.0 high`. *Why it matters:* this is the exact fake-sale pattern the product claims to detect. *Fix:* a drop counts only if the current price is **below every earlier price**, the "usual" price (the one that held longest) held ≥3 days, ≥3 distinct collections span ≥7 days; claimed original prices are credited only if they equal that observed usual price (±5 %). Time-weighted reference replaces the median.

**F3 — burst observations (High).** `ingest.py`/`deals.py`. Probe: observations at t, t+5 s, t+7 d ⇒ "Verified" from two real collections. *Fix:* one observation per (listing, run); observations <6 h apart collapse to one in scoring.

**F4 — false product merges (High).** `matching.py`. Old output (my run): `auto 0.875 S21 vs S21 Ultra`; `auto 0.856 … vs … Refurbished`; `auto 0.866 Phantom Black vs Phantom White`. Cause: variant words, condition, bundle and colour were never compared; RAM+storage alone satisfied `agree≥2`. *Fix:* hard-conflict vetoes (variant words incl. "+", condition, bundle/pack, colour, generation), suffix/region variants capped at *review*, MPN requires ≥5 chars + same non-empty brand + category check first, GTIN requires ≥8 digits. **Difficult-case results** (all tested): different RAM/storage ⇒ none; different generation (i5-8250U vs 10210U, Gen 2/3, iPhone 13 vs 13 Pro) ⇒ none (already correct); hyphenation/abbreviation differences ⇒ auto only with brand + model code + ≥2 agreeing specs; missing model number ⇒ review at best; region suffix ⇒ review; bundle vs single, refurb vs new, colour ⇒ none. *Known limit:* heuristic word lists (English; no synonym handling like "Midnight" vs "Black"); recall for true matches with unusual titles is lower by design (false merges are the worse error).

**F6/F7/F8 — admin and CSRF (High).** Reproduced: with the old code, `GET /admin` from a "loopback" peer with `X-Forwarded-For: 8.8.8.8` ⇒ 200; cross-origin `POST /watch/1` ⇒ 303. *Fix:* admin is **disabled unless** `WATCHTOWER_ADMIN_TOKEN` (≥20 chars) is set; sign-in sets an `HttpOnly; SameSite=Strict; Path=/admin` cookie holding an HMAC-derived value (never the token); constant-time compare; token accepted via `X-Admin-Token` header for scripts, never via URL; any unsafe-method request whose `Origin`/`Referer` host differs from `Host` (or `Origin: null`) gets 403; rate limits (login 10/min/IP). The public watchlist is now per-browser (random 128-bit cookie, validated), not a shared "local" user.

**F9 — SSRF/politeness (Medium).** Old fetcher followed redirects automatically and trusted adapter-supplied next URLs. Probe fetched `http://169.254.169.254/…` and followed a 302 to `127.0.0.1`. *Fix:* redirects off; each hop is re-validated: http(s) only, per-source host allowlist, private/loopback/link-local/reserved IP literals refused, `robots.txt` per hop (skipped only for authenticated official APIs, where the API agreement governs). *Residual:* a hostname that resolves to a private IP is not detected (no DNS pinning); mitigated because hosts must be on the per-source allowlist.

## 4. Verified-correct areas (no change needed)
`watchtower/` never imports `shopping/` (grep empty; reusable for competitor monitoring: Events, WatchItem, Fetcher, adapters are generic). NULL prices stay NULL through ingest/deals/assistant/charts. 401/403/451 stop without retry; robots 5xx ⇒ disallow-all. Capped/blocked/failed runs never infer removals. Jinja autoescape on; `|safe` only for the numeric SVG chart. No SQL injection found (ORM bound parameters). Pagination limits bounded.

## 5. Open items (NOT fixed) — be skeptical of "secure"
| Risk | Severity | Note |
|---|---|---|
| In-memory rate limiter is per process; behind a proxy requires `WATCHTOWER_TRUST_PROXY=1` | Medium | Use the proxy's limiter in production |
| Single shared admin token; no per-user accounts, MFA, audit log, session revocation | Medium | Fine for one operator only |
| Anonymous watchlist cookie rows are unbounded (no global cap) | Low | DoS by cookie churn possible |
| `style-src 'unsafe-inline'` (inline style attributes) | Low | Scripts are blocked (`script-src 'self'`) |
| No dependency/CVE audit; no HTTPS termination; SQLite file permissions are defaults | Medium | Deployment checklist in README |
| `attach_or_queue` is still a scan of same-category listings per new listing | Low | Fine to ~10⁴ listings |
| Opus-flagged speculative: timing side-channels, LLM behaviour under injection (no model available) | Low | Not exercised |
| Matching/deal thresholds are heuristics tuned on synthetic cases, not on real retailer data | Medium | Re-tune when real data exists |

## 6. Tests
`python -m pytest` → **143 passed** (46 original, 97 new across `test_audit_engine.py`, `test_audit_web.py`, `test_audit_assistant.py`, `test_ebay_adapter.py`, `test_international.py`). Clean-venv install previously reproduced; re-verify before merging.
