from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class RawListing:
    """What an adapter extracts from one product card. Price fields are None when not stated."""
    external_id: str
    url: str
    title: str
    price_minor: int | None
    currency: str
    description: str = ""
    original_price_minor: int | None = None
    availability: str = "unknown"  # in_stock | out_of_stock | unknown
    brand: str | None = None
    category: str | None = None
    image_url: str | None = None
    identifiers: dict = field(default_factory=dict)
    specs: dict = field(default_factory=dict)
    rating: float | None = None
    review_count: int | None = None
    shipping_minor: int | None = None
    condition: str | None = None  # new | used | refurbished
    ships_to: list | None = None
    location_country: str | None = None
    affiliate_url: str | None = None


class SourceAdapter(ABC):
    """One adapter per source. `parse_page` is pure (HTML in, data out) so it is unit-testable
    from saved fixtures; networking/politeness lives in watchtower.fetcher."""
    key: str
    name: str
    base_url: str
    country: str | None = None
    currency: str
    kind: str = "live"  # live | sandbox
    terms_note: str = ""
    stale_after_hours: int = 72
    default_max_pages: int = 5
    parse_errors: list[str] = []  # per-card extraction failures from the LAST parse_page call (card skipped, page kept)

    def allowed_hosts(self) -> set[str]:
        """Hosts the fetcher may contact for this source (initial URLs, next links, redirects)."""
        from urllib.parse import urlparse
        return {urlparse(self.base_url).hostname or ""}

    def listing_hosts(self) -> set[str]:
        """Registrable hosts a listing's public URL may point to (suffix match). Defaults to fetch hosts."""
        return self.allowed_hosts()

    @abstractmethod
    def start_urls(self) -> list[str]: ...

    @abstractmethod
    def parse_page(self, html: str, url: str) -> tuple[list[RawListing], str | None]:
        """Return (listings, next_page_url or None)."""
