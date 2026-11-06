"""Defensive extraction of structured product data (schema.org JSON-LD) and XML sitemaps.

Principles (see docs/SOURCE_DISCOVERY.md): never guess a price. An ambiguous or range price is NULL (unavailable),
never the minimum; a bare "12,50" is rejected unless the merchant is declared decimal-comma; an offer whose
priceValidUntil is in the past is not a current price.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

_TRACKING = re.compile(r"^(utm_|mc_|fbclid|gclid|msclkid|yclid|_ga|ref$|referrer$|source$|sid$|session)", re.I)


# ----------------------------------------------------------------------------- URLs
def normalise_url(url: str) -> str:
    """Canonical form for dedupe: lowercase scheme/host, no fragment, no default port, no tracking params,
    sorted query, collapsed slashes. Path case is preserved (paths are case-sensitive)."""
    p = urlparse(url.strip())
    host = (p.hostname or "").lower()
    port = f":{p.port}" if p.port and not ((p.scheme == "http" and p.port == 80) or (p.scheme == "https" and p.port == 443)) else ""
    q = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k))
    path = re.sub(r"/{2,}", "/", p.path) or "/"
    return urlunparse((p.scheme.lower(), host + port, path, "", urlencode(q), ""))


# ----------------------------------------------------------------------------- sitemaps
@dataclass
class SitemapEntry:
    loc: str
    lastmod: str | None = None
    priority: int = 9  # crawl order hint (lower = earlier); set by adapters that can tell which pages matter most


def parse_sitemap(xml: str) -> tuple[list[str], list[SitemapEntry]]:
    """Returns (child sitemap URLs, page entries). Safe against entity-expansion/XXE and absurd sizes."""
    from lxml import etree
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, recover=True)
    try:
        root = etree.fromstring(xml.encode("utf-8", "ignore"), parser)
    except etree.XMLSyntaxError:
        return [], []
    if root is None:
        return [], []
    strip = lambda t: t.rsplit("}", 1)[-1]
    kids, pages = [], []
    for el in root:
        tag = strip(el.tag)
        loc = lastmod = None
        for ch in el:
            if strip(ch.tag) == "loc" and ch.text:
                loc = ch.text.strip()
            elif strip(ch.tag) == "lastmod" and ch.text:
                lastmod = ch.text.strip()
        if not loc:
            continue
        (kids if tag == "sitemap" else pages).append(loc if tag == "sitemap" else SitemapEntry(loc, lastmod))
    return kids, pages


# ----------------------------------------------------------------------------- prices / availability
def parse_price(value, decimal_comma: bool = False) -> int | None:
    """-> minor units (cents) or None. Rejects non-positive, non-finite and ambiguous values."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        s = repr(value) if isinstance(value, float) else str(value)
    else:
        s = str(value).strip()
    s = re.sub(r"(?i)\b(usd|gbp|eur|us\$)\b|[$£€]", "", s).strip()
    if re.search(r"[A-Za-z]", s):  # anything else alphabetic (e.g. "1e400", "N/A", "from 5") is not a price
        return None
    s = re.sub(r"[^\d.,\-]", "", s)
    if not s or s.startswith("-"):
        return None
    if "," in s and "." in s:
        # whichever separator comes last is the decimal mark
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")  # "1,299" / "12,345,678": thousands separators
        elif re.fullmatch(r"\d+,\d{1,2}", s):
            if not decimal_comma:
                return None  # "12,50": ambiguous without a declared locale — refuse to guess
            s = s.replace(",", ".")
        else:
            return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if not d.is_finite() or d <= 0 or d > Decimal("100000000"):
        return None
    return int((d * 100).to_integral_value())


_AVAIL = {
    "instock": "in_stock", "limitedavailability": "in_stock", "onlineonly": "in_stock", "instoreonly": "in_stock",
    "outofstock": "out_of_stock", "soldout": "out_of_stock", "discontinued": "out_of_stock",
    "backorder": "backorder", "preorder": "preorder", "presale": "preorder",
}


def map_availability(v) -> str:
    if isinstance(v, list):
        v = v[0] if v else None
    key = re.sub(r"[^a-z]", "", str(v or "").split("/")[-1].lower())
    return _AVAIL.get(key, "unknown")


