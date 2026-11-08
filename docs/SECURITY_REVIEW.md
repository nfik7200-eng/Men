# Security review: authentication, sessions, isolation, CSRF, rate limits, API access, database safety

Scope: the web app (`shopping/web`), the notifications engine and CLI, and the admin JSON API, as of commit at HEAD of this
round. Method: code reading of `shopping/web/security.py` and `app.py`, plus regression tests (`tests/test_security_review_round.py`
and the earlier suites). No live system, no live merchant, no real database was touched.

## Verified controls
| Control | How it is enforced | Evidence |
|---|---|---|
| Admin fails closed | no token, or a token under 20 characters, disables admin (403) | `require_admin`; `tests/test_audit_web.py` |
| Admin token comparison | constant time (`hmac.compare_digest`) | `token_ok` |
| Admin session cookie | HMAC-signed over a timestamp, expires, non-ASCII input cannot raise | `cookie_valid` |
| Admin token never in a URL | refused as a query parameter | `tests/test_audit_web.py` |
| Admin JSON for API callers | a JSON 403 without credentials, never a redirect | `tests/test_api_watchlist_review.py` |
| Origin check on every unsafe method (POST, PUT, PATCH, DELETE) | a cross-origin request gets 403, including JSON writes and admin cookies | `tests/test_security_review_round.py`, `tests/test_api_watchlist_review.py` |
| Owner isolation for watchlists and alerts | rows are filtered by owner; another browser's id answers as missing (404 / no effect) | `tests/test_notifications.py`, `tests/test_security_review_round.py` |
| Forged owner cookie | a malformed value is replaced with a fresh, well-formed owner | `tests/test_security_review_round.py` |
| Rate limits | per client: strict 30-per-hour bucket for watch creation (HTML and JSON); 10/min login; 20/min research; 120/min general | `shopping/web/security.py`, `tests/test_security_fixes.py` |
| Security headers | CSP `default-src 'self'`, `script-src 'self'`, frame DENY, nosniff, referrer policy, `no-store` on the sign-in page | `tests/test_security_review_round.py` |
| Database safety | migrations refuse the live file (path and hard link); the notifications table is never created by app start | `tests/test_notifications.py`, `docs/NOTIFICATIONS.md` |
| Input validation | JSON bodies must be objects; ids must be integers; targets are finite and bounded; out-of-range numbers are 422 | `tests/test_api_watchlist_review.py` |
| Errors | JSON error bodies carry only the refusal message | `http_error` handler |

## Findings
1. **No new exploitable defect** was found in this round. Probes for cross-origin writes, owner isolation, forged cookies and admin CSRF all behaved as designed.
2. **Owner identity is a browser cookie.** Anyone holding the cookie controls that browser's watchlist and alerts. This is by design (there are no accounts), but it means watchlists are not private against someone with access to the same browser profile.
3. **Per-client rate limits key on the client address.** Users behind one NAT share a limit. Behind a reverse proxy the address must come from the proxy's trusted header; `WATCHTOWER_TRUST_PROXY` controls this and must be set correctly in deployment.
4. **Content-Security-Policy allows inline styles** (`style-src 'self' 'unsafe-inline'`). Scripts remain same-origin only. Removing inline styles would tighten this; it is recorded, not changed.
5. **Cookie `Secure` flag** depends on HTTPS or `WATCHTOWER_COOKIE_SECURE`. Production must run behind TLS and set it; the deployment environment file documents this.
6. **Not reviewed in this round:** TLS termination configuration in nginx (no VPS access, untested runtime), dependency vulnerability scanning (no network audit run), and brute-force resistance of the admin token beyond the login rate limit.

## Recommended before any public exposure
- Confirm `WATCHTOWER_TRUST_PROXY` and `WATCHTOWER_COOKIE_SECURE` in the real environment file (deployment step, owner approval).
- Run a dependency audit (`pip-audit` against the lock file) on a machine with network access.
- Test the nginx configuration on the real host.
