"""Bytes per row measured by encoding stored rows as Parquet (roadmap Stage 5 acceptance)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from bip.config import load_settings
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.storage.lake.datasets import TRANSACTIONS, TRANSFERS, measure_bytes_per_row
from bip.tools.lake import main
from tests.discovery.seed import E18, NOW, Seed, eth_addr, eth_tx


def seeded(tmp_path: Path, n: int) -> Path:
    data = tmp_path / "data"
    settings = load_settings(data_root=data, env="test")
    engine = create_db_engine(settings)
    seed = Seed(engine)
    for i in range(n):
        when = NOW - timedelta(hours=i)
        seed.tx(eth_tx(i + 1), eth_addr(0x100 + i), when)
        seed.transfer(eth_tx(i + 1), eth_addr(0x100 + i), eth_addr(0x200), E18 * (i + 1), when)
    Repository(engine).set_meta("data_origin", "offline_fixture")
    engine.dispose()
    return data


def test_schemas_are_named_and_keyed() -> None:
    assert TRANSACTIONS.dataset == "transactions"
    assert TRANSFERS.dataset == "transfers"
    assert TRANSFERS.sort_key[0] == "chain"


def test_measure_reports_bytes_per_row_and_the_data_origin(tmp_path: Path) -> None:
    data = seeded(tmp_path, 50)
    settings = load_settings(data_root=data, env="test")
    engine = create_db_engine(settings)
    try:
        report = measure_bytes_per_row(Repository(engine), settings.resolved_data_root, limit=1000)
    finally:
        engine.dispose()
    assert report["data_origin"] == "offline_fixture"
    by = {t["dataset"]: t for t in report["tables"]}
    assert by["transactions"]["rows"] == 50
    assert by["transfers"]["rows"] == 50
    assert by["transfers"]["bytes_per_row"] > 0
    assert not list((data / "tmp").rglob("*.parquet"))  # temporary files removed


def test_an_empty_database_measures_nothing(tmp_path: Path) -> None:
    data = seeded(tmp_path, 0)
    settings = load_settings(data_root=data, env="test")
    engine = create_db_engine(settings)
    try:
        report = measure_bytes_per_row(Repository(engine), settings.resolved_data_root, limit=10)
    finally:
        engine.dispose()
    assert all(t["rows"] == 0 and t["bytes_per_row"] is None for t in report["tables"])


def test_the_cli_measure_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data = seeded(tmp_path, 5)
    monkeypatch.setenv("BIP_DATA_ROOT", str(data))
    monkeypatch.setenv("BIP_ENV", "test")
    assert main(["measure", "--limit", "100"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["data_origin"] == "offline_fixture"
    assert {t["dataset"] for t in out["tables"]} == {"transactions", "transfers"}
