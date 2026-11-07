"""Provider-agnostic affiliate link building, click attribution and disclosure.

No programme is assumed or enabled by default. Two ways a link becomes affiliate:
  1. the provider returns a tracked URL (e.g. eBay Browse `itemAffiliateWebUrl` when an ePN campaign id is sent) —
     stored on Listing.affiliate_url and used only if it points at the same provider's domains and is http(s);
  2. a URL template in data/affiliate.json:  {"sources": {"<key>": {"network": "x", "template": "{url}?tag=ID&sub={click_id}"}}}
     placeholders: {url} {url_enc} {click_id} {listing_id}.
Every outbound click goes through /go/{id} and is logged (network=None means a plain, non-affiliate link).
Ranking code never imports this module (tested), so commissions cannot influence order.

Disclosure rules actually verified:
  * Amazon Associates Operating Agreement requires, wherever Amazon content/links are shown, the statement
    "As an Amazon Associate I earn from qualifying purchases." (affiliate-program.amazon.com/help/operating/agreement).
  * eBay Partner Network disclosure wording: NOT verified (EPN terms were not readable) — a generic disclosure is used.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

from sqlalchemy.orm import Session

from watchtower import config
from watchtower.models import AffiliateClick, Listing
from watchtower.sources import ADAPTERS

GENERIC_DISCLOSURE = "Some outbound links are affiliate links: we may earn a commission if you buy, at no extra cost to you. This never changes our rankings or prices shown."
AMAZON_DISCLOSURE = "As an Amazon Associate I earn from qualifying purchases."


@dataclass
class OutboundLink:
    url: str
    network: str | None
    is_affiliate: bool


def load_config(path: str | None = None) -> dict:
    p = Path(path or config.AFFILIATE_CONFIG)
    try:
        return json.loads(p.read_text()).get("sources", {}) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def _http(url: str | None) -> bool:
    return bool(url) and urlparse(url).scheme in ("http", "https") and bool(urlparse(url).hostname)


def _provider_url_ok(listing: Listing) -> bool:
    """A provider-issued affiliate URL is trusted only if it stays on the provider's own domains."""
    a = ADAPTERS.get(listing.source.key)
    host = (urlparse(listing.affiliate_url or "").hostname or "").lower()
    return bool(a) and _http(listing.affiliate_url) and any(host == h or host.endswith("." + h) for h in a.listing_hosts())


def build_link(listing: Listing, click_id: int | str = "", cfg: dict | None = None) -> OutboundLink:
    cfg = load_config() if cfg is None else cfg
    rule = cfg.get(listing.source.key)
    if rule and "template" in rule:
        url = rule["template"].format(url=listing.url, url_enc=quote(listing.url, safe=""), click_id=click_id, listing_id=listing.id)
        if _http(url):
            return OutboundLink(url, rule.get("network", "unknown"), True)
    if _provider_url_ok(listing):
        return OutboundLink(listing.affiliate_url, (rule or {}).get("network", listing.source.key + "_affiliate"), True)
    return OutboundLink(listing.url, None, False)


def record_click(s: Session, listing: Listing, cfg: dict | None = None) -> OutboundLink:
    probe = build_link(listing, cfg=cfg)
    click = AffiliateClick(listing_id=listing.id, network=probe.network)
    s.add(click)
    s.flush()
    return build_link(listing, click_id=click.id, cfg=cfg)


def disclosures(networks: set[str]) -> list[str]:
    """Statements to display when these affiliate networks are in use."""
    out = []
    if networks:
        out.append(GENERIC_DISCLOSURE)
    if any("amazon" in n for n in networks):
        out.append(AMAZON_DISCLOSURE)
    return out
