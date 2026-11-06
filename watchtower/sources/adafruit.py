import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..structured import SitemapEntry, normalise_url
from .product_page import ProductPageAdapter

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

    def discover(self, fetcher):
        seen: dict[str, SitemapEntry] = {}
        for cat in self.categories:
            html = fetcher.get(f"{self.base_url}/category/{cat}")
            for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
                path = a["href"].split("#")[0].split("?")[0]
                path = path.replace(self.base_url, "") if path.startswith(self.base_url) else path
                if _PRODUCT.match(path):
                    u = normalise_url(urljoin(self.base_url + "/", path))
                    seen.setdefault(u, SitemapEntry(u))
        return sorted(seen.values(), key=lambda e: int(_PRODUCT.match(e.loc[len(self.base_url):]).group(1)))

    def required_urls(self):
        return [f"{self.base_url}/product/5812", f"{self.base_url}/category/105"]

    def enrich(self, raw, soup, node):
        crumbs = [a.get_text(strip=True) for a in soup.select(".breadcrumbs a, #breadcrumbs a, [class*=readcrumb] a")]
        if crumbs and not raw.specs.get("merchant_category"):
            raw.specs["merchant_category"] = " > ".join(dict.fromkeys(crumbs))[:120]
