"""Peer selection for the deal score on real retail data (found via the real home page: a USB cable and 9V battery
clips appeared as 'below typical price' because every maker-electronics item was a 'peer' of every other)."""
from datetime import datetime

from watchtower import deals
from watchtower.collector import ensure_sources
from watchtower.ingest import ingest_listing
from watchtower.models import Listing, Source
from watchtower.sources import RawListing

T0 = datetime(2026, 11, 6, 12, 0)


def _raw(ext, title, price, cat="maker-electronics", avail="in_stock"):
    return RawListing(external_id=ext, url=f"https://x.example/{ext}", title=title, price_minor=price, currency="USD", brand="Raspberry Pi",
                      category=cat, availability=avail)


def _live(s, key):
    src = Source(key=key, name=key, base_url=f"https://{key}.example", currency="USD", kind="live")
    s.add(src)
    s.flush()
    return src


def test_broad_category_buckets_give_no_peers_so_a_cheap_cable_is_not_a_deal(db):
    ensure_sources(db)
    with db.session() as s:
        shop = _live(s, "shopa")
        for i in range(12):  # boards at $35-$80 …
            ingest_listing(s, shop, None, _raw(f"b{i}", f"Board Model B{i}00X", 3500 + i * 400), T0)
        cable, *_ = ingest_listing(s, shop, None, _raw("c1", "USB Cable - Standard A-B", 395), T0)  # … and a $3.95 cable
        s.flush()
        ds = deals.score_listing(s, cable, now=T0)
        peer = next(c for c in ds.components if c.name == "peer_value")
        assert peer.points == 0 and peer.evidence == "none" and "no other merchant" in peer.detail
        assert ds.label == "Insufficient data" and ds.confidence == "low"
        assert not [l for l, d in deals.top_deals(s, 20, now=T0) if l.id == cable.id]


def test_same_product_at_another_merchant_is_the_fair_peer(db):
    ensure_sources(db)
    with db.session() as s:
        a, b = _live(s, "shopa"), _live(s, "shopb")
        la, *_ = ingest_listing(s, a, None, _raw("a1", "Raspberry Pi Zero 2 W", 2900), T0)
        lb, *_ = ingest_listing(s, b, None, _raw("b1", "Raspberry Pi Zero 2 W", 1900), T0)
        s.flush()
        assert la.product_id == lb.product_id
        cheap, dear = deals.score_listing(s, lb, now=T0), deals.score_listing(s, la, now=T0)
        pc = next(c for c in cheap.components if c.name == "peer_value")
        assert 0 < pc.points <= 25 and "1 other merchant(s)" in pc.detail and "below" in pc.detail  # one peer: capped
        assert next(c for c in dear.components if c.name == "peer_value").points == 0  # the dearer listing earns nothing
        assert cheap.label == "Below typical price (estimate)" and cheap.confidence == "medium"
        assert "Verified" not in cheap.label  # never verified without history


def test_peers_never_cross_data_classes(db):
    ensure_sources(db)
    with db.session() as s:
        demo = s.query(Source).first()  # kind demo
        live = _live(s, "shopa")
        ingest_listing(s, demo, None, _raw("d1", "Raspberry Pi Zero 2 W", 500), T0)  # absurdly cheap demo twin
        lv, *_ = ingest_listing(s, live, None, _raw("l1", "Raspberry Pi Zero 2 W", 1900), T0)
        s.flush()
        ds = deals.score_listing(s, lv, now=T0)
        peer = next(c for c in ds.components if c.name == "peer_value")
        assert peer.points == 0 and peer.evidence == "none"  # the demo twin is not a peer of a real listing
