"""Regression tests for the independent ML review (H1-H2, M1-M6, L1-L7). All data is SYNTHETIC test fixture data."""
import json
import random
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("sklearn")

from tests._ml_helpers import mk_listing, mk_obs, mk_product, mk_source, small_catalogue  # noqa: E402
from watchtower.ml import anomaly, dataset, evaluate, pairs as P, sufficiency  # noqa: E402
from watchtower.models import MatchCandidate  # noqa: E402

T0 = datetime(2026, 1, 1, 12)


def series(prices):
    return [(T0 + timedelta(days=i), p) for i, p in enumerate(prices)]


# ----------------------------------------------------------------------------------------------------------- H1
def test_single_feature_auc_detects_label_reproducing_feature():
    y = [0] * 20 + [1] * 20
    perfect = [0.0] * 20 + [1.0] * 20
    inverted = [1.0] * 20 + [0.0] * 20
    rng = random.Random(0)
    noise = [rng.random() for _ in y]
    assert evaluate.single_feature_auc(y, perfect) == 1.0
    assert evaluate.single_feature_auc(y, inverted) == 1.0  # oriented: |AUC-0.5|+0.5
    assert evaluate.single_feature_auc(y, noise) < 0.8
    assert evaluate.single_feature_auc(y, [1.0] * 40) == 0.5  # constant feature carries nothing


def test_ablation_block_and_task_trivial_flag():
    y = [0, 0, 0, 1, 1, 1]
    cols = {"f_leaky": [0, 0, 0, 1, 1, 1], "f_noise": [1, 0, 1, 0, 1, 0]}
    blk = evaluate.ablation_block(y, cols)
    assert blk["features"]["f_leaky"] == 1.0 and blk["max_feature"] == "f_leaky"
    assert blk["task_trivial"] is True and blk["threshold"] == evaluate.TRIVIAL_AUC == 0.95
    assert evaluate.ablation_block(y, {"f_noise": cols["f_noise"]})["task_trivial"] is False


def test_report_carries_ablation_and_prominent_task_trivial(db):
    with db.session() as s:
        small_catalogue(s)
        rep = evaluate.run_evaluation(s, exploratory=True, k=2, out_dir=None)
        assert set(rep["ablation_single_feature_auc"]["pooled"]["features"]) == set(P.MODEL_FEATURE_NAMES)
        assert isinstance(rep["task_trivial"], bool)
        assert "TASK TRIVIAL" in rep["headline"] if rep["task_trivial"] else "task_trivial=false" in rep["headline"]


# ----------------------------------------------------------------------------------------------------------- H2
def _oof(n_h=40, n_r=40):
    """human tier: noisy scores; rule tier: perfectly separated (circular)."""
    rng = random.Random(3)
    rows = []
    for i in range(n_h):
        y = i % 2
        rows.append((y, min(1.0, max(0.0, 0.5 * y + rng.random() * 0.5)), "human"))
    for i in range(n_r):
        y = i % 2
        rows.append((y, 0.9 * y + 0.05, "rule_derived"))
    return rows


def test_tier_blocks_flags_and_pooled_is_never_evidence():
    blocks = evaluate.tier_blocks(_oof())
    assert blocks["human"]["circular"] is False and blocks["rule_derived"]["circular"] is True
    assert blocks["pooled"]["circular"] is True and blocks["pooled"]["evidence_eligible"] is False
    assert blocks["rule_derived"]["evidence_eligible"] is False
    for b in blocks.values():
        assert "underpowered" in b and "circular" in b
    assert blocks["human"]["underpowered"] is True  # 20 positives < 30


