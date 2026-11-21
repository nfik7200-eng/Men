"""Model drift: PSI and robust shift on stored feature snapshots, and honest 'no verdict'."""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert

from bip.config import load_settings
from bip.db import schema
from bip.db.engine import create_db_engine
from bip.db.errors import RecordNotFoundError
from bip.db.repository import Repository
from bip.ml.drift import (
    MIN_SAMPLES,
    drift_report,
    population_stability_index,
    robust_shift,
)
from tests.security.helpers import API, Rig

CREATED = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    e = create_db_engine(load_settings(data_root=tmp_path / "data", env="test"))
    yield e
    e.dispose()


def model(
    engine: Engine, *, status: str = "trained", data_kind: str = "stored_observations"
) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(schema.ml_models).values(
                name="wallet-class",
                version="1.0",
                kind="classifier",
                chain="ethereum",
                status=status,
                feature_version="1",
                taxonomy_version="1",
                data_kind=data_kind,
                params_json="{}",
                labels_json="[]",
                thresholds_json="{}",
                created_at=CREATED,
            )
        )


_counter = [0]


def snapshots(
    engine: Engine,
    n: int,
    when: datetime,
    values: Callable[[int], float],
    *,
    data_kind: str = "stored_observations",
    missing_every: int = 0,
) -> None:
    rows: list[dict[str, Any]] = []
    for i in range(n):
        _counter[0] += 1
        v = None if missing_every and i % missing_every == 0 else values(i)
        rows.append(
            {
                "chain": "ethereum",
                "address": f"0x{_counter[0]:040x}",
                "feature_version": "1",
                "as_of": when,
                "digest": f"{_counter[0]:064x}",
                "data_kind": data_kind,
                "payload_json": json.dumps({"values": {"tx_count": v}}),
                "created_at": when,
            }
        )
    with engine.begin() as conn:
        conn.execute(insert(schema.wallet_features), rows)


def gauss(seed: int, mean: float, sd: float) -> Callable[[int], float]:
    rng = random.Random(seed)  # noqa: S311
    cache: dict[int, float] = {}

    def value(i: int) -> float:
        return cache.setdefault(i, rng.gauss(mean, sd))

    return value


# ------------------------------------------------------------------ statistics


def test_psi_is_near_zero_for_the_same_distribution_and_large_for_a_shift() -> None:
    rng = random.Random(1)  # noqa: S311
    ref = [rng.gauss(10, 2) for _ in range(500)]
    same = [rng.gauss(10, 2) for _ in range(500)]
    shifted = [rng.gauss(14, 2) for _ in range(500)]
    assert (population_stability_index(ref, same) or 0) < 0.1
    assert (population_stability_index(ref, shifted) or 0) > 0.25


def test_psi_handles_constant_and_empty_inputs() -> None:
    assert population_stability_index([], [1.0]) is None
    assert population_stability_index([1.0], []) is None
    assert population_stability_index([5.0] * 50, [5.0] * 50) == 0.0
    assert (population_stability_index([5.0] * 50, [9.0] * 50) or 0) > 1.0


def test_robust_shift() -> None:
    ref = [float(x) for x in range(1, 100)]
    assert robust_shift(ref, ref) == 0.0
    assert (robust_shift(ref, [x + 100 for x in ref]) or 0) > 2
    assert robust_shift([], ref) is None
    assert robust_shift([3.0] * 10, [3.0] * 10) == 0.0
    assert robust_shift([3.0] * 10, [4.0] * 10) is None  # zero spread: no meaningful scale


# ------------------------------------------------------------------ report


def test_stable_inputs_are_reported_stable(engine: Engine) -> None:
    model(engine)
    snapshots(engine, 120, CREATED - timedelta(days=2), gauss(1, 10, 2))
    snapshots(engine, 120, CREATED + timedelta(days=2), gauss(2, 10, 2))
    report = drift_report(Repository(engine), "wallet-class", "1.0")
    assert report["status"] == "stable"
    feature = report["features"][0]
    assert feature["feature"] == "tx_count"
    assert feature["psi"] < 0.1
    assert feature["verdict"] == "stable"
    assert report["reference"]["wallets"] == report["current"]["wallets"] == 120


