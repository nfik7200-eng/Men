import pytest
"""Minor-unit exponents: a price typed or parsed in a zero- or three-decimal currency must not be scaled like dollars."""
from watchtower.currency import minor_digits, to_minor
from watchtower.money import fmt_money, parse_money
from shopping.search import CurrencyRequired, search_listings
from tests._ml_helpers import mk_listing, mk_source


def test_exponents_follow_iso_4217():
    assert minor_digits("JPY") == 0 and minor_digits("KRW") == 0 and minor_digits("UGX") == 0
    assert minor_digits("KWD") == 3 and minor_digits("BHD") == 3
    assert minor_digits("KES") == 2 and minor_digits("TZS") == 2 and minor_digits("USD") == 2  # were wrongly zero-decimal
    assert minor_digits("") == 2


def test_major_to_minor_uses_the_currency_exponent():
    assert to_minor(1000, "JPY") == 1000        # yen has no minor unit
    assert to_minor(12.34, "USD") == 1234
    assert to_minor(1.2345, "KWD") == 1234 or to_minor(1.2345, "KWD") == 1235  # three decimals, half-even rounding
    assert to_minor(0.125, "USD") == 12         # half-to-even on the exact decimal value


def test_money_is_formatted_with_the_right_digits():
    assert fmt_money(1000, "JPY") == "JPY 1,000"
    assert fmt_money(1234, "USD") == "$12.34"
    assert fmt_money(12345, "KWD") == "KWD 12.345"


def test_parsed_zero_decimal_price_is_not_scaled():
    assert parse_money("JPY 1000") == (1000, "JPY")
    assert parse_money("£12.34") == (1234, "GBP")


def _two_currency_widgets(db):
    with db.session() as s:
        usd = mk_source(s, "shopa")
        jpy = mk_source(s, "shopj")
        jpy.currency = "JPY"
        mk_listing(s, usd, "u1", "Widget", price=1000)     # $10.00
        l = mk_listing(s, jpy, "j1", "Widget", price=1000)
        l.currency = "JPY"                                  # 1,000 yen
        s.commit()


def test_a_price_limit_without_a_currency_is_refused(db):
    _two_currency_widgets(db)
    with db.session() as s:
        with pytest.raises(CurrencyRequired):
            search_listings(s, "Widget", max_price=15)


def test_a_price_limit_with_a_currency_compares_only_that_currency(db):
    _two_currency_widgets(db)
    with db.session() as s:
        rows, _ = search_listings(s, "Widget", currency="JPY", max_price=1500)
        assert [r.currency for r in rows] == ["JPY"]
        rows, _ = search_listings(s, "Widget", currency="JPY", max_price=500)
        assert rows == []
        rows, _ = search_listings(s, "Widget", currency="USD", max_price=15)
        assert [r.currency for r in rows] == ["USD"]


def test_a_price_sort_across_currencies_falls_back_to_name_and_says_so(db):
    _two_currency_widgets(db)
    info: dict = {}
    with db.session() as s:
        search_listings(s, "Widget", sort="price_asc", info=info)
    assert info["currencies"] == ["JPY", "USD"]
    assert info["sort_fallback"] == "name"


def test_a_single_currency_still_sorts_by_price(db):
    with db.session() as s:
        usd = mk_source(s, "shopa")
        mk_listing(s, usd, "u1", "Gadget", price=2000)
        mk_listing(s, usd, "u2", "Gadget", price=500)
        s.commit()
    info: dict = {}
    with db.session() as s:
        rows, _ = search_listings(s, "Gadget", sort="price_asc", info=info)
    assert [r.price_minor for r in rows] == [500, 2000]
    assert "sort_fallback" not in info


def test_a_three_decimal_currency_limit_is_exact(db):
    with db.session() as s:
        src = mk_source(s, "shopk")
        src.currency = "KWD"
        l = mk_listing(s, src, "k1", "Tile", price=12345)
        l.currency = "KWD"
        s.commit()
    with db.session() as s:
        rows, _ = search_listings(s, "Tile", currency="KWD", max_price=12.345)
        assert len(rows) == 1
        rows, _ = search_listings(s, "Tile", currency="KWD", max_price=12.344)
        assert rows == []


def test_a_third_decimal_is_kept_for_three_decimal_currencies():
    assert parse_money("KWD 12.345") == (12345, "KWD")   # used to be 12340: the third digit was dropped


def test_more_precision_than_the_currency_has_is_refused_not_rounded():
    assert parse_money("USD 12.345") is None             # two-decimal currency: refuse rather than invent a digit
    assert parse_money("JPY 1000.5") is None             # zero-decimal currency: a fraction is not a price