def test_human_pooled_evaluation_is_never_stamped_evidence():
    big = evaluate.tier_blocks(_oof(200, 200))
    assert big["human"]["underpowered"] is False and big["human"]["evidence_eligible"] is True
    ok_abl = {"task_trivial": False}
    assert evaluate.decide_models_status(True, False, big, ok_abl).startswith("evidence")
    # only a pooled/rule block: never evidence even when sufficiency says justified
    only_rule = {k: v for k, v in big.items() if k != "human"}
    assert not evaluate.decide_models_status(True, False, only_rule, ok_abl).startswith("evidence")
    # underpowered human tier: not evidence
    small = evaluate.tier_blocks(_oof(10, 200))
    assert not evaluate.decide_models_status(True, False, small, ok_abl).startswith("evidence")
    # human tier whose own task is trivial: not evidence
    assert not evaluate.decide_models_status(True, False, big, {"task_trivial": True}).startswith("evidence")
    assert "NOT EVIDENCE" in evaluate.decide_models_status(False, True, big, ok_abl)


def test_models_report_has_per_tier_blocks(db):
    with db.session() as s:
        small_catalogue(s)
        rep = evaluate.run_evaluation(s, exploratory=True, k=2, out_dir=None)
        for m in rep["models"].values():
            assert "by_trust_tier" in m and "pooled" in m["by_trust_tier"]
            assert m["by_trust_tier"]["pooled"]["evidence_eligible"] is False
        assert not rep["models_status"].startswith("evidence")


# ----------------------------------------------------------------------------------------------------------- M1
def test_recall_at_precision_is_labelled_oracle_with_ci_and_power_flag():
    y = [0] * 30 + [1] * 30
    score = [i / 100 for i in range(30)] + [0.5 + i / 100 for i in range(30)]
    r = evaluate.recall_at_precision(y, score)
    assert r["threshold_selection"] == "oracle_threshold"
    assert r["recall"] == 1.0 and r["n_predicted_positives"] == 30
    assert r["underpowered_for_target"] is True  # < 100 predicted positives cannot certify 0.99 precision
    lo, hi = r["recall_ci95"]
    assert lo <= r["recall"] <= hi
    assert evaluate.recall_at_precision(y, score) == r  # deterministic bootstrap


def test_recall_at_precision_ties_and_unreachable():
    assert evaluate.recall_at_precision([0, 1], [0.5, 0.5])["recall"] is None  # tie: precision 0.5 < target
    assert evaluate.recall_at_precision([0, 0], [0.1, 0.2])["recall"] is None


def test_nested_threshold_is_chosen_on_training_data_only():
    # train scores perfectly separate at 0.5; test has an overlapping noisy tail: nested recall must not exceed what the train
    # threshold gives on test, and must never use test labels to pick the threshold
    ytr = [0] * 50 + [1] * 50
    str_ = [0.1] * 50 + [0.9] * 50
    yte = [0, 0, 1, 1]
    ste = [0.2, 0.95, 0.3, 0.9]
    r = evaluate.apply_train_threshold(ytr, str_, yte, ste)
    assert r["threshold_selection"] == "nested_train_threshold" and r["threshold"] == 0.9
    assert r["precision"] == 0.5 and r["recall"] == 0.5


# ----------------------------------------------------------------------------------------------------------- M2 / M3
def _two_merchant_product(s, n=3, statuses=("confirmed", "auto", "auto")):
    a, b, c = mk_source(s, "sa"), mk_source(s, "sb"), mk_source(s, "sc")
    p = mk_product(s, "Acme Widget 2GB")
    ls = [mk_listing(s, src, f"x{i}", "Acme Widget 2GB", product=p, status=st)
          for i, (src, st) in enumerate(zip((a, b, c), statuses))]
    return p, ls


def test_mixed_confirmed_pair_is_human_propagated_not_human(db):
    with db.session() as s:
        p, (l0, l1, l2) = _two_merchant_product(s)
        pairs, _ = dataset.build_labelled_pairs(dataset.load_live_listings(s))
        by = {(x.a_id, x.b_id): x for x in pairs}
        assert by[tuple(sorted((l0.id, l1.id)))].trust == "human_propagated"
        assert by[tuple(sorted((l1.id, l2.id)))].trust == "rule_derived"
        summ = dataset.summarize(pairs)
        assert summ["positives_human"] == 0 and summ["positives_human_propagated"] == 2


