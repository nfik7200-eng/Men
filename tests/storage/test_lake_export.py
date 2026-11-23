"""Exporting stored rows into Silver lake partitions: bounded, resumable, idempotent."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")
pytest.importorskip("duckdb")

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from bip.api.access import required_role
from bip.api.app import create_app
from bip.config import Settings, load_settings
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.storage.lake.datasets import TRANSACTIONS, TRANSFERS
from bip.storage.lake.export import LakeExporter, cursor_key
from bip.storage.lake.integrity import verify_table
from bip.storage.lake.reader import LakeReader, TableRef
from bip.storage.paths import Layer, StorageRoot
from tests.api.conftest import roomy_guard
from tests.discovery.seed import E18, NOW, SOL, Seed, eth_addr, eth_tx

API = "/api/v1"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(data_root=tmp_path / "data", env="test")


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    e = create_db_engine(settings)
    yield e
    e.dispose()


def seed(engine: Engine, n: int, start: int = 0, days_apart: bool = True) -> None:
    s = Seed(engine)
    for i in range(start, start + n):
        when = NOW - timedelta(days=i if days_apart else 0, hours=1)
        s.tx(eth_tx(i + 1), eth_addr(0x100 + i), when)
        s.transfer(eth_tx(i + 1), eth_addr(0x100 + i), eth_addr(0x200), E18, when)


def exporter(engine: Engine, settings: Settings, batch: int = 1000) -> LakeExporter:
    root = StorageRoot(settings.resolved_data_root)
    return LakeExporter(Repository(engine), root, roomy_guard(root.root), batch_rows=batch)


def count(settings: Settings, schema: object, chain: str = "ethereum") -> int:
    reader = LakeReader(StorageRoot(settings.resolved_data_root))
    ref = TableRef(Layer.SILVER, chain, schema)  # type: ignore[arg-type]
    return int(reader.query("SELECT count(*) FROM t", t=ref)[1][0][0])


def test_rows_land_in_daily_partitions_and_the_cursor_moves(
    engine: Engine, settings: Settings
) -> None:
    seed(engine, 3)
    out = exporter(engine, settings).run()
    by = {d["dataset"]: d for d in out["datasets"]}
    assert by["transactions"]["rows"] == 3
    assert by["transfers"]["rows"] == 3
    assert by["transactions"]["files"] == 3  # three different days
    assert count(settings, TRANSACTIONS) == 3
    assert count(settings, TRANSFERS) == 3
    repo = Repository(engine)
    assert int(repo.get_meta(cursor_key("transactions")) or 0) > 0
    root = StorageRoot(settings.resolved_data_root)
    assert verify_table(root, Layer.SILVER, "ethereum", "transactions").ok


def test_a_second_run_exports_only_new_rows(engine: Engine, settings: Settings) -> None:
    seed(engine, 2)
    exporter(engine, settings).run()
    again = exporter(engine, settings).run()
    assert all(d["rows"] == 0 for d in again["datasets"])
    seed(engine, 2, start=10)
    later = exporter(engine, settings).run()
    assert {d["dataset"]: d["rows"] for d in later["datasets"]} == {
        "transactions": 2,
        "transfers": 2,
    }
    assert count(settings, TRANSACTIONS) == 4


def test_batches_are_bounded_and_resume(engine: Engine, settings: Settings) -> None:
    seed(engine, 5, days_apart=False)
    first = exporter(engine, settings, batch=2).run()
    assert {d["dataset"]: d["rows"] for d in first["datasets"]} == {
        "transactions": 2,
        "transfers": 2,
    }
    assert all(d["more"] for d in first["datasets"])
    exporter(engine, settings, batch=2).run()
    exporter(engine, settings, batch=2).run()
    assert count(settings, TRANSACTIONS) == 5


def test_a_crash_before_the_cursor_moves_is_replayed_without_duplicates(
    engine: Engine, settings: Settings
) -> None:
    seed(engine, 3)
    ex = exporter(engine, settings)
    ex.run()
    Repository(engine).set_meta(cursor_key("transactions"), "0")  # as if the cursor write was lost
    replay = exporter(engine, settings).run()
    tx = next(d for d in replay["datasets"] if d["dataset"] == "transactions")
    assert tx["rows"] == 3
    assert tx["files_created"] == 0  # identical files: adopted, not duplicated
    assert count(settings, TRANSACTIONS) == 3


def test_chains_are_separate_tables(engine: Engine, settings: Settings) -> None:
    s = Seed(engine)
    s.tx("5" * 88, "So1ana1111111111111111111111111111111111111", NOW, chain=SOL)
    exporter(engine, settings).run()
    assert count(settings, TRANSACTIONS, chain="solana") == 1
    assert count(settings, TRANSACTIONS, chain="ethereum") == 0


def test_api_status_and_admin_export(settings: Settings, engine: Engine) -> None:
    seed(engine, 2)
    app = create_app(settings, engine=engine, disk_guard=roomy_guard(settings.resolved_data_root))
    with TestClient(app) as c:
        before = c.get(f"{API}/system/lake").json()
        assert before["available"] is True
        assert all(t["rows"] == 0 for t in before["tables"])
        r = c.post(f"{API}/system/lake/export", headers={"X-BIP-Client": "web"})
        assert r.status_code == 200, r.text
        after = c.get(f"{API}/system/lake").json()
        tx = next(t for t in after["tables"] if t["table"] == "ethereum.transactions")
        assert tx["rows"] == 2
        assert tx["verified"] is True
        assert after["cursors"]["transactions"] > 0
    assert required_role("POST", "/system/lake/export") == "admin"
    assert required_role("GET", "/system/lake") == "viewer"
