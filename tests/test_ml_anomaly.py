"""Anomaly tests. All series below are SYNTHETIC TEST FIXTURES built inside the tests; they are not evidence about real prices."""
from datetime import datetime, timedelta

import pytest

pytest.importorskip("sklearn")

from tests._ml_helpers import mk_listing, mk_obs, mk_source  # noqa: E402
from watchtower.ml import anomaly  # noqa: E402

T0 = datetime(2026, 1, 1, 12)


def series(prices, days_apart=1):
    return [(T0 + timedelta(days=i * days_apart), p) for i, p in enumerate(prices)]


def test_refuses_one_day_of_data_even_with_many_observations():
    pts = [(T0 + timedelta(minutes=i), 1000) for i in range(50)]
    v = anomaly.detect_series(pts)
    assert v.status == "refused" and "1 distinct day" in v.reason and v.flags == []


def test_refuses_too_few_observations():
    v = anomaly.detect_series(series([1000, 1000, 5000]))
    assert v.status == "refused" and "observation" in v.reason


def test_detects_clear_synthetic_spike_and_not_flat_noise():
    prices = [1000, 1010, 995, 1005, 1000, 990, 1008, 1002, 998, 1004, 1001, 997, 4000, 1003]
    v = anomaly.detect_series(series(prices))
    assert v.status == "ok" and v.advisory
    flagged = {f["price_minor"] for f in v.flags}
    assert flagged == {4000}
    assert all(f["z_flag"] and f["iqr_flag"] for f in v.flags)
    quiet = anomaly.detect_series(series([1000, 1010, 995, 1005, 1000, 990, 1008, 1002, 998, 1004]))
    assert quiet.status == "ok" and quiet.flags == []


def test_flat_series_then_jump_is_flagged_without_division_by_zero():
    v = anomaly.detect_series(series([1000] * 10 + [2000]))
    assert v.status == "ok" and [f["price_minor"] for f in v.flags] == [2000]


def test_isolation_forest_refuses_on_insufficient_fleet():
    fleet = {i: anomaly.PriceSeries(i, "USD", series([1000] * 3)) for i in range(5)}
    r = anomaly.isolation_forest_scan(fleet)
    assert r["status"] == "refused" and r["model"] is None and "need >=" in r["reason"]


def test_isolation_forest_flags_injected_synthetic_spike():
    import random
    rng = random.Random(1)
    fleet = {}
    for i in range(40):
        base = 1000 + 50 * i
        prices = [int(base * (1 + rng.uniform(-0.01, 0.01))) for _ in range(10)]
        if i == 7:
            prices[6] = base * 5  # synthetic anomaly
        fleet[i] = anomaly.PriceSeries(i, "USD", series(prices))
    r = anomaly.isolation_forest_scan(fleet, contamination=0.01, seed=0)
    assert r["status"] == "ok" and r["advisory"] is True
    assert any(f["listing_id"] == 7 and f["price_minor"] == 1350 * 5 for f in r["flagged"])


def test_scan_database_uses_live_only_and_refuses_one_day(db):
    with db.session() as s:
        live, demo = mk_source(s, "live1"), mk_source(s, "demo1", "demo")
        l1, l2 = mk_listing(s, live, "1", "A 1GB"), mk_listing(s, demo, "2", "B 1GB")
        for i in range(30):  # same day crawls
            mk_obs(s, l1, T0 + timedelta(minutes=i), 1000)
            mk_obs(s, l2, T0 + timedelta(days=i), 1000 if i != 20 else 9000)
        s.flush()
        series_ = anomaly.load_price_series(s)
        assert set(series_) == {l1.id}  # demo history is not evidence
        rep = anomaly.scan_database(s)
        assert rep["listings_scanned"] == 0 and rep["refused_by_reason"] == {"history spans too few distinct days": 1}
        assert rep["isolation_forest"]["status"] == "refused"


def test_null_prices_are_skipped_not_zero(db):
    with db.session() as s:
        live = mk_source(s, "live1")
        l = mk_listing(s, live, "1", "A 1GB")
        mk_obs(s, l, T0, None)
        mk_obs(s, l, T0 + timedelta(days=1), 1000)
        s.flush()
        assert [p for _, p in anomaly.load_price_series(s)[l.id].points] == [1000]
