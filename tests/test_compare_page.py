"""The comparison page: listings side by side, each price in its own currency, never ranked across currencies."""
from fastapi.testclient import TestClient

from shopping.web.app import create_app
from tests._ml_helpers import mk_listing, mk_source


def _two_listings(db, usd_price=1200, jpy_price=1500):
    with db.session() as s:
        usd_src = mk_source(s, "cmpusd")
        jpy_src = mk_source(s, "cmpjpy")
        jpy_src.currency = "JPY"
        a = mk_listing(s, usd_src, "a1", "Compare board A", price=usd_price)
        a.specs = {"voltage": "5 V"}
        b = mk_listing(s, jpy_src, "b1", "Compare board B", price=jpy_price)
        b.currency = "JPY"
        b.specs = {"voltage": "5.2 V", "colour": "red"}
        s.commit()
        return a.id, b.id


def test_compare_shows_each_listing_with_its_spec_rows_and_currency_note(db):
    a, b = _two_listings(db)
    client = TestClient(create_app(db), base_url="http://testserver")
    page = client.get(f"/compare?ids={a},{b}")
    assert page.status_code == 200
    assert "Compare board A" in page.text and "Compare board B" in page.text
    assert "voltage" in page.text and "colour" in page.text
    assert "These listings use JPY, USD" in page.text
    assert "not compared with each other" in page.text


def test_compare_with_one_currency_has_no_currency_note_and_ignores_bad_ids(db):
    a, _ = _two_listings(db)
    client = TestClient(create_app(db), base_url="http://testserver")
    page = client.get(f"/compare?ids={a},notanumber,999999")
    assert page.status_code == 200
    assert "Compare board A" in page.text
    assert "not compared with each other" not in page.text


def test_compare_with_nothing_chosen_explains_how(db):
    client = TestClient(create_app(db), base_url="http://testserver")
    assert "Nothing to compare yet" in client.get("/compare").text
