"""Regression tests for the second independent (Opus) review of the matcher: false merges reproduced on real-style
maker-electronics titles, structural guards, and integrity bugs (FK on accept, rejected pairs re-queued)."""
from datetime import datetime

import pytest
from sqlalchemy import select

from watchtower.collector import ensure_sources
from watchtower.ingest import ingest_listing
from watchtower.matching import match_score, rematch_all, resolve_candidate
from watchtower.models import Listing, MatchCandidate, Product, Source
from watchtower.sources import RawListing
from watchtower.specs import detect_brand, parse_specs

T0 = datetime(2026, 11, 6, 12, 0)


def L(title, sid=1, brand="auto"):
    return Listing(title=title, source_id=sid, identifiers={}, specs=parse_specs(title, "", None), category="maker-electronics",
                   brand=detect_brand(title) if brand == "auto" else brand, sku=None, mpn=None, gtin=None)


@pytest.mark.parametrize("a,b", [
    ("Raspberry Pi SD Card 32GB", "Raspberry Pi SD Card 64GB"),
    ("Raspberry Pi SD Card 32GB", "Raspberry Pi SD Card 128GB"),
    ("Raspberry Pi Official 32GB microSD Card with Raspberry Pi OS", "Raspberry Pi Official 64GB microSD Card with Raspberry Pi OS"),
    ("Raspberry Pi 5.1V 3A USB-C Power Supply", "Raspberry Pi 5.1V 5A USB-C Power Supply"),
    ("Official Raspberry Pi Power Supply 5V 3A", "Official Raspberry Pi Power Supply 5V 2.5A"),
    ("Raspberry Pi NoIR Camera 1.3", "Raspberry Pi NoIR Camera 2.1"),
    ("BBC micro:bit Go 1.5", "BBC micro:bit Go 2.0"),
    ("Raspberry Pi Compute Module 4 CM4 4GB RAM 32GB eMMC WiFi", "Raspberry Pi Compute Module 4 CM4 4GB RAM 32GB eMMC"),
    ("Raspberry Pi CM4 2GB RAM 16GB eMMC Wireless", "Raspberry Pi CM4 2GB RAM 16GB eMMC No Wireless"),
    ("Raspberry Pi CM5 Wireless", "Raspberry Pi CM5"),
    ("Case for Raspberry Pi 4", "Raspberry Pi 4 Case"),
    ("Power Supply for Raspberry Pi 5", "Official Raspberry Pi 5 Power Supply"),
])
def test_review2_false_merges_are_blocked(a, b):
    assert match_score(L(a), L(b, 2)).decision != "auto", (a, b)


@pytest.mark.parametrize("a,b", [
    ("Raspberry Pi 5 - 8 GB RAM", "Raspberry Pi 5 8GB"), ("Raspberry Pi 5 - 4 GB RAM", "Raspberry Pi 5 (4GB)"),
    ("Raspberry Pi Zero 2 W", "Raspberry Pi Zero 2W"), ("Arduino Uno R3", "Arduino Uno Rev3"),
    ("Raspberry Pi 4 Model B - 2 GB RAM", "Raspberry Pi 4 Model B (2 GB)"), ("Raspberry Pi Pico W", "Raspberry Pi PicoW"),
])
def test_review2_same_product_pairs_still_match(a, b):
    assert match_score(L(a), L(b, 2)).decision == "auto", (a, b)


def test_review2_ambiguous_one_word_names_and_missing_brand_go_to_review_not_none_or_auto():
    assert match_score(L("Raspberry Pi Pico"), L("Raspberry Pi Pico Module", 2)).decision == "review"
    assert match_score(L("Raspberry Pi 5"), L("Raspberry Pi 5", 2)).decision == "review"  # RAM unknown on both: could be any Pi 5 variant
    assert match_score(L("Raspberry Pi 5 8GB"), L("Raspberry Pi 5 8GB", 2)).decision == "auto"
    r = match_score(L("Zero 2 W Board Kit Thing", brand=None), L("Zero 2 W Board Kit Thing", 2, brand=None))
    assert r.decision in ("review", "none")  # never auto without a known brand
    assert match_score(L("Acme Gizmo Mk2", brand=None), L("Acme Gizmo Mk2", 2, brand="Acme")).decision != "auto"


