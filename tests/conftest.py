from pathlib import Path

import pytest

from watchtower.db import Database
from watchtower.fetcher import FetchError, FetchGone, FetchResult

FIX = Path(__file__).parent / "fixtures"
LAPTOPS = "https://webscraper.io/test-sites/e-commerce/static/computers/laptops"
TABLETS = "https://webscraper.io/test-sites/e-commerce/static/computers/tablets"
PHONES = "https://webscraper.io/test-sites/e-commerce/static/phones/touch"


class FakeFetcher:
    """Serves saved real pages; unknown URLs raise FetchError (like a 404)."""

    def __init__(self, pages: dict[str, str] | None = None):
        self.pages = pages if pages is not None else {
            LAPTOPS: (FIX / "wsio_laptops_p1.html").read_text(),
            LAPTOPS + "?page=2": (FIX / "wsio_laptops_p2.html").read_text(),
            TABLETS: (FIX / "wsio_tablets_p1.html").read_text(),
            "https://books.toscrape.com/catalogue/page-1.html": (FIX / "books_p1.html").read_text(),
        }
        self.requested: list[str] = []

    def get(self, url, headers=None):
        self.requested.append(url)
        if url not in self.pages:
            raise FetchError(f"404 for {url}")
        return self.pages[url]


@pytest.fixture
def db():
    d = Database("sqlite:///:memory:")
    d.init()
    return d


@pytest.fixture
def fake_fetcher():
    return FakeFetcher


REAL = FIX / "real"


class FakeSite(FakeFetcher):
    """Product-page site double: serves pages with ETags, 304 on matching If-None-Match, 404->FetchGone, robots info."""

    def __init__(self, pages=None, gone=(), robots_sha="sha-1", disallow=(), forbid=()):
        super().__init__(pages if pages is not None else {})
        self.gone, self.disallow, self.forbid, self._sha = set(gone), set(disallow), set(forbid), robots_sha
        self.conditional_hits = 0

    def robots_info(self, url):
        return self._sha, None

    def allowed(self, url):
        return url not in self.disallow

    def get_ex(self, url, headers=None, etag=None, last_modified=None):
        from watchtower.fetcher import FetchBlocked
        self.requested.append(url)
        if url in self.forbid:
            raise FetchBlocked(f"403 from {url}")
        if url in self.gone:
            raise FetchGone(f"404 for {url}")
        if url not in self.pages:
            raise FetchError(f"404 for {url}")
        tag = f'"v-{hash(self.pages[url]) & 0xffff}"'
        if etag and etag == tag:
            self.conditional_hits += 1
            return FetchResult(304, None, etag)
        return FetchResult(200, self.pages[url], tag, None, len(self.pages[url]))
