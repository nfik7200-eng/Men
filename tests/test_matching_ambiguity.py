"""Real data showed one merchant (Adafruit) listing several DIFFERENT products under one identical title
(e.g. 'Raspberry Pi 5 FPC Camera Cable - 22-pin 0.5mm to 15-pin 1mm' at $2.50, $2.70, $3.16 — different lengths).
A title cannot tell them apart, so the matcher must never auto-merge a title-only match in these situations."""
from datetime import datetime

from sqlalchemy import select

from watchtower.collector import ensure_sources
from watchtower.ingest import ingest_listing
from watchtower.matching import demote_ambiguous
from watchtower.models import Listing, MatchCandidate, Product, Source
from watchtower.sources import RawListing

T0 = datetime(2026, 11, 6, 12, 0)
TITLE = "Raspberry Pi 5 FPC Camera Cable - 22-pin 0.5mm to 15-pin 1mm"


def _raw(ext, price, title=TITLE):
    return RawListing(external_id=ext, url=f"https://x.example/{ext}", title=title, price_minor=price, currency="USD",
                      brand="Raspberry Pi", category="maker-electronics", availability="in_stock")


def _two_sources(s):
    a, b, *_ = s.query(Source).order_by(Source.id).all()
    return a, b


def test_a_product_never_holds_two_listings_from_the_same_merchant(db):
    ensure_sources(db)
    with db.session() as s:
        shop1, shop2 = _two_sources(s)
        other, *_ = ingest_listing(s, shop2, None, _raw("o1", 300), T0)            # the other merchant: one item, same title
        v1, *_ = ingest_listing(s, shop1, None, _raw("v1", 250), T0)               # first variant merges (title-only, unique so far)
        v2, *_ = ingest_listing(s, shop1, None, _raw("v2", 270), T0)               # identical title again: must NOT join the same product
        s.flush()
        pids = [x.product_id for x in (other, v1, v2)]
        assert v2.product_id not in (other.product_id, v1.product_id)
        assert v2.match_status == "review" and any("already has a listing from this merchant" in r for r in v2.match_reasons)
        by_source = {}
        for l in s.scalars(select(Listing)):
            by_source.setdefault((l.product_id, l.source_id), []).append(l.id)
        assert all(len(v) == 1 for v in by_source.values())  # invariant: <=1 listing per (product, merchant)


def test_ties_between_equally_good_candidates_go_to_review_not_auto(db):
    ensure_sources(db)
    with db.session() as s:
        shop1, shop2 = _two_sources(s)
        v1, *_ = ingest_listing(s, shop1, None, _raw("v1", 250), T0)
        v2, *_ = ingest_listing(s, shop1, None, _raw("v2", 270), T0)
        v3, *_ = ingest_listing(s, shop1, None, _raw("v3", 316), T0)
        new, *_ = ingest_listing(s, shop2, None, _raw("o1", 300), T0)  # matches all three variants equally well
        s.flush()
        assert new.match_status == "review" and new.product_id not in (v1.product_id, v2.product_id, v3.product_id)
        assert any("ambiguous" in r for r in new.match_reasons)
        assert s.query(MatchCandidate).filter_by(listing_id=new.id).count() == 3  # a human sees every candidate


def test_demote_ambiguous_unmerges_when_a_merchant_turns_out_to_have_identical_names(db):
    ensure_sources(db)
    with db.session() as s:
        shop1, shop2 = _two_sources(s)
        other, *_ = ingest_listing(s, shop2, None, _raw("o1", 300), T0)
        v1, *_ = ingest_listing(s, shop1, None, _raw("v1", 250), T0)
        assert v1.match_status == "auto" and v1.product_id == other.product_id  # unique at that time
        ingest_listing(s, shop1, None, _raw("v2", 270), T0)                       # the sibling appears later
        n = demote_ambiguous(s, shop1.id)
        s.flush()
        assert n == 1 and v1.match_status == "review" and v1.product_id != other.product_id
        assert any("identically named" in r for r in v1.match_reasons)
        assert s.query(MatchCandidate).filter_by(listing_id=v1.id, product_id=other.product_id, status="pending").count() == 1
        assert s.get(Product, other.product_id) is not None and s.query(Listing).filter_by(product_id=other.product_id).count() == 1


def test_distinct_names_still_auto_merge_normally(db):
    ensure_sources(db)
    with db.session() as s:
        shop1, shop2 = _two_sources(s)
        a, *_ = ingest_listing(s, shop1, None, _raw("a", 1905, "Raspberry Pi Zero 2 W"), T0)
        b, *_ = ingest_listing(s, shop2, None, _raw("b", 1900, "Raspberry Pi Zero 2 W"), T0)
        s.flush()
        assert b.match_status == "auto" and a.product_id == b.product_id
        assert demote_ambiguous(s, shop1.id) == 0 and demote_ambiguous(s, shop2.id) == 0  # unique names are left alone


def test_items_distinguished_by_capacity_are_not_ambiguous_but_identical_titles_are(db):
    """Real data: Adafruit lists 'Raspberry Pi 4 Model B - 2 GB / 4 GB / 8 GB RAM'. Their name signatures are identical
    (GB tokens are stripped) but they are DIFFERENT items; only truly identical titles are ambiguous."""
    ensure_sources(db)
    with db.session() as s:
        shop1, shop2 = _two_sources(s)
        o2, *_ = ingest_listing(s, shop2, None, _raw("o2", 6795, "Raspberry Pi 4 Model B (2 GB)"), T0)
        o4, *_ = ingest_listing(s, shop2, None, _raw("o4", 12000, "Raspberry Pi 4 Model B (4 GB)"), T0)
        a2, *_ = ingest_listing(s, shop1, None, _raw("a2", 8000, "Raspberry Pi 4 Model B - 2 GB RAM"), T0)
        a4, *_ = ingest_listing(s, shop1, None, _raw("a4", 12000, "Raspberry Pi 4 Model B - 4 GB RAM"), T0)
        s.flush()
        assert a2.product_id == o2.product_id and a4.product_id == o4.product_id and a2.product_id != a4.product_id
        assert demote_ambiguous(s, shop1.id) == 0 and demote_ambiguous(s, shop2.id) == 0
        assert a2.match_status == "auto" and a4.match_status == "auto"