def map_condition(v) -> str | None:
    key = str(v or "").split("/")[-1].lower()
    return {"newcondition": "new", "refurbishedcondition": "refurbished", "usedcondition": "used",
            "damagedcondition": "used"}.get(key)


# ----------------------------------------------------------------------------- JSON-LD
def _loads(text: str):
    text = text.strip()
    for attempt in (text, re.sub(r",\s*([}\]])", r"\1", text), re.sub(r"[\x00-\x1f]", " ", text)):
        try:
            return json.loads(attempt, strict=False)
        except ValueError:
            continue
    return None


def _walk(o, depth=0):
    if depth > 6:
        return
    if isinstance(o, list):
        for x in o:
            yield from _walk(x, depth + 1)
    elif isinstance(o, dict):
        yield o
        for k in ("@graph", "itemListElement", "mainEntity", "hasVariant", "isVariantOf"):
            if k in o:
                yield from _walk(o[k], depth + 1)


def jsonld_nodes(soup: BeautifulSoup) -> list[dict]:
    out = []
    for tag in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        data = _loads(tag.string or tag.get_text() or "")
        if data is not None:
            out.extend(n for n in _walk(data))
    return out


def _is_type(node: dict, *names: str) -> bool:
    t = node.get("@type")
    ts = t if isinstance(t, list) else [t]
    return any(str(x).split("/")[-1] in names for x in ts)


def product_nodes(nodes: list[dict]) -> list[dict]:
    return [n for n in nodes if _is_type(n, "Product", "ProductGroup", "IndividualProduct")]


@dataclass
class OfferInfo:
    price_minor: int | None = None
    currency: str | None = None
    availability: str = "unknown"
    condition: str | None = None
    url: str | None = None
    notes: list[str] = field(default_factory=list)  # why a price was refused, etc.


def offer_from_product(node: dict, now: datetime | None = None, decimal_comma: bool = False) -> OfferInfo:
    """Pick THE offer for a product node. Never returns a range minimum or an arbitrary variant's price."""
    info = OfferInfo()
    raw = node.get("offers")
    offers = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
    flat: list[dict] = []
    for o in offers:
        if not isinstance(o, dict):
            continue
        if _is_type(o, "AggregateOffer"):
            inner = o.get("offers")
            inner = inner if isinstance(inner, list) else ([inner] if isinstance(inner, dict) else [])
            if len(inner) == 1:
                flat.append(inner[0])
            else:
                info.notes.append("AggregateOffer is a price range, not a price")
        else:
            flat.append(o)
    if not flat:
        return info
    parsed = []
    for o in flat:
        spec = o.get("priceSpecification")
        spec = spec[0] if isinstance(spec, list) and spec else spec
        price = o.get("price", (spec or {}).get("price") if isinstance(spec, dict) else None)
        cur = o.get("priceCurrency") or ((spec or {}).get("priceCurrency") if isinstance(spec, dict) else None)
        parsed.append((parse_price(price, decimal_comma), (str(cur).upper() if cur else None), o))
    distinct = {(p, c) for p, c, _ in parsed if p is not None}
    if len(distinct) > 1:
        info.notes.append(f"{len(distinct)} different offers on one page: ambiguous, no price taken")
        return info
    price, cur, o = next(((p, c, o) for p, c, o in parsed if p is not None), (None, parsed[0][1], parsed[0][2]))
    info.price_minor, info.currency = price, cur
    info.availability = map_availability(o.get("availability"))
    info.condition = map_condition(o.get("itemCondition"))
    info.url = o.get("url") if isinstance(o.get("url"), str) else None
    until = o.get("priceValidUntil")
    if until and price is not None and now is not None:
        try:
            if datetime.fromisoformat(str(until)[:10]) < now.replace(hour=0, minute=0, second=0, microsecond=0):
                info.price_minor = None
                info.notes.append(f"offer expired (priceValidUntil {str(until)[:10]})")
        except ValueError:
            pass
    return info


def text_of(v) -> str | None:
    if isinstance(v, dict):
        v = v.get("name")
    return str(v).strip() if v not in (None, "") else None