def test_both_confirmed_is_human(db):
    with db.session() as s:
        p, (l0, l1, l2) = _two_merchant_product(s, statuses=("confirmed", "confirmed", "auto"))
        pairs, _ = dataset.build_labelled_pairs(dataset.load_live_listings(s))
        by = {(x.a_id, x.b_id): x for x in pairs}
        assert by[tuple(sorted((l0.id, l1.id)))].trust == "human"


def test_undecided_candidates_are_not_labels_and_decisions_count_once(db):
    with db.session() as s:
        p, (l0, l1, l2) = _two_merchant_product(s, statuses=("confirmed", "auto", "auto"))
        extra = mk_listing(s, mk_source(s, "sd"), "z", "Acme Widget 2GB", status="confirmed", product=p)
        s.add_all([MatchCandidate(listing_id=extra.id, product_id=p.id, score=0.8, status="accepted", decided_by="admin"),
                   MatchCandidate(listing_id=l1.id, product_id=p.id, score=0.8, status="rejected", decided_by=None)])
        s.flush()
        live = dataset.load_live_listings(s)
        decs = dataset.load_candidate_decisions(s, {l.id for l in live})
        assert [(d[0], d[2]) for d in decs] == [(extra.id, "accepted")]  # decided_by NULL is not a human decision
        pairs, _ = dataset.build_labelled_pairs(live, decs)
        summ = dataset.summarize(pairs)
        # one accepted decision covers 3 member pairs: 3 pairs, but ONE decision and ONE positive product
        assert summ["positives_human"] == 3 and summ["human_pos_decisions"] == 1 and summ["human_positive_products"] == 1


def test_sufficiency_counts_decisions_and_positive_products_not_pairs():
    summ = {"positives_human": 500, "negatives_human": 500, "human_pos_decisions": 2, "human_neg_decisions": 2,
            "human_positive_products": 2}
    assert sufficiency.supervised_ok(summ) is False
    summ.update(human_pos_decisions=100, human_neg_decisions=100, human_positive_products=29)
    assert sufficiency.supervised_ok(summ) is False  # 29 positive products < 30
    summ.update(human_positive_products=30)
    assert sufficiency.supervised_ok(summ) is True
    assert not hasattr(sufficiency, "SUPERVISED_MIN_GROUPS")  # replaced by the positive-product threshold
    assert sufficiency.SUPERVISED_MIN_POSITIVE_PRODUCTS == 30


# ----------------------------------------------------------------------------------------------------------- M4 / M5
def _dup_listings(s):
    a, b = mk_source(s, "da"), mk_source(s, "db")
    out = []
    for i in range(12):  # each "model" appears twice on the same source with identical brand+title (e.g. colour variants)
        out += [mk_listing(s, a, f"a{i}", f"Acme Gizmo{i} Thing", price=100 + i),
                mk_listing(s, a, f"a{i}v", f"Acme Gizmo{i} Thing", price=200 + i),
                mk_listing(s, b, f"b{i}", f"Acme Gizmo{i} Thing", price=300 + i)]
    return out


def test_group_map_unions_identical_brand_signature_with_products(db):
    with db.session() as s:
        ls = _dup_listings(s)
        g = dataset.build_group_map(ls)
        assert len({g[l.id] for l in ls}) == 12
        assert len({dataset.group_of_listing(l) for l in ls}) == 36  # the old grouping separated the duplicates
        p = mk_product(s)
        x = mk_listing(s, mk_source(s, "dz"), "zz", "Totally Different Item", product=p)
        y = mk_listing(s, mk_source(s, "dy"), "yy", "Another Thing Entirely", product=p)
        g2 = dataset.build_group_map(ls + [x, y])
        assert g2[x.id] == g2[y.id]  # product link still unions


