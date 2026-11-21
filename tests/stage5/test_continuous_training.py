"""Controlled continuous retraining: a challenger replaces the served model only when it is
not worse; everything is decided on local data with a fake clock."""

from __future__ import annotations

import ast
import json
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import bip.ml.continuous as continuous_module
from bip.db import schema
from bip.ml.continuous import (
    ContinuousTraining,
    decision_key,
    examples_key,
    gate,
)
from bip.ml.training import Challenger
from bip.ops.jobs import build_jobs
from tests.stage3.builders import a
from tests.stage3.test_intel_api import Env
from tests.stage5.helpers import API, J, env_with

BASE = datetime(2026, 1, 1, tzinfo=UTC)
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
LABEL = "high_frequency_activity"


def seed(e: Env, c: TestClient, indexes: range) -> None:
    """Real, human-reviewed labels with enough stored activity for features."""
    from tests.stage3.test_findings_opportunities import Seeder

    labels: list[J] = []
    with e.engine.begin() as conn:
        s = Seeder(conn)
        s.n = indexes.start * 1000  # tx ids stay unique across calls
        for i in indexes:
            w, busy, at = a(5000 + i), i % 2 == 0, BASE + timedelta(days=i)
            for k in range(30 if busy else 3):
                s.tx(at - timedelta(minutes=5 * k if busy else 3000 * k), w, a(9))
            labels.append(
                {
                    "chain": "ethereum",
                    "address": w,
                    "label": LABEL,
                    "value": busy,
                    "status": "analyst_reviewed",
                    "source": "test-review",
                    "observed_at": (at + timedelta(minutes=1)).isoformat(),
                }
            )
    assert c.post(f"{API}/ml/labels", json={"labels": labels}).status_code == 201


class FakeRanking:
    def __init__(self, counts: dict[str, dict[str, int]]) -> None:
        self.counts = counts
        self.evaluations: list[str] = []

    def label_counts(self) -> dict[str, dict[str, int]]:
        return self.counts

    def evaluate(self, actor: str) -> dict[str, Any]:
        self.evaluations.append(actor)
        return {"status": "completed", "run_id": 9, "labelled": 3}


@contextmanager
def rig(
    tmp_path: Path, min_new: int = 5, **ml: Any
) -> Generator[tuple[Env, TestClient, Callable[..., ContinuousTraining]]]:
    e = env_with(
        tmp_path,
        min_labelled_wallets=10,
        min_positives_per_label=3,
        continuous_min_new_examples=min_new,
        **ml,
    )
    try:
        with TestClient(e.app) as c:
            ctx = e.app.state.bip

            def make(
                ranking: Any = None, cls: type[ContinuousTraining] = ContinuousTraining
            ) -> ContinuousTraining:
                return cls(
                    ctx.repo,
                    ctx.settings,
                    ctx.trainer,
                    ctx.classifier,
                    ranking or FakeRanking({}),
                    clock=lambda: NOW,
                )

            yield e, c, make
    finally:
        e.engine.dispose()


def meta(e: Env, key: str) -> str | None:
    return e.app.state.bip.repo.get_meta(key)


def served(c: TestClient) -> str:
    return str(c.get(f"{API}/ml/models").json()["active"]["ethereum"]["version"])


def model_count(e: Env) -> int:
    with e.engine.begin() as conn:
        return int(conn.execute(select(func.count()).select_from(schema.ml_models)).scalar_one())


def metrics(pr: float, brier: float) -> dict[str, J]:
    return {LABEL: {"pr_auc": pr, "brier": brier}}


# ---------------------------------------------------------------- the promotion gate


def test_gate_promotes_when_both_conditions_hold() -> None:
    ok, why = gate(metrics(0.9, 0.10), metrics(0.9, 0.10), 0.005)
    assert ok, why


def test_gate_rejects_a_lower_pr_auc() -> None:
    ok, why = gate(metrics(0.80, 0.10), metrics(0.90, 0.10), 0.005)
    assert not ok
    assert "PR-AUC" in why


def test_gate_brier_tolerance_boundary() -> None:
    assert gate(metrics(0.9, 0.104), metrics(0.9, 0.10), 0.005)[0]
    assert not gate(metrics(0.9, 0.106), metrics(0.9, 0.10), 0.005)[0]


def test_gate_compares_macro_means_over_common_labels_only() -> None:
    champ = {"x": {"pr_auc": 0.5, "brier": 0.2}, "y": {"pr_auc": 0.9, "brier": 0.1}}
    chall = {"y": {"pr_auc": 0.9, "brier": 0.1}, "z": {"pr_auc": 0.1, "brier": 0.9}}
    assert gate(chall, champ, 0.005)[0]  # only y is comparable


