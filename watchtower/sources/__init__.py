from .adafruit import Adafruit
from .base import RawListing, SourceAdapter
from .books_toscrape import BooksToScrape
from .ebay_browse import EbayBrowse
from .sparkfun import SparkFun
from .webscraper_io import WebscraperIoStatic

ADAPTERS: dict[str, SourceAdapter] = {a.key: a for a in (WebscraperIoStatic(), BooksToScrape(), EbayBrowse(), Adafruit(), SparkFun())}

__all__ = ["ADAPTERS", "RawListing", "SourceAdapter"]
