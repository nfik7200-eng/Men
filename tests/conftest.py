from pathlib import Path

import pytest

from watchtower.db import Database
from watchtower.fetcher import FetchError

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

    def get(self, url):
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