def test_gate_rejects_when_nothing_is_comparable() -> None:
    ok, why = gate(metrics(0.9, 0.1), {"other": {"pr_auc": 0.9, "brier": 0.1}}, 0.005)
    assert not ok
    assert "comparable" in why


# ---------------------------------------------------------------- wallet classifier cycle


def test_too_few_new_examples_skips_and_records_the_reason(tmp_path: Path) -> None:
    with rig(tmp_path, min_new=20) as (e, c, make):
        seed(e, c, range(12))
        d = make().run_wallet("ethereum")
        assert d["decision"] == "skipped"
        assert "12 new" in d["reason"]
        assert model_count(e) == 0
        stored = json.loads(meta(e, decision_key("ethereum")) or "{}")
        assert stored["decision"] == "skipped"
        assert stored["time"] == NOW.isoformat()
        assert meta(e, examples_key("ethereum")) is None  # a skip is not a decision point


def test_no_champion_promotes_when_the_data_gate_passes(tmp_path: Path) -> None:
    with rig(tmp_path) as (e, c, make):
        seed(e, c, range(24))
        d = make().run_wallet("ethereum")
        assert d["decision"] == "promoted", d
        assert d["reason"] == "no champion"
        assert d["champion"] is None
        assert d["examples"] == 24
        assert d["filters"]["input"] == 24
        assert d["filters"]["filters_version"] == "1"
        assert d["challenger"]["metrics"]["labels"] == [LABEL]
        assert served(c) == d["model"]["version"] == "1.0.0"
        assert meta(e, examples_key("ethereum")) == "24"
        assert json.loads(meta(e, decision_key("ethereum")) or "{}") == d


def test_data_gate_failure_registers_nothing(tmp_path: Path) -> None:
    with rig(tmp_path, min_new=1) as (e, c, make):
        seed(e, c, range(4))  # below min_labelled_wallets
        d = make().run_wallet("ethereum")
        assert d["decision"] == "skipped"
        assert "labelled wallet" in d["reason"]
        assert model_count(e) == 0


def test_a_second_cycle_without_new_examples_skips(tmp_path: Path) -> None:
    with rig(tmp_path) as (e, c, make):
        seed(e, c, range(24))
        make().run_wallet("ethereum")
        again = make().run_wallet("ethereum")
        assert again["decision"] == "skipped"
        assert "0 new" in again["reason"]
        assert model_count(e) == 1


class WorseChampion(ContinuousTraining):
    def score_champion(self, champion: J, challenger: Challenger) -> dict[str, J]:
        return metrics(0.0, 0.9)


class BetterChampion(ContinuousTraining):
    def score_champion(self, champion: J, challenger: Challenger) -> dict[str, J]:
        return metrics(2.0, 0.0)  # beats any real challenger, even on this easy fixture


def test_a_better_challenger_is_promoted_and_served(tmp_path: Path) -> None:
    with rig(tmp_path) as (e, c, make):
        seed(e, c, range(24))
        make().run_wallet("ethereum")
        seed(e, c, range(24, 32))
        d = make(cls=WorseChampion).run_wallet("ethereum")
        assert d["decision"] == "promoted", d
        assert d["champion"]["version"] == "1.0.0"
        assert d["champion"]["metrics"]["macro_pr_auc"] == 0.0
        assert served(c) == "1.0.1"
        assert model_count(e) == 2


def test_a_worse_challenger_is_rejected_and_never_served(tmp_path: Path) -> None:
    with rig(tmp_path) as (e, c, make):
        seed(e, c, range(24))
        make().run_wallet("ethereum")
        seed(e, c, range(24, 32))
        d = make(cls=BetterChampion).run_wallet("ethereum")
        assert d["decision"] == "rejected", d
        assert "PR-AUC" in d["reason"]
        assert served(c) == "1.0.0"
        assert model_count(e) == 1  # the challenger never entered ml_models
        runs = c.get(f"{API}/ml/training-runs").json()["items"]
        newest = runs[0]
        assert newest["status"] == "succeeded"
        assert newest["message"] == "challenger not promoted"
        stored = newest["dataset"]
        assert stored["challenger_metrics"]["test"][LABEL]["pr_auc"] is not None
        assert stored["filters"]["input"] == 32
        assert meta(e, examples_key("ethereum")) == "32"  # a rejection is a decision too
        # a rejected challenger is not served for classification either
        out = c.post(f"{API}/ml/classify/ethereum/{a(5000)}").json()
        assert all("1.0.1" not in json.dumps(x) for x in out["behavioural_roles"])