def test_folds_with_group_map_have_zero_cross_fold_duplicates(db):
    with db.session() as s:
        ls = _dup_listings(s)
        pairs = [dataset.LabelledPair(*sorted((x.id, y.id)), 0, "rule_veto", "rule_derived")
                 for x in ls for y in ls if x.id < y.id and x.title != y.title][:200]
        key_of = dataset.name_signature_keys(ls)
        old = {l.id: dataset.group_of_listing(l) for l in ls}
        new = dataset.build_group_map(ls)
        rate_old = max(dataset.cross_fold_duplicate_rate(dataset.group_folds(pairs, old, 3, sd), key_of) for sd in range(5))
        assert rate_old > 0  # the metric detects the problem the reviewer found
        for sd in range(5):
            assert dataset.cross_fold_duplicate_rate(dataset.group_folds(pairs, new, 3, sd), key_of) == 0.0


def test_fold_summary_reports_unique_never_evaluated_and_class_counts():
    g = {i: f"g{i}" for i in range(1, 7)}
    prs = [dataset.LabelledPair(a, b, int((a + b) % 2 == 0), "rule_veto", "rule_derived")
           for a in range(1, 7) for b in range(a + 1, 7)]
    folds = dataset.group_folds(prs, g, 3, 0)
    fs = dataset.fold_summary(prs, folds)
    tested = {p.key() for _tr, te, _d in folds for p in te}
    assert fs["pairs_total"] == len(prs) and fs["pairs_never_in_a_test_fold"] == len(prs) - len(tested)
    assert fs["pairs_never_in_a_test_fold"] <= len(prs)
    assert sum(f["dropped_straddling"] for f in fs["folds"]) > fs["pairs_never_in_a_test_fold"]  # the old doc's sum-over-folds
    for f, (tr, te, d) in zip(fs["folds"], folds):
        assert f["test"] == {"positives": sum(p.label for p in te), "negatives": sum(1 - p.label for p in te)}
        assert f["train"]["positives"] == sum(p.label for p in tr)


# ----------------------------------------------------------------------------------------------------------- M6
def test_load_price_series_drops_non_finite_and_non_integer_prices(db):
    with db.session() as s:
        live = mk_source(s, "live1")
        l = mk_listing(s, live, "1", "A 1GB")
        for i, price in enumerate([1000, float("inf"), 10.5, 0, -5, 2000.0, 1500]):
            mk_obs(s, l, T0 + timedelta(days=i), price)
        s.flush()
        ser, stats = anomaly.load_price_series_with_stats(s)
        assert [p for _, p in ser[l.id].points] == [1000, 2000, 1500]
        assert all(isinstance(p, int) for _, p in ser[l.id].points)
        assert stats["rows_dropped"] == {"non_finite": 1, "non_integer": 1, "non_positive": 2}
        rep = anomaly.scan_database(s)  # must not raise math domain errors
        assert rep["price_rows_dropped"]["non_finite"] == 1


# ----------------------------------------------------------------------------------------------------------- L1 / L2 / L4
def test_five_percent_sale_after_flat_series_is_not_flagged():
    v = anomaly.detect_series(series([1000] * 9 + [950]))
    assert v.status == "refused" and "variation" in v.reason and v.flags == []


def test_flat_series_with_two_changes_still_flags_real_jump_but_not_small_wobble():
    big = anomaly.detect_series(series([1000] * 8 + [2000, 1000]))
    assert big.status == "ok" and [f["price_minor"] for f in big.flags] == [2000]
    small = anomaly.detect_series(series([1000] * 8 + [950, 1000]))
    assert small.status == "ok" and small.flags == []  # 5% moves sit below the minimum relative scale


def test_daily_last_price_dedupe():
    pts = [(T0 + timedelta(days=d, hours=h), 1000 + d * 10 + h) for d in range(8) for h in range(3)]
    daily = anomaly.daily_last(pts)
    assert len(daily) == 8 and [p for _, p in daily][:2] == [1002, 1012]
    v = anomaly.detect_series(pts)
    assert v.status == "ok" and v.n_obs == 8


def _fleet():
    rng = random.Random(1)
    fleet = {}
    for i in range(40):
        base = 1000 + 50 * i
        fleet[i] = anomaly.PriceSeries(i, "USD", series([int(base * (1 + rng.uniform(-0.01, 0.01))) for _ in range(10)]))
    return fleet


