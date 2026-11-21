"""The wallet trainer applies the training-data filters before the data gate."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from bip.db import schema
from bip.ml.training import Trainer
from tests.stage3.builders import a
from tests.stage5.helpers import API, J, env_with

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def label(i: int, **over: Any) -> J:
    row: J = {
        "chain": "ethereum",
        "address": a(7000 + i),
        "label": "high_frequency_activity",
        "value": True,
        "status": "analyst_reviewed",
        "source": "test-review",
        "observed_at": (BASE + timedelta(days=i)).isoformat(),
    }
    return {**row, **over}


def seed_activity(e: Any, indexes: list[int]) -> None:
    from tests.stage3.test_findings_opportunities import Seeder

    with e.engine.begin() as conn:
        s = Seeder(conn)
        for i in indexes:
            at = BASE + timedelta(days=i)
            for k in range(10 + i):
                s.tx(at - timedelta(minutes=5 * k), a(7000 + i), a(9))


def trainer(e: Any) -> Trainer:
    return e.app.state.bip.trainer


def test_a_stored_machine_label_is_excluded_from_training(tmp_path: Path) -> None:
    e = env_with(tmp_path)
    try:
        with TestClient(e.app) as c:
            seed_activity(e, [0, 1])
            labels = [label(0), label(1, source="signal")]
            assert c.post(f"{API}/ml/labels", json={"labels": labels}).status_code == 201
            t = trainer(e)
            examples = t._stored("ethereum")  # pyright: ignore[reportPrivateUsage]
            assert len(examples) == 2
            kept, report = t._filtered(examples)  # pyright: ignore[reportPrivateUsage]
            assert [x.fv.address for x in kept] == [a(7000)]
            assert report["steps"][0]["name"] == "human_verified"
            assert report["steps"][0]["reasons"] == {"machine_origin": 1}
    finally:
        e.engine.dispose()


def test_a_reviewer_accepted_weak_label_trains_as_analyst_reviewed(tmp_path: Path) -> None:
    e = env_with(tmp_path)
    try:
        with TestClient(e.app) as c:
            seed_activity(e, [0])
            c.post(f"{API}/ml/labels", json={"labels": [label(0, status="weak")]})
            items = c.get(f"{API}/ml/labels").json()["items"]
            r = c.post(
                f"{API}/ml/labels/{items[0]['id']}/reviews",
                json={"decision": "accepted", "reviewer": "alice"},
            )
            assert r.status_code == 201
            t = trainer(e)
            kept, report = t._filtered(t._stored("ethereum"))  # pyright: ignore[reportPrivateUsage]
            assert report["output"] == 1
            assert len(kept) == 1
            assert kept[0].statuses["high_frequency_activity"] == "analyst_reviewed"
            assert kept[0].sources["high_frequency_activity"] == "test-review"
    finally:
        e.engine.dispose()


def test_an_unaccepted_weak_label_never_reaches_the_filters(tmp_path: Path) -> None:
    e = env_with(tmp_path)
    try:
        with TestClient(e.app) as c:
            seed_activity(e, [0])
            c.post(f"{API}/ml/labels", json={"labels": [label(0, status="weak")]})
            assert trainer(e)._stored("ethereum") == []  # pyright: ignore[reportPrivateUsage]
    finally:
        e.engine.dispose()


def test_an_example_with_too_few_features_is_excluded_and_counted(tmp_path: Path) -> None:
    e = env_with(tmp_path)
    try:
        with TestClient(e.app) as c:
            seed_activity(e, [0])  # wallet 1 has no stored observations at all
            c.post(f"{API}/ml/labels", json={"labels": [label(0), label(1)]})
            t = trainer(e)
            kept, report = t._filtered(t._stored("ethereum"))  # pyright: ignore[reportPrivateUsage]
            assert [x.fv.address for x in kept] == [a(7000)]
            step = next(s for s in report["steps"] if s["name"] == "feature_completeness")
            assert step["reasons"] == {"too_many_missing": 1}
    finally:
        e.engine.dispose()


def test_the_report_is_persisted_on_the_run_before_the_data_gate(tmp_path: Path) -> None:
    e = env_with(tmp_path)  # default gate needs 30 wallets: this run is insufficient_data
    try:
        with TestClient(e.app) as c:
            seed_activity(e, [0, 1])
            c.post(f"{API}/ml/labels", json={"labels": [label(0), label(1, source="signal")]})
            r = c.post(
                f"{API}/ml/training-runs", json={"chain": "ethereum", "source": "stored_labels"}
            ).json()
            assert r["status"] == "insufficient_data"
            assert "1 distinct labelled wallet" in r["message"]  # the gate counted filtered data
            t = schema.ml_training_runs
            with e.engine.begin() as conn:
                raw = conn.execute(
                    select(t.c.dataset_json).where(t.c.id == r["training_run_id"])
                ).scalar_one()
            filters = json.loads(raw)["filters"]
            assert filters["input"] == 2
            assert filters["output"] == 1
            assert filters["filters_version"] == "1"
    finally:
        e.engine.dispose()


def test_the_synthetic_path_does_not_run_the_filters(tmp_path: Path) -> None:
    e = env_with(tmp_path, allow_synthetic_training=True)
    try:
        with TestClient(e.app) as c:
            r = c.post(
                f"{API}/ml/training-runs", json={"chain": "ethereum", "source": "synthetic"}
            ).json()
            assert r["dataset"]["filters"] == "not_applied_synthetic"
    finally:
        e.engine.dispose()
