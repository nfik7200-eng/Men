"""eBay Browse API adapter (item_summary/search) — NOT LIVE until credentials + eBay approval exist.

What is VERIFIED from eBay's own Browse API guide (read 2026-11-06, developer.ebay.com/api-docs/buy/static/api-browse.html):
  * GET /buy/browse/v1/item_summary/search with q / category_ids / epid / gtin / filter / limit / sort; max 10,000 results.
  * Affiliate attribution: header  X-EBAY-C-ENDUSERCTX: affiliateCampaignId=<10-digit ePN campaign id>[,affiliateReferenceId=<id>]
    makes the response include `itemAffiliateWebUrl`; `contextualLocation=country%3DUS%2Czip%3D...` improves shipping data.
  * Items whose end date is in the past must not be shown; use getItem (fieldgroups=COMPACT) to refresh price/availability.
  * A sandbox environment exists.
NOT verified (pages returned 403 to us — treat as open questions, see docs/DATA_SOURCE_RESEARCH.md):
  * OAuth endpoint/scope details (implemented from eBay's documented client-credentials flow; confirm against the sandbox),
  * daily call limits, production-keyset approval steps, and the API License Agreement's rules on storing data /
    price history / data products. THE LICENSE MUST BE READ BEFORE ANY PRODUCTION USE.
  * exact ItemSummary field presence — the parser is defensive and skips/flags anything unexpected.

Configuration (environment only; never commit secrets):
  EBAY_CLIENT_ID, EBAY_CLIENT_SECRET   required
  EBAY_ENV                             'sandbox' (default) | 'production'
  EBAY_MARKETPLACE                     EBAY_US (default) | EBAY_GB | ...
  EBAY_CAMPAIGN_ID                     optional ePN campaign id (enables itemAffiliateWebUrl)
  EBAY_QUERIES                         optional JSON: [{"q": "laptop", "category": "laptop", "category_ids": "177"}, ...]
"""
import json
import os
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode

from ..fetcher import FetchBlocked
from ..specs import detect_brand, parse_specs
from .base import RawListing, SourceAdapter

SCOPE = "https://api.ebay.com/oauth/api_scope"
LISTING_DOMAINS = {"ebay.com", "ebay.co.uk", "ebay.de", "ebay.fr", "ebay.it", "ebay.es", "ebay.ca", "ebay.com.au"}
# 9355 (Cell Phones & Smartphones) appears in eBay's own guide; validate others with the Taxonomy API before relying on them.
DEFAULT_QUERIES = [{"q": "laptop", "category": "laptop"}, {"q": "smartphone", "category": "phone", "category_ids": "9355"},
                   {"q": "tablet", "category": "tablet"}]


def _minor(value) -> int | None:
    try:
        v = int((Decimal(str(value)) * 100).to_integral_value())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return v if v > 0 else None


def _condition(text: str | None) -> str | None:
    t = (text or "").lower()
    if not t:
        return None
    if "refurb" in t or "certified" in t or "renewed" in t:
        return "refurbished"
    if "new" in t and "like" not in t and "open" not in t:
        return "new"
    return "used"


