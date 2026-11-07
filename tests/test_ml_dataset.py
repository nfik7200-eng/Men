import json
from datetime import datetime

import pytest

pytest.importorskip("sklearn")

from sqlalchemy import select  # noqa: E402
from tests._ml_helpers import mk_listing, mk_product, mk_source, small_catalogue  # noqa: E402
from watchtower.ml import dataset, evaluate, sufficiency  # noqa: E402
from watchtower.models import Listing  # noqa: E402


def _build(s):
    small_catalogue(s)
    ls = dataset.load_live_listings(s)
    return ls, *dataset.build_labelled_pairs(ls)


def test_demo_listings_are_never_evidence(db):
    with db.session() as s:
        small_catalogue(s)
        ls = dataset.load_live_listings(s)
        assert all(l.source.kind == "live" for l in ls) and len(ls) == 9
        demo = s.scalars(select(Listing).where(Listing.external_id == "d0")).one()
        with pytest.raises(ValueError):
            dataset.build_labelled_pairs(ls + [demo])


def test_label_provenance_and_trust(db):
    with db.session() as s:
        ls, pairs, stats = _build(s)
        pos = [p for p in pairs if p.label == 1]
        assert len(pos) == 3 and all(p.label_source == "rule_auto" and p.trust == "rule_derived" for p in pos)
        assert all(p.trust == "rule_derived" for p in pairs if p.label == 0)  # no human negatives were supplied
        assert all(p.a_id < p.b_id for p in pairs)


def test_review_state_listings_are_excluded(db):
    with db.session() as s:
        small_catalogue(s)
        r = s.scalars(select(Listing).where(Listing.external_id == "b0")).one()
        r.match_status = "review"
        ls = dataset.load_live_listings(s)
        pairs, stats = dataset.build_labelled_pairs(ls)
        assert stats["excluded_review_state"] == 1
        assert all(r.id not in (p.a_id, p.b_id) for p in pairs)


def test_group_split_has_no_leakage_for_every_seed(db):
    with db.session() as s:
        ls, pairs, _ = _build(s)
        group_of = {l.id: dataset.group_of_listing(l) for l in ls}
        for seed in range(10):
            train, test, dropped = dataset.group_split(pairs, group_of, 0.4, seed)
            dataset.assert_no_group_leak(train, test, group_of)
            assert len(train) + len(test) + dropped == len(pairs)
            for tr, te, _d in dataset.group_folds(pairs, group_of, 3, seed):
                dataset.assert_no_group_leak(tr, te, group_of)
        assert dataset.group_split(pairs, group_of, 0.4, 3) == dataset.group_split(pairs, group_of, 0.4, 3)  # deterministic


def test_leak_detector_actually_detects(db):
    with db.session() as s:
        ls, pairs, _ = _build(s)
        group_of = {l.id: dataset.group_of_listing(l) for l in ls}
        with pytest.raises(dataset.LeakageError):
            dataset.assert_no_group_leak(pairs[:5], pairs[:5], group_of)


def test_sufficiency_refuses_supervised_and_circular_labels_do_not_count(db):
    with db.session() as s:
        small_catalogue(s)
        rep = sufficiency.sufficiency_report(s)
        assert rep["justified"] == {"supervised_matching": False, "anomaly_robust_baseline": False,
                                    "anomaly_isolation_forest": False, "price_forecasting": False}
        assert rep["labelled_pairs"]["positives_human"] == 0 and rep["labelled_pairs"]["positives_rule_derived"] == 3
        assert rep["live_listings"] == 9 and rep["cross_merchant_products"] == 3
        assert "Insufficient" in rep["verdict"] and "harness + rules baseline only" in rep["verdict"]


def test_evaluation_refuses_models_and_writes_versioned_report(db, tmp_path):
    with db.session() as s:
        small_catalogue(s)
        rep = evaluate.run_evaluation(s, seed=5, out_dir=tmp_path, now=datetime(2026, 1, 2, 3, 4, 5))
        assert rep["models"]["status"] == "skipped" and rep["models"]["reason"].startswith("refused")
        assert rep["rule_baseline"]["human"]["n"] == 0 and rep["rule_baseline"]["rule_derived"]["circular"] is True
        assert rep["never_auto_merge"] is True and rep["seed"] == 5
        saved = json.loads((tmp_path / f"{rep['run_id']}.json").read_text())
        assert saved["run_id"].startswith("ml-20260102T030405Z-s5-") and saved["dataset_fingerprint"] == rep["dataset_fingerprint"]
        again = evaluate.run_evaluation(s, seed=5, out_dir=None, now=datetime(2026, 1, 2, 3, 4, 5))
        assert again["run_id"] == rep["run_id"]  # deterministic given data+seed+clock


def test_exploratory_models_are_stamped_not_evidence(db, tmp_path):
    with db.session() as s:
        small_catalogue(s)
        rep = evaluate.run_evaluation(s, exploratory=True, k=2, out_dir=None)
        assert "NOT EVIDENCE" in rep["models_status"]


def test_open_readonly_refuses_app_db(tmp_path, monkeypatch):
    from watchtower import config
    p = tmp_path / "app.db"
    p.write_bytes(b"")
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{p}")
    with pytest.raises(dataset.RefusedDatabase):
        dataset.open_readonly_session(p)


def test_ml_does_not_leak_into_core_or_import_shopping():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for f in (root / "watchtower" / "ml").glob("*.py"):
        assert "shopping" not in "\n".join(l for l in f.read_text().splitlines() if l.lstrip().startswith(("import", "from")))
    for f in (root / "watchtower").glob("*.py"):
        assert "watchtower.ml" not in f.read_text() and "from .ml" not in f.read_text()
    for f in (root / "shopping").rglob("*.py"):
        assert "watchtower.ml" not in f.read_text()