def test_review2_third_party_accessories_are_not_branded_as_the_platform():
    assert detect_brand("Case for Raspberry Pi 4") is None and detect_brand("Heatsink compatible with Raspberry Pi 5") is None
    assert detect_brand("Raspberry Pi 4 Case") == "Raspberry Pi" and detect_brand("Adafruit Case for Raspberry Pi 4") == "Adafruit"


def _raw(ext, title, price=500):
    return RawListing(external_id=ext, url=f"https://x.example/{ext}", title=title, price_minor=price, currency="USD",
                      brand=detect_brand(title), category="maker-electronics", availability="in_stock")


def _srcs(s):
    a, b, c, *_ = s.query(Source).order_by(Source.id).all()
    return a, b, c


def test_review2_no_transitive_drift_new_listing_must_not_conflict_with_any_member(db):
    ensure_sources(db)
    with db.session() as s:
        s1, s2, s3 = _srcs(s)
        a, *_ = ingest_listing(s, s1, None, _raw("a", "Raspberry Pi Zero 2 W"), T0)
        b, *_ = ingest_listing(s, s2, None, _raw("b", "Raspberry Pi Zero 2 W"), T0)
        assert b.product_id == a.product_id
        c, *_ = ingest_listing(s, s3, None, _raw("c", "Raspberry Pi Zero 2 W Wireless Headers"), T0)  # conflicts (variant) with members
        assert c.product_id != a.product_id and c.match_status in ("self", "review")


def test_review2_accepting_a_candidate_does_not_break_other_pending_candidates(db):
    ensure_sources(db)
    with db.session() as s:
        s1, s2, s3 = _srcs(s)
        l, *_ = ingest_listing(s, s1, None, _raw("l", "Mystery Widget Alpha"), T0)
        p1, *_ = ingest_listing(s, s2, None, _raw("p1", "Mystery Widget Beta"), T0)
        p2, *_ = ingest_listing(s, s3, None, _raw("p2", "Mystery Widget Gamma"), T0)
        s.flush()
        c1 = MatchCandidate(listing_id=l.id, product_id=p1.product_id, score=0.6, reasons=["t"])
        c2 = MatchCandidate(listing_id=l.id, product_id=p2.product_id, score=0.6, reasons=["t"])
        c3 = MatchCandidate(listing_id=p1.id, product_id=l.product_id, score=0.6, reasons=["mirror"])  # points at l's own product
        s.add_all([c1, c2, c3]); s.flush()
        old = l.product_id
        resolve_candidate(s, c1.id, True)  # l's own product becomes empty and is deleted: c3 pointed at it
        s.commit()                           # PRAGMA foreign_keys=ON: used to raise IntegrityError
        assert l.product_id == p1.product_id and l.match_status == "confirmed" and s.get(Product, old) is None
        assert s.query(MatchCandidate).filter_by(product_id=old).count() == 0


def test_review2_rematch_all_never_requeues_a_human_rejection(db):
    ensure_sources(db)
    with db.session() as s:
        s1, s2, _ = _srcs(s)
        a, *_ = ingest_listing(s, s1, None, _raw("a", "Raspberry Pi Pico"), T0)
        b, *_ = ingest_listing(s, s2, None, _raw("b", "Raspberry Pi Pico Module"), T0)  # review pair
        s.flush()
        cand = s.query(MatchCandidate).filter_by(listing_id=b.id).one()
        resolve_candidate(s, cand.id, False)
        s.flush()
        assert b.match_status == "self"
        before = s.query(MatchCandidate).count()
        out = rematch_all(s)
        s.flush()
        assert b.match_status == "self" and a.match_status == "self" and s.query(MatchCandidate).count() == before
        assert s.query(MatchCandidate).filter_by(status="pending").count() == 0, out