class EbayBrowse(SourceAdapter):
    key = "ebay_browse"
    name = "eBay (Browse API)"
    official_api = True
    currency = "USD"  # default only; every item carries its own currency
    stale_after_hours = 24
    default_max_pages = 4
    terms_note = ("Official eBay Browse API. Requires eBay Developer Program keys; use is governed by the eBay API License "
                  "Agreement (NOT yet read — page returned 403 to our fetcher). Rights to store prices/price history "
                  "and to resell data are UNVERIFIED: do not launch publicly before reading it.")

    def __init__(self):
        self._token: str | None = None
        self._expires = 0.0
        self.now = time.monotonic  # injectable for tests

    # ---- configuration (read at call time so env changes/tests apply) ----
    @property
    def env(self) -> str:
        return "production" if os.getenv("EBAY_ENV", "sandbox").lower() == "production" else "sandbox"

    @property
    def kind(self) -> str:  # sandbox data is test data and is flagged as such everywhere in the UI
        return "live" if self.env == "production" else "sandbox"

    @property
    def base_url(self) -> str:
        return "https://api.ebay.com" if self.env == "production" else "https://api.sandbox.ebay.com"

    @property
    def marketplace(self) -> str:
        return os.getenv("EBAY_MARKETPLACE", "EBAY_US")

    @property
    def country(self) -> str:
        return {"EBAY_US": "US", "EBAY_GB": "GB", "EBAY_DE": "DE", "EBAY_CA": "CA", "EBAY_AU": "AU"}.get(self.marketplace, "US")

    def configured(self):
        missing = [v for v in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET") if not os.getenv(v)]
        return (not missing), missing

    def allowed_hosts(self):
        return {"api.ebay.com"} if self.env == "production" else {"api.sandbox.ebay.com"}

    def listing_hosts(self):
        return LISTING_DOMAINS | {"sandbox.ebay.com"}

    def queries(self) -> list[dict]:
        raw = os.getenv("EBAY_QUERIES")
        if raw:
            try:
                q = json.loads(raw)
                return [x for x in q if isinstance(x, dict) and x.get("q")]
            except ValueError:
                pass
        return DEFAULT_QUERIES

    def start_urls(self):
        out = []
        for q in self.queries():
            params = {"q": q["q"], "limit": "50"}
            if q.get("category_ids"):
                params["category_ids"] = q["category_ids"]
            out.append(f"{self.base_url}/buy/browse/v1/item_summary/search?{urlencode(params)}")
        return out

    # ---- auth + headers ----
    def _token_for(self, fetcher) -> str:
        if self._token and self.now() < self._expires - 60:
            return self._token
        ok, missing = self.configured()
        if not ok:
            raise FetchBlocked(f"missing credentials: {', '.join(missing)}")
        data = fetcher.post_form(f"{self.base_url}/identity/v1/oauth2/token",
                                 {"grant_type": "client_credentials", "scope": SCOPE},
                                 auth=(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"]))
        tok = data.get("access_token")
        if not tok:
            raise FetchBlocked("token endpoint returned no access_token")
        self._token, self._expires = tok, self.now() + float(data.get("expires_in", 7200))
        return tok

    def enduser_ctx(self) -> str | None:
        parts = []
        camp = os.getenv("EBAY_CAMPAIGN_ID")
        if camp:
            parts.append(f"affiliateCampaignId={camp}")
        parts.append("contextualLocation=" + quote(f"country={self.country}", safe=""))
        return ",".join(parts)

    def request_headers(self, fetcher) -> dict:
        h = {"Authorization": f"Bearer {self._token_for(fetcher)}", "X-EBAY-C-MARKETPLACE-ID": self.marketplace,
             "Accept": "application/json"}
        ctx = self.enduser_ctx()
        if ctx:
            h["X-EBAY-C-ENDUSERCTX"] = ctx
        return h

    # ---- parsing ----
    def parse_page(self, body, url):
        self.parse_errors = []
        data = json.loads(body)
        out = []
        for it in data.get("itemSummaries") or []:
            try:
                item = self._item(it, url)
            except Exception as e:  # one odd item must not discard the page
                self.parse_errors.append(f"item skipped: {type(e).__name__}: {e}"[:160])
                continue
            if item:
                out.append(item)
        return out, data.get("next") or None

    def _item(self, it: dict, url: str) -> RawListing | None:
        item_id, title, web = it.get("itemId"), (it.get("title") or "").strip(), it.get("itemWebUrl")
        if not item_id or not title or not web:
            raise ValueError("missing itemId/title/itemWebUrl")
        price = it.get("price") or {}
        cur = (price.get("currency") or "").upper()
        if len(cur) != 3:
            raise ValueError("missing price currency")
        minor = _minor(price.get("value"))  # missing/zero/garbled => None (unavailable), never 0
        orig = None
        mp = it.get("marketingPrice") or {}
        if (mp.get("originalPrice") or {}).get("currency", "").upper() == cur:
            orig = _minor(mp["originalPrice"].get("value"))  # seller-CLAIMED reference price, unverified
        ship = None
        for opt in it.get("shippingOptions") or []:
            sc = opt.get("shippingCost") or {}
            if opt.get("shippingCostType") == "FIXED" and (sc.get("currency") or "").upper() == cur and _minor(sc.get("value")) is not None:
                ship = _minor(sc["value"])
                break
        qcat = next((q.get("category") for q in self.queries() if f"q={quote(q['q'])}" in url or f"q={q['q']}" in url), None)
        ids = {}
        if it.get("epid"):
            ids["epid"] = str(it["epid"])
        if it.get("gtin"):
            ids["gtin"] = str(it["gtin"])
        img = (it.get("image") or {}).get("imageUrl")
        return RawListing(
            external_id=str(item_id), url=web, title=title, price_minor=minor, currency=cur,
            description=(it.get("shortDescription") or ""), original_price_minor=orig,
            availability="unknown",  # search summaries do not state stock; refresh via getItem(fieldgroups=COMPACT)
            brand=detect_brand(title), category=qcat, image_url=img, identifiers=ids,
            specs=parse_specs(title, it.get("shortDescription") or "", qcat),
            shipping_minor=ship, condition=_condition(it.get("condition")),
            location_country=((it.get("itemLocation") or {}).get("country") or None),
            affiliate_url=it.get("itemAffiliateWebUrl") or None,
        )
