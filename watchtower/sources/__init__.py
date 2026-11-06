from .base import RawListing, SourceAdapter
from .books_toscrape import BooksToScrape
from .ebay_browse import EbayBrowse
from .webscraper_io import WebscraperIoStatic

ADAPTERS: dict[str, SourceAdapter] = {a.key: a for a in (WebscraperIoStatic(), BooksToScrape(), EbayBrowse())}

__all__ = ["ADAPTERS", "RawListing", "SourceAdapter"]
