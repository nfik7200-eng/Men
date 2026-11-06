"""Loop E, iteration 4: unit spelling must not decide relevance.

Found on the real data (mission 004): for "raspberry pi 5 8gb" the assistant returned SparkFun's 'Raspberry Pi 5 - 8GB'
plus two Compute Modules, and dropped Adafruit's 'Raspberry Pi 5 - 8 GB RAM' — the SAME matched product — because the
query word '8gb' was required literally and Adafruit writes '8 GB'."""
from datetime import datetime

import pytest

from shopping import assistant
from watchtower.collector import ensure_sources
from watchtower.ingest import ingest_listing
from watchtower.models import Source
from watchtower.sources import RawListing

T0 = datetime(2026, 11, 6, 12, 0)


def _raw(ext, title, price):
    return RawListing(external_id=ext, url=f"https://x.example/{ext}", title=title, price_minor=price, currency="USD",
                      brand="Raspberry Pi", category="maker-electronics", availability="in_stock")


@pytest.mark.parametrize("q", ["raspberry pi 5 8gb", "raspberry pi 5 8 GB", "Raspberry Pi 5 8GB"])
def test_same_product_spelled_8_gb_and_8gb_is_relevant_and_outranks_other_products(db, q):
    ensure_sources(db)
    with db.session() as s:
        a = Source(key="ada", name="Ada", base_url="https://ada.example", currency="USD", kind="live")
        b = Source(key="spk", name="Spk", base_url="https://spk.example", currency="USD", kind="live")
        s.add_all([a, b])
        s.flush()
        ada, *_ = ingest_listing(s, a, None, _raw("a1", "Raspberry Pi 5 - 8 GB RAM", 20000), T0)
        spk, *_ = ingest_listing(s, b, None, _raw("b1", "Raspberry Pi 5 - 8GB", 18000), T0)
        ingest_listing(s, b, None, _raw("b2", "Raspberry Pi Compute Module 5 32GB - 8GB RAM", 15000), T0)
        ingest_listing(s, a, None, _raw("a2", "Raspberry Pi 5 - 4 GB RAM", 13000), T0)
        s.flush()
        assert ada.product_id == spk.product_id  # the matcher already knows they are one product
        r = assistant.answer(s, q, now=T0, use_llm=False)
        titles = [p.listing.title for p in r["picks"]]
        assert titles[:2] == ["Raspberry Pi 5 - 8GB", "Raspberry Pi 5 - 8 GB RAM"], titles  # both merchants, cheapest first
        assert "Raspberry Pi 5 - 4 GB RAM" not in titles  # a different RAM size is not what was asked


def test_unit_tokens_match_whole_values_only():
    assert assistant.has_word("8gb", assistant.norm_units("Raspberry Pi 5 - 8 GB RAM"))
    assert not assistant.has_word("8gb", assistant.norm_units("Raspberry Pi SD Card 128GB"))
    assert not assistant.has_word("8gb", assistant.norm_units("Pi 5 - 2.8GB"))  # contrived, but '2.8gb' is not '8gb'
    assert assistant.has_word("cable", "usb cables")


def _two_shops(s):
    a = Source(key="ada", name="Ada", base_url="https://ada.example", currency="USD", kind="live")
    b = Source(key="spk", name="Spk", base_url="https://spk.example", currency="USD", kind="live")
    s.add_all([a, b])
    s.flush()
    return a, b


def test_out_of_stock_exact_product_is_not_displaced_by_an_in_stock_different_product(db):
    """Real data (mission 004): 'pi 5 2gb' answered with two Compute Modules; the Pi 5 2GB boards (out of stock at both
    merchants) were not shown at all."""
    ensure_sources(db)
    with db.session() as s:
        a, b = _two_shops(s)
        oos = lambda ext, t, p: RawListing(**{**_raw(ext, t, p).__dict__, "availability": "out_of_stock"})
        ingest_listing(s, a, None, oos("a1", "Raspberry Pi 5 - 2 GB RAM", 9000), T0)
        ingest_listing(s, b, None, oos("b1", "Raspberry Pi 5 - 2GB", 8000), T0)
        ingest_listing(s, b, None, _raw("b2", "Raspberry Pi Compute Module 5 Lite - 2GB RAM", 7000), T0)
        s.flush()
        r = assistant.answer(s, "pi 5 2gb", now=T0, use_llm=False)
        titles = [p.listing.title for p in r["picks"]]
        assert set(titles) == {"Raspberry Pi 5 - 2GB", "Raspberry Pi 5 - 2 GB RAM"}, titles
        assert any("none is in stock" in c for c in r["caveats"])  # shown honestly, flagged
        assert all(any("stock" in m for m in p.misses) for p in r["picks"])


