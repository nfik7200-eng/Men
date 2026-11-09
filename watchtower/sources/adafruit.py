import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..structured import SitemapEntry, normalise_url
from .product_page import ProductPageAdapter
from .htmlattr import attr_text

_PRODUCT = re.compile(r"^/product/(\d+)/?$")


class Adafruit(ProductPageAdapter):
    key = "adafruit"
    name = "Adafruit Industries"
    base_url = "https://www.adafruit.com"
    country = "US"
    currency = "USD"
    terms_note = ("US maker-electronics retailer. robots.txt (reviewed 2026-11-06) allows /product/ and /category/ for all agents; "
                  "terms of service contain no clause on automated access found. Prices exclude sales tax.")
    # Categories overlapping other merchants: Raspberry Pi (105), Arduino (17), RP2040/Pico (875). Scope of the crawl.
    categories = (105, 17, 875)

    _CORE = re.compile(r"^(official )?raspberry pi (zero|pico|\d|model|camera)|^arduino|^(official )?raspberry pi$", re.I)
    _BRAND = re.compile(r"raspberry pi|arduino|micro:?bit|pico|rp2040|rp2350", re.I)

    def discover(self, fetcher):
        """Product links from the overlapping category pages, in page order, tiered by the card's own name so that
        boards other merchants also sell are collected first (0 = core boards, 1 = same-brand accessories, 2 = rest)."""
        seen: dict[str, SitemapEntry] = {}
        for cat in self.categories:
            html = fetcher.get(f"{self.base_url}/category/{cat}")
            for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
                path = (attr_text(a["href"]) or "").split("#")[0].split("?")[0]
                path = path.replace(self.base_url, "") if path.startswith(self.base_url) else path
                if not _PRODUCT.match(path):
                    continue
                u = normalise_url(urljoin(self.base_url + "/", path))
                name = (a.get("title") or a.get_text(" ", strip=True) or (a.find("img") or {}).get("alt") or "").strip().rstrip(".")
                tier = 0 if self._CORE.search(name) else (1 if self._BRAND.search(name) else 2)
                cur = seen.get(u)
                if cur is None or (name and tier < cur.priority):
                    seen[u] = SitemapEntry(u, None, tier, name or None)
        return list(seen.values())

    def required_urls(self):
        return [f"{self.base_url}/product/5812", f"{self.base_url}/category/105"]

    def enrich(self, raw, soup, node):
        crumbs = [a.get_text(strip=True) for a in soup.select(".breadcrumbs a, #breadcrumbs a, [class*=readcrumb] a")]
        if crumbs and not raw.specs.get("merchant_category"):
            raw.specs["merchant_category"] = " > ".join(dict.fromkeys(crumbs))[:120]
