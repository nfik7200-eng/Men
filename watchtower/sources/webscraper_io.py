from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..money import parse_money
from ..specs import detect_brand, parse_specs
from .base import RawListing, SourceAdapter

ROOT = "https://webscraper.io/test-sites/e-commerce/static"
# Only paths NOT disallowed by robots.txt (/test-sites/product/, /pagination/, /scroll/, /load-more/).
CATEGORIES = {"laptop": "computers/laptops", "tablet": "computers/tablets", "phone": "phones/touch"}


class WebscraperIoStatic(SourceAdapter):
    key = "webscraper_io_static"
    name = "Web Scraper test site (e-commerce, static)"
    base_url = "https://webscraper.io/test-sites/e-commerce/static"
    country = "US"
    currency = "USD"
    kind = "sandbox"
    terms_note = ("Public scraping-practice site. robots.txt checked 2026-11-05: only /test-sites/product/, "
                  "/pagination/, /scroll/, /load-more/ disallowed (not used). DEMO catalogue, not a real shop.")
    stale_after_hours = 72
    default_max_pages = 3

    def start_urls(self):
        return [f"{ROOT}/{p}" for p in CATEGORIES.values()]

    def parse_page(self, html, url):
        soup = BeautifulSoup(html, "lxml")
        category = next((c for c, p in CATEGORIES.items() if p in url), None)
        out = []
        for card in soup.select("div.card.thumbnail"):
            a = card.select_one("a.title")
            if not a or not a.get("href"):
                continue
            title = (a.get("title") or a.get_text()).strip()
            href = urljoin(url, a["href"])
            ext_id = href.rstrip("/").rsplit("/", 1)[-1]
            price_el = card.select_one('[itemprop="price"]')
            cur_el = card.select_one('[itemprop="priceCurrency"]')
            parsed = parse_money(price_el.get_text() if price_el else None,
                                 cur_el.get("content") if cur_el else self.currency)
            desc_el = card.select_one("p.description")
            desc = desc_el.get_text(" ", strip=True) if desc_el else ""
            img = card.select_one("img")
            rc = card.select_one('[itemprop="reviewCount"]')
            rating_el = card.select_one("p[data-rating]")
            out.append(RawListing(
                external_id=ext_id, url=href, title=title,
                price_minor=parsed[0] if parsed else None,
                currency=parsed[1] if parsed else self.currency,
                description=desc,
                availability="unknown",  # card does not state stock; do NOT assume in stock
                brand=detect_brand(title), category=category,
                image_url=urljoin(url, img["src"]) if img and img.get("src") else None,
                specs=parse_specs(title, desc, category),
                rating=float(rating_el["data-rating"]) if rating_el else None,
                review_count=int(rc.get_text(strip=True)) if rc and rc.get_text(strip=True).isdigit() else None,
            ))
        nxt = soup.select_one("ul.pagination a[rel=next]")
        return out, (urljoin(url, nxt["href"]) if nxt and nxt.get("href") else None)
