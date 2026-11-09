"""Regression tests for the third independent review: peers must be buyable, collapse must not chain, robots UA exact, stock text."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from watchtower import deals
from watchtower.collector import ensure_sources
from watchtower.ingest import ingest_listing
from watchtower.models import Source
from watchtower.robots import Robots
from watchtower.sources import RawListing
from watchtower.sources.sparkfun import _STOCK

T0 = datetime(2026, 11, 6, 12, 0)


def _raw(ext, price, avail="in_stock"):
    return RawListing(external_id=ext, url=f"https://x.example/{ext}", title="Raspberry Pi Zero 2 W", price_minor=price, currency="USD",
                      brand="Raspberry Pi", category="maker-electronics", availability=avail)


def _live(s, key):
    src = Source(key=key, name=key, base_url=f"https://{key}.example", currency="USD", kind="live")
    s.add(src)
    s.flush()
    return src


def test_out_of_stock_and_stale_peers_are_not_price_evidence(db):
    ensure_sources(db)
    with db.session() as s:
        a, b, c = _live(s, "shopa"), _live(s, "shopb"), _live(s, "shopc")
        la, *_ = ingest_listing(s, a, None, _raw("a1", 2900), T0)
        ingest_listing(s, b, None, _raw("b1", 100, "out_of_stock"), T0)           # cheap but cannot be bought
        ingest_listing(s, c, None, _raw("c1", 100), T0 - timedelta(days=30))      # cheap but observed a month ago
        s.flush()
        assert deals.PeerContext(s, T0).peers(la)[0] == 0
        ingest_listing(s, c, None, _raw("c1", 2000), T0)  # same listing seen again today
        s.flush()
        assert deals.PeerContext(s, T0).peers(la)[0] == 1


def test_collapse_is_anchored_not_a_sliding_chain():
    mk = lambda h: SimpleNamespace(observed_at=T0 + timedelta(hours=h))
    chain = [mk(h) for h in range(0, 60, 10)]  # each 10h after the previous, 50h in total
    assert len(deals.collapse(chain, gap_hours=24)) == 2  # buckets 0-20h and 30-50h (a sliding window would give 1)


def test_robots_group_needs_the_exact_product_token():
    txt = "User-agent: watchtowerbot-extra\nDisallow: /\n\nUser-agent: *\nDisallow: /private\n"
    ua = "WatchtowerBot/0.1 (contact: ops@watchtower-research.net)"
    r = Robots(txt)
    assert r.can_fetch(ua, "https://x.example/page")  # a different token's group does not apply to us
    assert not r.can_fetch(ua, "https://x.example/private")
    assert not Robots("User-agent: WatchtowerBot\nDisallow: /\n").can_fetch(ua, "https://x.example/page")


def test_not_in_stock_is_never_read_as_in_stock():
    text = "not in stock"
    val = next(v for needle, v in _STOCK if needle in text)
    assert val == "out_of_stock"


def test_assistant_omits_stale_prices_when_fresh_ones_exist_and_normalises_units(db):
    from shopping import assistant
    ensure_sources(db)
    with db.session() as s:
        a, b = _live(s, "shopa"), _live(s, "shopb")
        r1 = RawListing(external_id="a1", url="https://x.example/a1", title="Raspberry Pi 5 8GB", price_minor=8000, currency="USD",
                        brand="Raspberry Pi", category="maker-electronics", availability="in_stock")
        r2 = RawListing(external_id="b1", url="https://x.example/b1", title="Raspberry Pi 5 8GB", price_minor=6000, currency="USD",
                        brand="Raspberry Pi", category="maker-electronics", availability="in_stock")
        ingest_listing(s, a, None, r1, T0)
        ingest_listing(s, b, None, r2, T0 - timedelta(days=30))  # cheaper but a month old
        s.commit()
        for q in ("pi 5 8GB", "pi 5 8 GB"):
            r = assistant.answer(s, q, now=T0, use_llm=False)
            assert [p.listing.source.key for p in r["picks"]] == ["shopa"], q
            assert r["picks"][0].extra == 0, q  # '8 GB' and '8GB' are the same word
            assert any("omitted because their prices are stale" in c for c in r["caveats"])


import pytest
from watchtower.matching import match_score
from watchtower.models import Listing
from watchtower.specs import detect_brand, parse_specs


def _L(title, sid=1):
    return Listing(title=title, source_id=sid, identifiers={}, specs=parse_specs(title, "", "maker-electronics"), category="maker-electronics",
                   brand=detect_brand(title), sku=None, mpn=None, gtin=None)


@pytest.mark.parametrize("board,other", [
    ("Raspberry Pi 5 - 4GB", "Raspberry Pi 5 Camera Cable - 200mm"),
    ("Raspberry Pi 5 - 4GB", "Official Raspberry Pi 5 Active Cooler"),
    ("Raspberry Pi 5 - 4GB", "Raspberry Pi 5 RTC Battery"),
    ("Raspberry Pi 5 - 8GB", "Raspberry Pi Compute Module 5 16GB - 8GB RAM"),
])
def test_accessory_or_part_is_never_a_candidate_for_the_bare_board(board, other):
    assert match_score(_L(board), _L(other, 2)).decision == "none"
