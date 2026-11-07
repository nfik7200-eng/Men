import re


from ..structured import parse_sitemap
from .product_page import ProductPageAdapter

_PAGE = re.compile(r"^https://www\.sparkfun\.com/[a-z0-9][a-z0-9\-\.]*\.html$")
# Scope = categories that overlap other merchants (Raspberry Pi, Arduino, micro:bit, RP2040/Pico, ESP32, Teensy)
_SCOPE = re.compile(r"raspberry-pi|arduino|micro-?bit|pico|rp2040|rp2350|esp32|teensy")
_STOCK = [("not in stock", "out_of_stock"), ("out of stock", "out_of_stock"), ("in stock", "in_stock"), ("backorder", "backorder"), ("pre-order", "preorder"),
          ("preorder", "preorder"), ("retired", "out_of_stock"), ("coming soon", "preorder"), ("sold out", "out_of_stock")]


class SparkFun(ProductPageAdapter):
    key = "sparkfun"
    name = "SparkFun Electronics"
    base_url = "https://www.sparkfun.com"
    country = "US"
    currency = "USD"
    check_interval_hours = 48  # Crawl-delay is 10 s: spend the request budget carefully
    default_max_pages = 40
    terms_note = ("US maker-electronics retailer. robots.txt (2026-11-06) allows product pages, Crawl-delay 10 s (honoured); "
                  "terms contain no clause on automated access found. Prices exclude sales tax.")
    sitemap_url = "https://www.sparkfun.com/media/sitemap.xml"

    def discover(self, fetcher):
        _, pages = parse_sitemap(fetcher.get(self.sitemap_url))
        out = [e for e in pages if _PAGE.match(e.loc) and _SCOPE.search(e.loc)]
        for e in out:  # boards other merchants also sell first
            e.priority = self.tier_for_url(e.loc)
        return sorted(out, key=lambda e: (e.priority, e.loc))

    def tier_for_url(self, url: str) -> int:
        slug = url.rsplit("/", 1)[-1]
        return 0 if slug.startswith("raspberry-pi") else (1 if re.search(r"arduino|micro-?bit|pico|rp2040", slug) else 2)

    def required_urls(self):
        return [f"{self.base_url}/raspberry-pi-zero-2-w.html", self.sitemap_url]

    def enrich(self, raw, soup, node):
        # ONLY the main product block: every other div.stock on the page belongs to a related-products card (a real defect
        # found on live data: reading the first div.stock gave a neighbouring product's stock for all 62 listings)
        el = soup.select_one("div.product-info-main div.product-info-stock-sku div.stock")
        txt = (el.get_text(" ", strip=True).lower() if el else "")
        raw.availability = "unknown"  # no main-product stock element found => unknown, never a neighbour's value
        for needle, val in _STOCK:
            if needle in txt:
                raw.availability = val
                break
        if not raw.specs.get("merchant_category"):
            crumbs = [a.get_text(strip=True) for a in soup.select(".breadcrumbs a, nav[aria-label*=readcrumb] a")][1:]
            raw.specs["merchant_category"] = " > ".join(crumbs)[:120]
        import hashlib
        raw.content_hash = hashlib.sha1(f"{raw.price_minor}|{raw.currency}|{raw.availability}|{raw.title}".encode()).hexdigest()
