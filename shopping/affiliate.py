"""Provider-agnostic affiliate link building + click attribution.

No programme is assumed. With no config, links are plain outbound links (network=None) and no
affiliate disclosure is shown. A config file maps a source key to a URL template, e.g.
  {"sources": {"ebay_us": {"network": "ebay_epn", "template": "{url}?campid=YOURID&customid={click_id}"}}}
Templates are filled with {url}, {url_enc}, {click_id}, {listing_id}. Verify each programme's linking rules first.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from sqlalchemy.orm import Session

from watchtower import config
from watchtower.models import AffiliateClick, Listing


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


def build_link(listing: Listing, click_id: int | str = "", cfg: dict | None = None) -> OutboundLink:
    cfg = load_config() if cfg is None else cfg
    rule = cfg.get(listing.source.key)
    if not rule or "template" not in rule:
        return OutboundLink(listing.url, None, False)
    url = rule["template"].format(url=listing.url, url_enc=quote(listing.url, safe=""),
                                  click_id=click_id, listing_id=listing.id)
    return OutboundLink(url, rule.get("network", "unknown"), True)


def record_click(s: Session, listing: Listing, cfg: dict | None = None) -> OutboundLink:
    probe = build_link(listing, cfg=cfg)
    click = AffiliateClick(listing_id=listing.id, network=probe.network)
    s.add(click)
    s.flush()
    return build_link(listing, click_id=click.id, cfg=cfg)
