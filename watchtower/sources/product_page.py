"""Base class for merchants that expose one product per page with schema.org JSON-LD and a sitemap/category index.

HTTP-first (no browser). Subclasses declare discovery (`discover`) and small site-specific enrichment (`enrich`).
We deliberately do NOT store product descriptions or images (copyright; see docs/SOURCE_DISCOVERY.md): only
facts (name, price, currency, availability, identifiers) plus a hash of the description for change detection.
"""
import hashlib
from datetime import datetime

from bs4 import BeautifulSoup

from ..identifiers import clean_mpn, normalise_gtin
from ..specs import detect_brand, parse_specs
from ..structured import (SitemapEntry, jsonld_nodes, normalise_url, offer_from_product, product_nodes, text_of)
from .base import RawListing, SourceAdapter

CANONICAL_CATEGORY = "maker-electronics"


class ProductPageAdapter(SourceAdapter):
    page_per_product = True
    kind = "live"
    decimal_comma = False
    check_interval_hours = 24
    stale_after_hours = 96
    default_max_pages = 60  # product pages fetched per run (discovery pages not counted)

    # -- to be provided by subclasses
    def discover(self, fetcher) -> list[SitemapEntry]:
        raise NotImplementedError

    def required_urls(self) -> list[str]:
        """Sample URLs of every path class we fetch; robots.txt is re-checked against these on every run."""
        raise NotImplementedError

    def enrich(self, raw: RawListing, soup: BeautifulSoup, node: dict) -> None:  # optional hook
        pass

    # -- the SourceAdapter contract (page-per-product sources use parse_product instead of listing pages)
    def start_urls(self):
        return []

    def parse_page(self, html, url):  # pragma: no cover - not used for product-page sources
        raise NotImplementedError

    def parse_product(self, html: str, url: str, now: datetime | None = None) -> RawListing | None:
        """Returns None when the page has no Product data (e.g. a category page). Never raises on odd markup."""
        soup = BeautifulSoup(html, "lxml")
        prods = product_nodes(jsonld_nodes(soup))
        if not prods:
            return None
        node = prods[0]
        title = text_of(node.get("name")) or text_of((soup.find("meta", property="og:title") or {}).get("content"))
        if not title:
            return None
        offer = offer_from_product(node, now, self.decimal_comma)
        sku = text_of(node.get("sku")) or text_of(node.get("productID"))
        gtin = next((g for k in ("gtin", "gtin14", "gtin13", "gtin12", "gtin8") if (g := normalise_gtin(node.get(k)))), None)
        mpn = clean_mpn(node.get("mpn"), sku)
        clean = normalise_url(url)
        price, cur = offer.price_minor, offer.currency
        if cur is None:  # currency missing: we do not guess it onto a price
            price = None
        meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
        desc_src = (meta_desc.get("content") if meta_desc else "") or ""
        raw = RawListing(
            external_id=sku or clean.rstrip("/").rsplit("/", 1)[-1], url=clean, title=title,
            price_minor=price, currency=(cur or self.currency), description="",
            availability=offer.availability, brand=text_of(node.get("brand")) or detect_brand(title),
            category=CANONICAL_CATEGORY, specs=parse_specs(title, "", None), sku=sku, mpn=mpn, gtin=gtin,
            condition=offer.condition,
            desc_hash=hashlib.sha1(desc_src.lower().encode()).hexdigest() if desc_src else None,
            extraction_method="jsonld", http_status=200,
        )
        raw.specs["merchant_category"] = text_of(node.get("category")) or ""
        if offer.notes:
            raw.specs["extraction_notes"] = "; ".join(offer.notes)
        raw.content_hash = hashlib.sha1(f"{raw.price_minor}|{raw.currency}|{raw.availability}|{title}".encode()).hexdigest()
        self.enrich(raw, soup, node)
        return raw