def test_named_model_ranks_the_board_above_a_cheaper_compute_module(db):
    ensure_sources(db)
    with db.session() as s:
        a, b = _two_shops(s)
        ingest_listing(s, a, None, _raw("a1", "Raspberry Pi 4 Model B - 4 GB RAM", 12000), T0)
        ingest_listing(s, b, None, _raw("b1", "Raspberry Pi Compute Module 4 16GB (Wireless Version) - 4GB RAM", 9500), T0)
        s.flush()
        r = assistant.answer(s, "raspberry pi 4 4gb", now=T0, use_llm=False)
        assert r["picks"][0].listing.title == "Raspberry Pi 4 Model B - 4 GB RAM"
        r = assistant.answer(s, "raspberry pi compute module 4 4gb", now=T0, use_llm=False)  # asked for: then it is the answer
        assert r["picks"][0].listing.title.startswith("Raspberry Pi Compute Module 4")


# --- independent review of iterations 4 and 6 (each case failed before the fix) ---------------------------------
def _cat(s, src, ext, title, price, cat="maker-electronics", avail="in_stock"):
    r = _raw(ext, title, price)
    r.category, r.availability = cat, avail
    return ingest_listing(s, src, None, r, T0)[0]


def test_decimal_unit_values_still_match_their_listing(db):
    ensure_sources(db)
    with db.session() as s:
        a, b = _two_shops(s)
        _cat(s, a, "a1", "Raspberry Pi 15W USB-C Power Supply 5.1V 3A", 800)
        _cat(s, b, "b1", "Official Raspberry Pi Power Supply 5V 2.5A", 750)
        _cat(s, b, "b2", "Raspberry Pi 27W USB-C Power Supply 5.1V 5A", 1200)
        s.flush()
        assert [p.listing.external_id for p in assistant.answer(s, "5.1V 3A", now=T0, use_llm=False)["picks"]][:1] == ["a1"]
        assert assistant.answer(s, "2.5A power supply", now=T0, use_llm=False)["picks"][0].listing.external_id == "b1"


def test_a_spec_requirement_is_a_minimum_not_a_literal_keyword(db):
    ensure_sources(db)
    with db.session() as s:
        a, b = _two_shops(s)
        _cat(s, a, "a1", "Acme Notebook 14 8GB RAM 256GB SSD", 50000, cat="laptop")
        _cat(s, b, "b1", "Acme Notebook 14 16GB RAM 512GB SSD", 60000, cat="laptop")
        s.flush()
        ids = {p.listing.external_id for p in assistant.answer(s, "laptop with 8 GB RAM", now=T0, use_llm=False)["picks"]}
        assert ids == {"a1", "b1"}  # 16 GB meets '8 GB RAM' (the fit check treats it as 8+)


def test_plural_accessory_query_is_not_answered_with_the_board(db):
    ensure_sources(db)
    with db.session() as s:
        a, b = _two_shops(s)
        _cat(s, a, "a1", "Raspberry Pi 5 - 4 GB RAM", 6000)
        _cat(s, b, "b1", "Raspberry Pi 5 Case - Red/White", 1000)
        s.flush()
        for q in ("pi 5 cases", "pi 5 case"):
            assert assistant.answer(s, q, now=T0, use_llm=False)["picks"][0].listing.external_id == "b1", q


def test_leaving_out_other_product_types_is_reported(db):
    ensure_sources(db)
    with db.session() as s:
        a, b = _two_shops(s)
        _cat(s, a, "a1", "Raspberry Pi 5 - 2 GB RAM", 9000, avail="out_of_stock")
        _cat(s, b, "b2", "Raspberry Pi Compute Module 5 Lite - 2GB RAM", 7000)
        s.flush()
        r = assistant.answer(s, "pi 5 2gb", now=T0, use_llm=False)
        assert [p.listing.external_id for p in r["picks"]] == ["a1"]
        assert any("other product type" in c for c in r["caveats"]), r["caveats"]


def test_type_filter_does_not_apply_to_laptops_and_phones(db):
    ensure_sources(db)
    with db.session() as s:
        a, b = _two_shops(s)
        _cat(s, a, "a1", "Dell XPS 13 Touchscreen Display 16GB RAM", 120000, cat="laptop")
        _cat(s, b, "b1", "Dell XPS 13 16GB RAM", 125000, cat="laptop")
        s.flush()
        ids = {p.listing.external_id for p in assistant.answer(s, "dell xps 13", now=T0, use_llm=False)["picks"]}
        assert ids == {"a1", "b1"}


def test_decimal_values_with_longer_units_match_whole(db):
    ensure_sources(db)
    with db.session() as s:
        a, b = _two_shops(s)
        _cat(s, a, "a1", "Pi 5 Board 2.4GHz Quad Core", 8000)
        _cat(s, b, "b1", "Pi 4 Board 1.8GHz Quad Core", 5000)
        s.flush()
        assert [p.listing.external_id for p in assistant.answer(s, "board 2.4ghz", now=T0, use_llm=False)["picks"]] == ["a1"]


# --- mission 005: the answer reflects what has actually been collected --------------------------------------------
def _crawl_row(s, src_key, url, hint, **kw):
    from watchtower.models import CrawlUrl
    src = s.query(Source).filter_by(key=src_key).one()
    c = CrawlUrl(source_id=src.id, url=url, first_seen_at=T0, name_hint=hint, **kw)
    s.add(c)
    s.flush()
    return c