def test_isolation_forest_default_does_not_force_a_flag_fraction():
    r = anomaly.isolation_forest_scan(_fleet())
    assert r["contamination"] == "auto" and r["forced_flag_fraction"] is None
    assert r["flagged_fraction"] == pytest.approx(len(r["flagged"]) / r["n_obs"])
    forced = anomaly.isolation_forest_scan(_fleet(), contamination=0.05)
    assert forced["forced_flag_fraction"] == 0.05


def test_scan_database_survives_missing_sklearn(db, monkeypatch):
    monkeypatch.setattr(anomaly, "load_price_series", lambda s: _fleet())
    monkeypatch.setattr(anomaly, "load_price_series_with_stats", lambda s: (_fleet(), {"rows_dropped": {}, "listings_dropped_mixed_currency": 0}))
    monkeypatch.setitem(sys.modules, "sklearn.ensemble", None)
    with db.session() as s:
        rep = anomaly.scan_database(s)
    assert rep["isolation_forest"]["status"] == "refused" and "scikit-learn" in rep["isolation_forest"]["reason"]


# ----------------------------------------------------------------------------------------------------------- L3 / L5 / L6 / L7
def test_objects_without_source_are_refused_by_default():
    bare = SimpleNamespace(id=1, source_id=1, product_id=None, title="x", brand=None, match_status="self")
    with pytest.raises(ValueError):
        dataset.build_labelled_pairs([bare])


def test_report_records_stack_commit_and_content_fingerprint(db, tmp_path):
    with db.session() as s:
        small_catalogue(s)
        rep = evaluate.run_evaluation(s, out_dir=None)
        for k in ("numpy", "scipy", "joblib", "sklearn"):
            assert rep["libraries"][k]
        assert "code_commit" in rep and (rep["code_commit"] is None or len(rep["code_commit"]) >= 7)
        fp1 = rep["dataset_fingerprint"]
        from watchtower.models import Listing
        l = s.query(Listing).filter_by(external_id="a0").one()
        l.title = "Acme Widget 2GB EDITED"
        s.flush()
        assert evaluate.run_evaluation(s, out_dir=None)["dataset_fingerprint"] != fp1  # content-based, not just ids


def test_named_constants_exist_and_pandas_not_required():
    for mod, names in ((evaluate, ("TARGET_PRECISION", "TRIVIAL_AUC", "BOOTSTRAP_N", "CALIBRATION_BINS", "DECISION_THRESHOLD")),
                       (anomaly, ("MIN_REL_SCALE", "MIN_DISTINCT_PRICES", "MIN_PRICE_CHANGES", "IF_N_ESTIMATORS")),
                       (P, ("MAX_BLOCK_SIZE", "SCREEN_TOLERANCE_IN"))):
        for n in names:
            assert hasattr(mod, n), n
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    assert "pandas" not in [l.split("==")[0].strip() for l in (root / "requirements-ml.txt").read_text().splitlines()
                            if not l.startswith("#")]
    for f in list((root / "watchtower" / "ml").glob("*.py")) + [root / "scripts" / "ml_env_check.py"]:
        assert "import pandas" not in f.read_text()


def test_json_report_is_serialisable(db, tmp_path):
    with db.session() as s:
        small_catalogue(s)
        rep = evaluate.run_evaluation(s, exploratory=True, k=2, out_dir=tmp_path)
        json.loads(open(rep["report_path"]).read())


def test_snapshot_without_audit_column_yields_no_human_decisions(tmp_path):
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    f = tmp_path / "old.db"
    c = sqlite3.connect(f)
    c.execute("create table match_candidates (id integer primary key, listing_id int, product_id int, status text)")
    c.execute("insert into match_candidates values (1, 1, 1, 'accepted')")
    c.commit()
    c.close()
    with Session(create_engine(f"sqlite:///{f}")) as s:
        assert dataset.load_candidate_decisions(s, {1}) == []
