"""Migration v6: research runs linked to cases (ADR-030).

Additive: runs and reports are never touched.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from bip.config import load_settings
from bip.db import schema
from bip.db.engine import create_db_engine, database_path, migrate
from tests.stage5.fakes import FakeLLM
from tests.stage5.test_research import API, World, body, run_to_end


def test_v5_database_with_cases_upgrades_to_v6_keeping_rows(tmp_path: Path) -> None:
    settings = load_settings(data_root=tmp_path / "d", env="test")
    engine = create_db_engine(settings)
    migrate(engine)
    with engine.begin() as conn:  # simulate a v5 database that already has a case
        for t in schema.V6_TABLES:
            t.drop(conn)
        conn.exec_driver_sql("DELETE FROM schema_migrations WHERE version >= 6")
        conn.exec_driver_sql(
            "INSERT INTO investigations (title, status, created_at, updated_at) VALUES "
            "('Kept case', 'open', '2026-11-05T00:00:00.000000+00:00', "
            "'2026-11-05T00:00:00.000000+00:00')"
        )
    engine.dispose()
    con = sqlite3.connect(database_path(settings))
    assert max(r[0] for r in con.execute("SELECT version FROM schema_migrations")) == 5
    con.close()

    engine = create_db_engine(settings)
    assert migrate(engine) == schema.SCHEMA_VERSION
    with engine.connect() as conn:
        tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master")}
        kept = conn.exec_driver_sql("SELECT title FROM investigations").scalar_one()
    assert "investigation_research_runs" in tables
    assert kept == "Kept case"
    engine.dispose()


def test_link_list_and_unlink_research_runs_on_a_case(tmp_path: Path) -> None:
    w = World(tmp_path, llm=FakeLLM(available=False))
    try:
        with TestClient(w.app) as c:
            c.post(f"{API}/ingestion/wallet/ethereum/{w.env.eth.wallet}?limit=20")
            run = run_to_end(c, body(w))
            case = c.post(
                f"{API}/investigations", json={"title": "Case", "description": None}
            ).json()
            url = f"{API}/investigations/{case['id']}/research-runs"

            r = c.post(url, json={"run_id": run["run_id"], "note": "supports hypothesis A"})
            assert r.status_code == 201
            assert c.post(url, json={"run_id": run["run_id"]}).status_code == 409  # no duplicates
            missing = c.post(url, json={"run_id": "00000000-0000-0000-0000-000000000000"})
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "not_found"
            no_case = c.post(
                f"{API}/investigations/999/research-runs", json={"run_id": run["run_id"]}
            )
            assert no_case.status_code == 404

            items = c.get(url).json()["items"]
            assert len(items) == 1
            assert items[0]["run_id"] == run["run_id"]
            assert items[0]["note"] == "supports hypothesis A"
            assert items[0]["state"] == run["state"]
            assert items[0]["chain"] == "ethereum"
            assert items[0]["question"]

            assert c.delete(f"{url}/{run['run_id']}").status_code == 204
            assert c.get(url).json()["items"] == []
            # the run and its report are untouched by unlinking
            assert c.get(f"{API}/research/runs/{run['run_id']}").status_code == 200
            assert c.get(f"{API}/research/runs/{run['run_id']}/report").status_code == 200
        with w.env.engine.connect() as conn:
            n = conn.execute(select(func.count()).select_from(schema.research_reports)).scalar_one()
        assert n == 1
    finally:
        w.env.engine.dispose()