def test_shifted_inputs_are_reported_drifted(engine: Engine) -> None:
    model(engine)
    snapshots(engine, 120, CREATED - timedelta(days=2), gauss(1, 10, 2))
    snapshots(engine, 120, CREATED + timedelta(days=2), gauss(2, 16, 2))
    report = drift_report(Repository(engine), "wallet-class", "1.0")
    assert report["status"] == "drifted"
    assert report["features"][0]["psi"] > 0.25
    assert report["features"][0]["robust_shift"] > 1
    assert any("not measure accuracy" in c for c in report["caveats"])


def test_too_few_samples_give_no_verdict(engine: Engine) -> None:
    model(engine)
    snapshots(engine, 120, CREATED - timedelta(days=2), gauss(1, 10, 2))
    snapshots(engine, MIN_SAMPLES - 1, CREATED + timedelta(days=2), gauss(2, 99, 2))
    report = drift_report(Repository(engine), "wallet-class", "1.0")
    assert report["status"] == "insufficient_data"
    assert report["features"][0]["verdict"] == "insufficient_data"
    assert report["features"][0]["psi"] is None


def test_missing_values_are_reported_not_turned_into_zero(engine: Engine) -> None:
    model(engine)
    snapshots(engine, 120, CREATED - timedelta(days=2), gauss(1, 10, 2))
    snapshots(engine, 120, CREATED + timedelta(days=2), gauss(2, 10, 2), missing_every=3)
    feature = drift_report(Repository(engine), "wallet-class", "1.0")["features"][0]
    assert feature["missing_share_reference"] == 0
    assert 0.3 < feature["missing_share_current"] < 0.4
    assert feature["current_n"] == 80  # only real values entered the comparison
    assert feature["verdict"] == "stable"


def test_only_the_newest_snapshot_per_wallet_counts(engine: Engine) -> None:
    model(engine)
    snapshots(engine, 60, CREATED - timedelta(days=2), gauss(1, 10, 2))
    report = drift_report(Repository(engine), "wallet-class", "1.0")
    assert report["reference"]["wallets"] == 60


def test_synthetic_models_say_their_drift_is_not_evidence(engine: Engine) -> None:
    model(engine, status="experimental_synthetic", data_kind="synthetic_demo")
    report = drift_report(Repository(engine), "wallet-class", "1.0")
    assert any("synthetic" in c for c in report["caveats"])


def test_unknown_model(engine: Engine) -> None:
    with pytest.raises(RecordNotFoundError):
        drift_report(Repository(engine), "wallet-class", "9.9")


def test_drift_endpoint(tmp_path: Path) -> None:
    rig = Rig(tmp_path, mode="disabled")
    model(rig.engine)
    snapshots(rig.engine, 100, CREATED - timedelta(days=1), gauss(1, 10, 2))
    snapshots(rig.engine, 100, CREATED + timedelta(days=1), gauss(2, 18, 2))
    try:
        with TestClient(rig.app) as c:
            ok = c.get(f"{API}/ml/models/wallet-class/1.0/drift")
            assert ok.status_code == 200
            assert ok.json()["status"] == "drifted"
            assert c.get(f"{API}/ml/models/wallet-class/9.9/drift").status_code == 404
            assert c.get(f"{API}/ml/models/BAD NAME/1.0/drift").status_code == 422
    finally:
        rig.dispose()


def test_error_rates_at_the_minimum_sample_size_are_pinned() -> None:
    """Regression: uncorrected PSI called every comparison drifted at small samples."""
    from bip.ml.drift import psi_details

    false_drift = detected = 0
    runs = 200
    for seed in range(runs):
        rng = random.Random(seed)  # noqa: S311
        ref = [rng.gauss(10, 2) for _ in range(MIN_SAMPLES)]
        same = [rng.gauss(10, 2) for _ in range(MIN_SAMPLES)]
        shifted = [rng.gauss(13, 2) for _ in range(MIN_SAMPLES)]
        false_drift += (psi_details(ref, same) or (0, 0, 0))[0] >= 0.25
        detected += (psi_details(ref, shifted) or (0, 0, 0))[0] >= 0.25
    assert false_drift / runs <= 0.03
    assert detected / runs >= 0.95
