"""Detector performance: analyst review as a false-positive proxy, withheld until meaningful."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert

from bip.config import load_settings
from bip.db import schema
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.discovery.service import MIN_REVIEWED, DiscoveryService
from tests.discovery.seed import NOW


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    e = create_db_engine(load_settings(data_root=tmp_path / "data", env="test"))
    yield e
    e.dispose()


def add(engine: Engine, detector: str, status: str, n: int) -> None:
    with engine.begin() as conn:
        for i in range(n):
            conn.execute(
                insert(schema.signals).values(
                    signal_key=f"{detector}-{status}-{i}",
                    detector_id=detector,
                    detector_version="1",
                    family="f",
                    chain="ethereum",
                    subject_kind="wallet",
                    subject_ref=f"0x{i}",
                    title="t",
                    severity="low",
                    confidence=0.1,
                    score=1.0,
                    status=status,
                    explanation_json="{}",
                    evidence_json="[]",
                    coverage_json="{}",
                    observed_at=NOW,
                    first_seen_at=NOW,
                    last_seen_at=NOW,
                    occurrences=1,
                )
            )


def service(engine: Engine) -> DiscoveryService:
    settings = load_settings(data_root=Path("/unused"), env="test")
    return DiscoveryService(Repository(engine), settings, clock=lambda: NOW)


def test_rate_is_withheld_until_enough_signals_were_reviewed(engine: Engine) -> None:
    add(engine, "activity_burst", "dismissed", 3)
    add(engine, "activity_burst", "promoted", 1)
    row = next(
        d for d in service(engine).performance()["detectors"] if d["detector"] == "activity_burst"
    )
    assert row["reviewed"] == 4
    assert row["dismissal_rate"] is None
    assert row["assessment"] == "insufficient_reviews"


def test_rate_is_reported_once_the_sample_is_large_enough(engine: Engine) -> None:
    add(engine, "large_transfer_mad", "dismissed", 15)
    add(engine, "large_transfer_mad", "promoted", MIN_REVIEWED - 15)
    add(engine, "large_transfer_mad", "invalidated", 5)
    add(engine, "large_transfer_mad", "open", 10)
    report = service(engine).performance()
    row = next(d for d in report["detectors"] if d["detector"] == "large_transfer_mad")
    assert row["dismissal_rate"] == 0.75
    assert row["assessment"] == "measured"
    assert row["total"] == 35
    assert row["invalidated_share"] == pytest.approx(5 / 35, abs=0.001)
    assert row["by_status"]["open"] == 10
    assert any("not ground truth" in c for c in report["caveats"])


def test_every_detector_is_listed_even_without_signals(engine: Engine) -> None:
    ids = {d["detector"] for d in service(engine).performance()["detectors"]}
    assert ids == {d["id"] for d in service(engine).detectors()}