def test_a_discovered_but_uncollected_page_is_disclosed_not_claimed(db):
    ensure_sources(db)
    with db.session() as s:
        a = Source(key="adafruit", name="Adafruit", base_url="https://www.adafruit.com", currency="USD", kind="live")
        b = Source(key="sparkfun", name="SparkFun", base_url="https://www.sparkfun.com", currency="USD", kind="live")
        s.query(Source).filter(Source.key.in_(("adafruit", "sparkfun"))).delete()
        s.add_all([a, b])
        s.flush()
        ingest_listing(s, a, None, _raw("5291", "Raspberry Pi Zero 2 W", 1500), T0)
        _crawl_row(s, "sparkfun", "https://www.sparkfun.com/raspberry-pi-zero-2-w.html", "raspberry pi zero 2 w")
        _crawl_row(s, "sparkfun", "https://www.sparkfun.com/sparkfun-raspberry-pi-zero-2-w-basic-kit.html", "sparkfun raspberry pi zero 2 w basic kit")
        _crawl_row(s, "sparkfun", "https://www.sparkfun.com/raspberry-pi-pico-w.html", "raspberry pi pico w")
        s.flush()
        r = assistant.answer(s, "raspberry pi zero 2 w", now=T0, use_llm=False)
        assert [p.listing.source.key for p in r["picks"]] == ["adafruit"]  # an uncollected page is never a pick
        note = [c for c in r["caveats"] if "not collected" in c.lower()]
        assert len(note) == 1 and "SparkFun" in note[0] and "raspberry-pi-zero-2-w.html" in note[0]
        assert "price and stock are unknown" in note[0] and "kit" not in note[0] and "pico" not in note[0].lower()
        assert r["uncollected"] == [{"source": "SparkFun", "url": "https://www.sparkfun.com/raspberry-pi-zero-2-w.html"}]
        # once the page is collected the disclosure disappears and both merchants are compared
        ingest_listing(s, b, None, _raw("z2", "Raspberry Pi Zero 2 W", 1400), T0)
        s.query(__import__("watchtower.models", fromlist=["CrawlUrl"]).CrawlUrl).filter_by(url="https://www.sparkfun.com/raspberry-pi-zero-2-w.html").one().listing_id = 2
        s.flush()
        r = assistant.answer(s, "raspberry pi zero 2 w", now=T0, use_llm=False)
        assert not [c for c in r["caveats"] if "not collected" in c.lower()] and len(r["picks"]) == 2


def test_picks_state_the_cross_merchant_match_status_and_history_depth(db):
    ensure_sources(db)
    with db.session() as s:
        a, b = _two_shops(s)
        x, *_ = ingest_listing(s, a, None, _raw("a1", "Raspberry Pi 5 - 8 GB RAM", 20000), T0)
        y, *_ = ingest_listing(s, b, None, _raw("b1", "Raspberry Pi 5 - 8GB", 18000), T0)
        lone, *_ = ingest_listing(s, b, None, _raw("b9", "Raspberry Pi 500 Kit", 9000), T0)
        s.flush()
        r = assistant.answer(s, "raspberry pi 5 8gb", now=T0, use_llm=False)
        text = r["text"]
        assert "Also sold at" in text and "automatic match" in text and "not human-verified" in text
        assert "Price history: 1 observation" in text and "verified" in text.lower()
        y.match_status = x.match_status = "confirmed"
        s.flush()
        assert "confirmed by a person" in assistant.answer(s, "raspberry pi 5 8gb", now=T0, use_llm=False)["text"]
        r2 = assistant.answer(s, "raspberry pi kit", now=T0, use_llm=False)
        assert "Also sold at" not in r2["text"] and "no other shop" in r2["text"].lower()


def test_uncollected_disclosure_excludes_other_models_and_non_product_pages(db):
    ensure_sources(db)
    with db.session() as s:
        a = Source(key="adafruit", name="Adafruit", base_url="https://www.adafruit.com", currency="USD", kind="live")
        s.query(Source).filter(Source.key.in_(("adafruit", "sparkfun"))).delete()
        b = Source(key="sparkfun", name="SparkFun", base_url="https://www.sparkfun.com", currency="USD", kind="live")
        s.add_all([a, b])
        s.flush()
        ingest_listing(s, a, None, _raw("1", "Raspberry Pi Pico W", 600), T0)
        for slug in ("raspberry-pi-pico-w", "raspberry-pi-pico-2-w", "get-started-with-micropython-on-raspberry-pi-pico"):
            _crawl_row(s, "sparkfun", f"https://www.sparkfun.com/{slug}.html", None)
        s.flush()
        r = assistant.answer(s, "pi pico w", now=T0, use_llm=False)
        assert [u["url"].rsplit("/", 1)[1] for u in r["uncollected"]] == ["raspberry-pi-pico-w.html"]
