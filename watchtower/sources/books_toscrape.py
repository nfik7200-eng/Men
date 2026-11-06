import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..money import parse_money
from .base import RawListing, SourceAdapter

_STARS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


class BooksToScrape(SourceAdapter):
    key = "books_toscrape"
    name = "Books to Scrape (demo bookstore)"
    base_url = "https://books.toscrape.com"
    country = "GB"
    currency = "GBP"
    kind = "sandbox"
    terms_note = ("Public scraping-practice site; no robots.txt (404) on 2026-11-05 => no published restrictions. "
                  "DEMO catalogue, not a real shop.")
    stale_after_hours = 72
    default_max_pages = 3

    def start_urls(self):
        return ["https://books.toscrape.com/catalogue/page-1.html"]

    def parse_page(self, html, url):
        soup = BeautifulSoup(html, "lxml")
        out, self.parse_errors = [], []
        for art in soup.select("article.product_pod"):
            try:
                item = self._card(art, url)
            except Exception as e:  # one malformed card must not discard the rest of the page
                self.parse_errors.append(f"card skipped: {type(e).__name__}: {e}"[:160])
                continue
            if item:
                out.append(item)
        nxt = soup.select_one("li.next a")
        return out, (urljoin(url, nxt["href"]) if nxt else None)

    def _card(self, art, url):
        a = art.select_one("h3 a")
        if not a:
            return None
        href = urljoin(url, a["href"])
        m = re.search(r"/([^/]+)/index\.html$", href)
        if not m:
            raise ValueError(f"unrecognised product link {href[:60]!r}")
        price_el = art.select_one("p.price_color")
        price = parse_money(price_el.get_text() if price_el else None, self.currency)
        av = art.select_one("p.availability")
        avail_txt = av.get_text(" ", strip=True).lower() if av else ""
        availability = "in_stock" if "in stock" in avail_txt else ("out_of_stock" if avail_txt else "unknown")
        star = art.select_one("p.star-rating")
        rating = next((_STARS[c] for c in (star.get("class") or []) if c in _STARS), None) if star else None
        img = art.select_one("img")
        return RawListing(
            external_id=m.group(1), url=href, title=(a.get("title") or a.get_text()).strip(),
            price_minor=price[0] if price else None, currency=price[1] if price else self.currency,
            availability=availability, category="book",
            image_url=urljoin(url, img["src"]) if img and img.get("src") else None,
            rating=float(rating) if rating else None,
        )
