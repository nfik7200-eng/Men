"""Candidate ranking (Stage 4): point-in-time features, leakage-safe splits, baselines, the
supervised gate and the metrics. SYNTHETIC rows only; synthetic labels are never persisted."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, insert, select, text

from bip.config import load_settings
from bip.db import schema
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.discovery.review import ReviewService
from bip.discovery.service import DiscoveryService
from bip.ml.signal_ranking import (
    NUMERIC_FEATURES,
    SignalRanking,
    _Row,  # pyright: ignore[reportPrivateUsage]
    anomaly_scores,
    average_precision,
    choose_threshold,
    entity_split,
    evaluate_scores,
)
from tests.discovery.seed import E18, ETH, NOW, Seed, eth_addr, eth_tx

T0 = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    e = create_db_engine(load_settings(data_root=tmp_path / "data", env="test"))
    yield e
    e.dispose()


def ranking(engine: Engine, **ml: Any) -> tuple[SignalRanking, ReviewService, Repository]:
    settings = load_settings(data_root=Path("/unused"), env="test", ml=ml)
    repo = Repository(engine)
    review = ReviewService(repo)
    return SignalRanking(repo, review, settings.ml), review, repo


def add(
    repo: Repository,
    n: int,
    *,
    chain: str = "ethereum",
    subject: str | None = None,
    at: datetime | None = None,
    rule: float = 30.0,
    **features: float,
) -> int:
    when = at or T0 + timedelta(hours=n)
    base: dict[str, float] = dict.fromkeys(NUMERIC_FEATURES, 0.0)
    for key, value in features.items():
        base[key] = value
    with repo.transaction() as conn:
        pk = conn.execute(
            insert(schema.signals).values(
                signal_key=f"k{n}",
                detector_id="large_transfer_mad",
                detector_version="1",
                family="large_transfers",
                chain=chain,
                subject_kind="wallet",
                subject_ref=subject or f"0x{n:040x}",
                title="SYNTHETIC",
                severity="low",
                score=rule,
                confidence=0.1,
                status="open",
                explanation_json="{}",
                evidence_json="[]",
                coverage_json="{}",
                occurrences=1,
                observed_at=when,
                first_seen_at=when,
                last_seen_at=when,
            )
        ).inserted_primary_key
        assert pk is not None
        sid = int(pk[0])
        conn.execute(
            insert(schema.signal_feature_snapshots).values(
                signal_id=sid,
                feature_version="1",
                chain=chain,
                family="large_transfers",
                detector_id="large_transfer_mad",
                subject_ref=subject or f"0x{n:040x}",
                observed_at=when,
                rule_score=rule,
                features_json=json.dumps(base),
                created_at=when,
            )
        )
    return sid


def synthetic_dataset(repo: Repository, n: int = 160) -> dict[int, int]:
    """Labels that a feature predicts (magnitude) and the rule score half-predicts."""
    labels: dict[int, int] = {}
    for i in range(n):
        positive = i % 2 == 0
        chain = "ethereum" if i % 4 < 2 else "solana"
        sid = add(
            repo,
            i,
            chain=chain,
            rule=60.0 if positive and i % 3 else 40.0,
            magnitude=0.9 if positive else 0.1,
            prior_counterparties=float(i % 7),
        )
        labels[sid] = 1 if positive else 0
    return labels


# ------------------------------------------------------------------ snapshots are point in time


def test_a_snapshot_is_written_once_at_creation_from_data_before_the_event(
    engine: Engine,
) -> None:
    seed = Seed(engine)
    for i in range(15):  # a baseline for the large-transfer detector
        when = NOW - timedelta(days=10, hours=i)
        seed.tx(eth_tx(100 + i), eth_addr(0xA0), when, to=eth_addr(0xB0))
        seed.transfer(eth_tx(100 + i), eth_addr(0xA0), eth_addr(0xB0), 2 * E18, when)
    big = NOW - timedelta(hours=2)
    seed.tx(eth_tx(900), eth_addr(0xA0), big, to=eth_addr(0xB1))
    seed.transfer(eth_tx(900), eth_addr(0xA0), eth_addr(0xB1), 5000 * E18, big)
    settings = load_settings(data_root=Path("/unused"), env="test")
    svc = DiscoveryService(Repository(engine), settings, clock=lambda: NOW)
    svc.run(ETH)
    with engine.connect() as conn:
        snaps = conn.execute(select(schema.signal_feature_snapshots)).all()
    assert snaps, "creating a signal writes its snapshot"
    first = {int(s.signal_id): (s.features_json, s.rule_score) for s in snaps}
    later = NOW - timedelta(hours=1)  # data after the event must not leak into its snapshot
    seed.tx(eth_tx(901), eth_addr(0xA0), later, to=eth_addr(0xB2))
    seed.transfer(eth_tx(901), eth_addr(0xA0), eth_addr(0xB2), 9000 * E18, later)
    svc.run(ETH)  # refreshes or merges signals
    with engine.connect() as conn:
        again = {
            int(s.signal_id): (s.features_json, s.rule_score)
            for s in conn.execute(select(schema.signal_feature_snapshots)).all()
        }
    for sid, value in first.items():
        assert again[sid] == value  # never rewritten
    features = json.loads(next(iter(first.values()))[0])
    assert set(features) == set(NUMERIC_FEATURES)
    assert "status" not in features  # no outcome-like field is a feature


def test_anomaly_scores_only_use_earlier_candidates(engine: Engine) -> None:
    rows = [
        _Row(
            i, "ethereum", "f", f"s{i}", T0 + timedelta(hours=i), 30.0, {"magnitude": 0.1 * (i % 3)}
        )
        for i in range(30)
    ]
    before = anomaly_scores(rows)
    later_outlier = _Row(
        99, "ethereum", "f", "s99", T0 + timedelta(days=5), 30.0, {"magnitude": 50}
    )
    after = anomaly_scores([*rows, later_outlier])
    for i in range(30):
        assert after[i] == before[i]  # a later candidate never changes an earlier score
    assert before[0][0] is None  # too few earlier candidates: no score, not a zero
    assert after[99][0] is not None
    assert after[99][0] > (after[29][0] or 0)


def test_the_split_is_chronological_and_never_shares_a_subject(engine: Engine) -> None:
    rows = [
        _Row(i, "ethereum", "f", f"s{i % 9}", T0 + timedelta(hours=i), 30.0, {}) for i in range(90)
    ]
    train, val, test, info = entity_split(rows)
    subjects = [{r.subject for r in part} for part in (train, val, test)]
    assert not subjects[0] & subjects[1]
    assert not subjects[0] & subjects[2]
    assert not subjects[1] & subjects[2]
    assert len(train) + len(val) + len(test) == 90
    first = {
        s: min(r.observed_at for r in rows if r.subject == s) for s in {r.subject for r in rows}
    }
    assert max(first[s] for s in subjects[0]) <= min(first[s] for s in subjects[2])
    assert info["method"].startswith("chronological")


# ------------------------------------------------------------------ the gate and the evaluation


def test_without_verified_labels_the_ranker_is_not_ready_and_says_why(engine: Engine) -> None:
    rank, _review, repo = ranking(engine)
    repo.set_meta("data_origin", "live_network")
    for i in range(20):
        add(repo, i)
    status = rank.status()
    assert status["supervised"] == "not_ready"
    result = rank.evaluate("alice")
    assert result["status"] == "not_ready"
    assert result["experimental"] is False
    assert "test split" in result["reason"]
    assert rank.runs()[0]["status"] == "not_ready"  # the attempt is on record


def test_synthetic_labels_exercise_every_scorer_but_are_never_stored(engine: Engine) -> None:
    rank, _review, repo = ranking(engine, ranking_min_labels_per_class=10)
    labels = synthetic_dataset(repo)
    result = rank.evaluate("tests", labels=labels)
    assert result["experimental"] is True
    assert result["labels_source"] == "synthetic (test only)"
    assert result["status"] == "completed"
    scorers = result["scorers"]
    assert set(scorers) == {"rule_score", "anomaly", "supervised"}
    sup = scorers["supervised"]
    for key in (
        "precision",
        "recall",
        "f1",
        "pr_auc",
        "precision_at_10",
        "brier",
        "false_positives",
        "per_chain",
        "calibration_bins",
    ):
        assert key in sup
    assert set(sup["per_chain"]) <= {"ethereum", "solana"}
    # the supervised model learns the planted signal and beats the half-informative rule score
    assert (sup["pr_auc"] or 0) >= (scorers["rule_score"]["pr_auc"] or 0)
    assert result["model"]["kind"] == "logistic_regression"
    assert rank.runs() == []  # experimental results are never persisted


def test_evaluations_are_deterministic(engine: Engine) -> None:
    rank, _review, repo = ranking(engine, ranking_min_labels_per_class=10)
    labels = synthetic_dataset(repo, 80)
    a = rank.evaluate("t", labels=labels)
    b = rank.evaluate("t", labels=labels)
    assert a["dataset_hash"] == b["dataset_hash"]
    assert a["scorers"] == b["scorers"]
    assert a["model"] == b["model"]


def test_real_ground_truth_drives_a_persisted_run(engine: Engine) -> None:
    rank, review, repo = ranking(engine, ranking_min_labels_per_class=10)
    repo.set_meta("data_origin", "live_network")
    labels = synthetic_dataset(repo, 60)  # features only; the labels below are human decisions
    for sid, y in labels.items():
        if y:
            review.decide(sid, "confirmed", "evidence_sufficient", None, "alice")
        else:
            review.decide(sid, "rejected", "false_positive", None, "alice")
    result = rank.evaluate("alice")
    assert result["experimental"] is False
    assert result["labelled"] == 60
    assert result["status"] == "completed"
    run = rank.run(result["run_id"])
    assert run["dataset_hash"] == result["dataset_hash"]
    assert run["scorers"]["rule_score"]["kind"] == "baseline"
    explanation = rank.explain(next(iter(labels)))
    assert explanation["available"] is True
    assert "authority" in explanation


def test_disputed_and_machine_decisions_never_train_the_ranker(engine: Engine) -> None:
    rank, review, repo = ranking(engine)
    repo.set_meta("data_origin", "live_network")
    a, b = add(repo, 1), add(repo, 2)
    review.decide(a, "confirmed", "evidence_sufficient", None, "alice")
    review.decide(a, "rejected", "false_positive", None, "bob")  # disputed
    review.decide(b, "confirmed", "evidence_sufficient", None, "system:auto")
    assert rank.evaluate("x", persist=False)["labelled"] == 0


# ------------------------------------------------------------------ metric helpers


def test_metric_helpers_match_hand_computed_values() -> None:
    scored = [(0.9, 1), (0.8, 0), (0.7, 1), (0.1, 0)]
    assert average_precision(scored) == pytest.approx((1 + 2 / 3) / 2, abs=1e-4)
    assert average_precision([(0.5, 0)]) is None  # no positives: undefined, not zero
    assert choose_threshold(scored) in {0.7, 0.9}
    m = evaluate_scores([(s, y, "ethereum") for s, y in scored], 0.7)
    assert (m["precision"], m["recall"]) == (pytest.approx(2 / 3, abs=1e-4), 1.0)
    assert m["false_positives"] == 1
    assert m["precision_at_10"] == 0.5


def test_ranking_api_status_evaluate_runs_and_explanations(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from tests.security.helpers import API, Rig

    rig = Rig(tmp_path, mode="disabled")
    sid = add(rig.repo, 1)
    with TestClient(rig.app, base_url="http://127.0.0.1:8000") as c:
        status = c.get(f"{API}/ml/signal-ranking").json()
        assert status["supervised"] == "not_ready"
        run = c.post(f"{API}/ml/signal-ranking/evaluate")
        assert run.status_code == 201
        assert run.json()["status"] == "not_ready"
        runs = c.get(f"{API}/ml/signal-ranking/runs").json()["items"]
        assert len(runs) == 1
        assert c.get(f"{API}/ml/signal-ranking/runs/{runs[0]['id']}").status_code == 200
        explained = c.get(f"{API}/signals/{sid}/ranking").json()
        assert explained["available"] is True
        assert explained["supervised"]["status"] == "not_ready"
        with rig.engine.connect() as conn:  # the API never writes a decision on its own
            assert conn.execute(text("SELECT COUNT(*) FROM signal_decisions")).scalar_one() == 0
    rig.dispose()


def test_a_chain_that_passes_the_gate_but_has_no_usable_training_slice_is_not_ready(
    engine: Engine,
) -> None:
    """Labels exist in total, but the chain's subjects all appear late, so none fall in the
    chronological training split: report not ready instead of crashing."""
    rank, _review, repo = ranking(engine, ranking_min_labels_per_class=10)
    labels: dict[int, int] = {}
    for i in range(60):  # early: ethereum only
        labels[add(repo, i, chain="ethereum", magnitude=0.9 if i % 2 else 0.1)] = i % 2
    for i in range(60, 100):  # late: solana only, enough labels of both classes in total
        labels[add(repo, i, chain="solana", magnitude=0.9 if i % 2 else 0.1)] = i % 2
    result = rank.evaluate("tests", labels=labels)
    assert result["status"] == "completed"  # no crash
    sup = result["scorers"]["supervised"]
    model: dict[str, Any] = result.get("model") or {}
    assert "solana" not in model.get("chains", [])
    assert result["gate"]["chains"]["solana"]["train_ready"] is False
    assert sup["status"] in ("evaluated", "not_ready")


def test_missing_features_are_never_scored_as_zero() -> None:
    """CLAUDE.md principle 8: a feature with no data is missing, not 0."""
    from bip.ml.signal_ranking import _vector, predict, train_logistic  # pyright: ignore[reportPrivateUsage]

    vec = _vector({"magnitude": 0.5})  # every other feature absent
    assert vec[NUMERIC_FEATURES.index("magnitude")] == 0.5
    assert vec[NUMERIC_FEATURES.index("subject_age_days")] is None
    # anomaly scores ignore a feature the candidate lacks instead of comparing a fake 0
    rows = [
        _Row(
            i,
            "ethereum",
            "f",
            f"s{i}",
            T0 + timedelta(hours=i),
            30.0,
            {"magnitude": 0.1, "subject_age_days": 900.0},
        )
        for i in range(20)
    ]
    newcomer = _Row(99, "ethereum", "f", "s99", T0 + timedelta(days=3), 30.0, {"magnitude": 0.1})
    score, top = anomaly_scores([*rows, newcomer])[99]
    assert score is not None
    assert all(t["feature"] != "subject_age_days" for t in top)
    assert score < 0.2  # typical on what it has; a 0 age would have looked extreme
    # the logistic model imputes the training mean (zero contribution), never a literal 0
    x = [_vector({"magnitude": float(i % 2), "subject_age_days": 500.0}) for i in range(20)]
    model = train_logistic(x, [i % 2 for i in range(20)])
    _, contributions = predict(model, {"magnitude": 1.0})
    age = [c for c in contributions if c["feature"] == "subject_age_days"]
    assert not age or age[0]["contribution"] == 0


def test_a_subject_with_no_earlier_history_has_no_age(engine: Engine) -> None:
    from bip.ml.signal_ranking import write_snapshot

    repo = Repository(engine)
    sid = add(repo, 1)
    with repo.transaction() as conn:
        conn.execute(text("DELETE FROM signal_feature_snapshots"))
        write_snapshot(
            conn,
            sid,
            chain="ethereum",
            family="f",
            detector_id="d",
            subject_ref="0x" + "ab" * 20,
            observed_at=T0,
            magnitude=0.5,
            baseline_reliability=0.5,
            confidence_cap=0.5,
            evidence_txs=1,
            rule_score=30.0,
            rule_confidence=0.2,
        )
        features = json.loads(
            conn.execute(text("SELECT features_json FROM signal_feature_snapshots")).scalar_one()
        )
    assert features["subject_age_days"] is None  # unknown, not "0 days old"


def test_a_later_run_without_a_model_does_not_hide_an_earlier_model(engine: Engine) -> None:
    rank, _review, repo = ranking(engine)
    sid = add(repo, 1)
    model = {
        "kind": "logistic_regression",
        "features": list(NUMERIC_FEATURES),
        "weights": [0.0] * len(NUMERIC_FEATURES),
        "bias": 0.0,
        "means": [0.0] * len(NUMERIC_FEATURES),
        "stds": [1.0] * len(NUMERIC_FEATURES),
        "chains": ["ethereum"],
    }
    with repo.transaction() as conn:
        summaries: list[dict[str, Any]] = [{"model": model}, {"scorers": {}}]
        for summary in summaries:
            conn.execute(
                insert(schema.signal_ranking_runs).values(
                    created_at=T0,
                    actor="t",
                    status="completed",
                    feature_version="1",
                    ranker_version="1",
                    dataset_hash="h",
                    summary_json=json.dumps(summary),
                )
            )
    assert rank.explain(sid)["supervised"]["status"] == "available"


def test_tied_scores_are_ranked_pessimistically_and_independently_of_input_order() -> None:
    a = [(0.5, 1), (0.5, 0), (0.9, 1)]
    b = [(0.5, 0), (0.5, 1), (0.9, 1)]
    assert average_precision(a) == average_precision(b)
    # within a tie the negative counts first, so a tie never flatters a scorer
    assert average_precision([(0.5, 1), (0.5, 0)]) == 0.5
    m1 = evaluate_scores([(s, y, "ethereum") for s, y in a], 0.6)
    m2 = evaluate_scores([(s, y, "ethereum") for s, y in b], 0.6)
    assert m1["precision_at_10"] == m2["precision_at_10"]
