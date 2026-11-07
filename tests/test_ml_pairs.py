import random

import pytest

pytest.importorskip("sklearn")

from tests._ml_helpers import small_catalogue  # noqa: E402
from watchtower.ml import pairs as P  # noqa: E402


def _rows(db):
    with db.session() as s:
        small_catalogue(s)
        s.flush()
        from watchtower.models import Listing
        rows = s.query(Listing).all()
        s.expunge_all()
    return rows


def test_features_are_deterministic_and_symmetric(db):
    rows = _rows(db)
    a, b = rows[0], rows[1]
    f1, f2 = P.pair_features(a, b), P.pair_features(a, b)
    assert f1 == f2
    assert tuple(f1) == P.PAIR_FEATURE_NAMES
    sym = P.pair_features(b, a)
    for k in ("name_jaccard", "brand_equal", "seq_ratio", "title_conflict", "hard_conflict", "unit_kinds_agree"):
        assert f1[k] == pytest.approx(sym[k])


def test_candidate_pairs_independent_of_input_order(db):
    rows = _rows(db)
    base = P.candidate_pairs(rows)
    shuffled = rows[:]
    random.Random(7).shuffle(shuffled)
    assert P.candidate_pairs(shuffled) == base == sorted(base)
    assert all(a < b for a, b in base)
    srcs = {r.id: r.source_id for r in rows}
    assert all(srcs[a] != srcs[b] for a, b in base)  # cross-merchant only


def test_missing_price_is_flag_not_zero_ratio(db):
    rows = _rows(db)
    a, b = rows[0], rows[1]
    a.price_minor = None
    f = P.pair_features(a, b)
    assert f["has_price_pair"] == 0.0 and f["price_ratio"] == 0.0


def test_different_variants_are_vetoed_in_features(db):
    rows = {r.external_id: r for r in _rows(db)}
    f = P.pair_features(rows["a0"], rows["a1"])  # 2GB vs 4GB
    assert f["title_conflict"] == 1.0 and f["hard_conflict"] == 1.0
    same = P.pair_features(rows["a0"], rows["b0"])
    assert same["title_conflict"] == 0.0 and same["unit_kinds_conflict"] == 0.0 and same["unit_kinds_agree"] >= 1
    assert same["name_jaccard"] > f["name_jaccard"] or same["hard_conflict"] < f["hard_conflict"]


def test_model_feature_set_excludes_rule_echo():
    assert not set(P.RULE_ECHO_FEATURES) & set(P.MODEL_FEATURE_NAMES)
