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