def test_a_different_feature_version_is_incomparable_and_promotes(tmp_path: Path) -> None:
    with rig(tmp_path) as (e, c, make):
        seed(e, c, range(24))
        make().run_wallet("ethereum")
        with e.engine.begin() as conn:
            conn.execute(schema.ml_models.update().values(feature_version="0"))
        seed(e, c, range(24, 32))
        d = make(cls=BetterChampion).run_wallet("ethereum")  # would reject if comparable
        assert d["decision"] == "promoted", d
        assert d["reason"] == "feature version changed"
        assert served(c) == "1.0.1"


def test_the_real_champion_is_rescored_on_the_challenger_test_split(tmp_path: Path) -> None:
    with rig(tmp_path) as (e, c, make):
        seed(e, c, range(24))
        make().run_wallet("ethereum")
        seed(e, c, range(24, 32))
        d = make().run_wallet("ethereum")
        assert d["decision"] in {"promoted", "rejected"}
        champ = d["champion"]["metrics"]
        assert champ["labels"] == [LABEL]
        assert champ["macro_pr_auc"] is not None
        assert champ["brier"] is not None
        assert served(c) == ("1.0.1" if d["decision"] == "promoted" else "1.0.0")


# ---------------------------------------------------------------- signal ranking


def test_ranking_runs_once_when_enough_new_human_outcomes(tmp_path: Path) -> None:
    with rig(tmp_path, min_new=5) as (e, _c, make):
        ranking = FakeRanking(
            {"ethereum": {"positive": 4, "negative": 3}, "solana": {"positive": 1, "negative": 1}}
        )
        out = make(ranking).run_ranking(["ethereum", "solana"])
        assert [d["decision"] for d in out] == ["evaluated", "skipped"]
        assert ranking.evaluations == ["continuous-training"]
        eth = json.loads(meta(e, decision_key("ethereum", "signal_ranking")) or "{}")
        assert eth["run_id"] == 9
        assert eth["examples"] == 7
        assert meta(e, examples_key("ethereum", "signal_ranking")) == "7"
        assert meta(e, examples_key("solana", "signal_ranking")) is None
        again = make(ranking).run_ranking(["ethereum"])
        assert again[0]["decision"] == "skipped"
        assert ranking.evaluations == ["continuous-training"]


# ---------------------------------------------------------------- the scheduler job


def job_names(e: Env) -> dict[str, Any]:
    ctx = e.app.state.bip
    return {j.name: j for j in build_jobs(ctx, ctx.backups)}


def test_the_job_is_not_registered_when_disabled(tmp_path: Path) -> None:
    with rig(tmp_path) as (e, _c, _make):
        assert "ml_continuous_training" not in job_names(e)


def test_the_job_is_registered_when_enabled_and_never_contacts_providers(tmp_path: Path) -> None:
    with rig(tmp_path, continuous_training_enabled=True, continuous_interval_hours=3) as (
        e,
        c,
        _make,
    ):
        job = job_names(e)["ml_continuous_training"]
        assert job.interval == timedelta(hours=3)
        assert job.contacts_providers is False
        seed(e, c, range(24))
        before = len(e.transport.requests)
        out = job.run()
        assert "ethereum: promoted" in out
        assert len(e.transport.requests) == before  # no provider call


def test_the_job_respects_the_training_lock(tmp_path: Path) -> None:
    with rig(tmp_path, continuous_training_enabled=True) as (e, c, _make):
        job = job_names(e)["ml_continuous_training"]
        seed(e, c, range(24))
        ctx = e.app.state.bip
        assert ctx.train_lock.acquire(blocking=False)
        try:
            assert job.run().startswith("skipped: a training run is in progress")
        finally:
            ctx.train_lock.release()
        assert model_count(e) == 0


def test_the_continuous_module_imports_no_provider_code() -> None:
    tree = ast.parse(Path(continuous_module.__file__).read_text())
    mods: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {x.name for x in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module)
    forbidden = ("bip.providers", "bip.router", "bip.rpc", "bip.ingest", "bip.acquisition")
    assert not [m for m in mods if m.startswith(forbidden)], mods


@pytest.mark.parametrize("bad", ["", "x", "-3"])
def test_a_garbled_stored_count_counts_as_zero(tmp_path: Path, bad: str) -> None:
    with rig(tmp_path, min_new=20) as (e, c, make):
        seed(e, c, range(12))
        e.app.state.bip.repo.set_meta(examples_key("ethereum"), bad)
        d = make().run_wallet("ethereum")
        assert "12 new" in d["reason"]
