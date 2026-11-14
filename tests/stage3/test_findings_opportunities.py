"""Findings (dedup, attach, watchlist filter) and research leads on a seeded, synthetic DB.

The seeded rows are artificial test data written directly to SQLite; they never leave tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, func, insert, select

from bip.chains.common import SourceInfo
from bip.chains.ethereum.parse import provenance
from bip.contracts.provenance import Finality
from bip.db import schema
from bip.db.types import utcnow
from bip.intel.service import as_of_now
from tests.stage3.test_intel_api import INTEL, Env

WALLET = "0x" + "ab" * 20
TOKEN = "0x" + "70" * 20


def a(n: int) -> str:
    return "0x" + f"{n:040x}"


class Seeder:
    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self.n = 0

    def tx(
        self,
        at: datetime,
        frm: str,
        to: str | None,
        *,
        token_to: str | None = None,
        amount: int = 1,
    ) -> str:
        self.n += 1
        txh = "0x" + f"{self.n:064x}"
        block = 1_000 + self.n
        prov = provenance(
            SourceInfo("publicnode-eth", "eth_getTransactionByHash", utcnow(), None),
            block_number=block,
            block_hash=txh,
            tx_hash=txh,
            event_time=at,
            finality=Finality.OBSERVED,
        )
        self.conn.execute(
            insert(schema.transactions).values(
                chain="ethereum",
                tx_id=txh,
                block_number=block,
                block_time=at,
                from_address=frm,
                to_address=to,
                status="success",
                provenance_json=prov.model_dump_json(),
                stored_at=utcnow(),
                block_hash=txh,
                parse_status="complete",
                value_raw="0",
            )
        )
        if token_to is not None:
            self.conn.execute(
                insert(schema.transfers).values(
                    chain="ethereum",
                    tx_id=txh,
                    event_key="log:0",
                    block_number=block,
                    standard="erc20",
                    token=TOKEN,
                    from_address=frm,
                    to_address=token_to,
                    amount_raw=str(amount),
                    classification="unclassified",
                    provenance_json=prov.model_dump_json(),
                    stored_at=utcnow(),
                    observation_kind="erc20_transfer_log",
                    block_time=at,
                )
            )
        return txh

    def run(self, address: str) -> None:
        self.conn.execute(
            insert(schema.ingestion_runs).values(
                chain="ethereum",
                address=address,
                status="succeeded",
                requested_limit=10,
                started_at=utcnow(),
                finished_at=utcnow(),
            )
        )


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    e = Env(tmp_path, second=False)
    yield e
    e.engine.dispose()


def seed_spike(env: Env) -> None:
    end = as_of_now() - timedelta(days=1)
    start = end - timedelta(days=12)
    with env.engine.begin() as conn:
        s = Seeder(conn)
        s.run(WALLET)
        for d in range(11):
            for i in range(2):
                s.tx(start + timedelta(days=d, hours=i), WALLET, a(500 + d))
        for i in range(25):
            s.tx(start + timedelta(days=11, minutes=i), WALLET, a(9))


def test_anomaly_finding_is_recorded_once_and_attachable(env: Env) -> None:
    seed_spike(env)
    with TestClient(env.app) as c:
        run = c.post(f"{INTEL}/analyze/ethereum/{WALLET}").json()
        assert run["status"] == "succeeded"
        spike = next(
            x
            for x in c.get(f"{INTEL}/anomalies?status=anomaly").json()["items"]
            if x["detector_id"] == "tx_frequency_spike"
        )
        fid = spike["finding_id"]
        assert fid
        assert fid.startswith("bip.tx_frequency_spike.")
        detail = c.get(f"{INTEL}/anomalies/{fid}").json()
        finding = detail["finding"]
        assert finding["status"] == "unverified"
        types = {cl["claim_type"] for cl in finding["claims"]}
        assert types == {"statistical_observation"}  # no verified_fact without corroboration
        assert finding["limitations"]
        assert finding["produced_by"] == "tx_frequency_spike@1.0.0"
        assert any(e["kind"] == "chain_record" for e in finding["evidence"])

        # a refresh after new data must not duplicate the same finding
        with env.engine.begin() as conn:
            Seeder(conn).run(WALLET)  # changes nothing analysed except run count
        c.post(f"{INTEL}/analyze/ethereum/{WALLET}")
        with env.engine.connect() as conn:
            n = conn.execute(
                select(func.count())
                .select_from(schema.findings)
                .where(schema.findings.c.finding_id == fid)
            ).scalar_one()
        assert n == 1

        inv = c.post("/api/v1/investigations", json={"title": "Spike review"}).json()
        r = c.post(f"{INTEL}/findings/{fid}/attach", json={"investigation_id": inv["id"]})
        assert r.status_code == 200, r.text
        assert c.get(f"{INTEL}/anomalies/{fid}").json()["investigation_id"] == inv["id"]
        got = c.get(f"/api/v1/investigations/{inv['id']}").json()
        assert any(f["finding_id"] == fid for f in got["findings"])
        assert (
            c.post(
                f"{INTEL}/findings/nope/attach", json={"investigation_id": inv["id"]}
            ).status_code
            == 404
        )

        wl = c.post("/api/v1/watchlists", json={"name": "Spikes"}).json()
        c.post(
            f"/api/v1/watchlists/{wl['id']}/entries", json={"chain": "ethereum", "address": WALLET}
        )
        listed = c.get(f"{INTEL}/anomalies?watchlist_id={wl['id']}").json()
        assert listed["total"] == 5
        empty = c.post("/api/v1/watchlists", json={"name": "Empty"}).json()
        assert c.get(f"{INTEL}/anomalies?watchlist_id={empty['id']}").json()["total"] == 0
        batch = c.post(f"{INTEL}/watchlists/{wl['id']}/analyze").json()
        assert batch["analysed"] == 1


def test_opportunities_insufficient_data_returns_no_candidates(env: Env) -> None:
    with TestClient(env.app) as c:
        r = c.post(f"{INTEL}/opportunities/refresh/ethereum").json()
        assert r["status"] == "insufficient_data"
        assert r["candidates"] == []
        assert c.get(f"{INTEL}/opportunities").json()["total"] == 0


def seed_rising_token(env: Env, *, recent_wallets: int) -> None:
    anchor = datetime(2026, 6, 30, tzinfo=UTC)
    with env.engine.begin() as conn:
        s = Seeder(conn)
        for w in range(3):
            s.run(a(1000 + w))
        s.tx(anchor - timedelta(days=10), a(1000), TOKEN, token_to=a(2000))  # prior: 2 parties
        for i in range(12):
            frm = a(1000 + i % recent_wallets)
            s.tx(anchor - timedelta(days=1, hours=i), frm, TOKEN, token_to=a(3000 + i % 3))


def test_rising_participation_lead_is_transparent_and_deduplicated(env: Env) -> None:
    seed_rising_token(env, recent_wallets=3)
    with TestClient(env.app) as c:
        r = c.post(f"{INTEL}/opportunities/refresh/ethereum").json()
        assert r["status"] == "evaluated"
        token_leads = [
            x for x in r["candidates"] if x["subject"] == TOKEN and x["subject_kind"] == "token"
        ]
        assert len(token_leads) == 1
        lead = token_leads[0]
        assert lead["label"] == "unverified research lead"
        assert lead["features"]["recent_participants"] == 6
        assert lead["features"]["prior_participants"] == 2
        assert sum(lead["contributions"].values()) == pytest.approx(lead["score"], abs=1e-3)
        assert "Not investment advice" in lead["disclaimer"]
        assert lead["evidence"]["total"] == 12
        c.post(f"{INTEL}/opportunities/refresh/ethereum")  # refresh: no duplicates
        listed = c.get(f"{INTEL}/opportunities").json()
        keys = [x["candidate_key"] for x in listed["items"]]
        assert len(keys) == len(set(keys))
        one = c.get(f"{INTEL}/opportunities/{lead['candidate_key']}").json()
        assert one["subject"] == TOKEN
        assert one["current"] is True
        assert c.get(f"{INTEL}/opportunities/{'0' * 64}").status_code == 404


def test_flat_participation_produces_no_lead(env: Env) -> None:
    anchor = datetime(2026, 6, 30, tzinfo=UTC)
    with env.engine.begin() as conn:
        s = Seeder(conn)
        for w in range(3):
            s.run(a(1000 + w))
        for i in range(12):
            s.tx(
                anchor - timedelta(days=10, hours=i),
                a(1000 + i % 3),
                TOKEN,
                token_to=a(3000 + i % 3),
            )
            s.tx(
                anchor - timedelta(days=1, hours=i),
                a(1000 + i % 3),
                TOKEN,
                token_to=a(3000 + i % 3),
            )
    with TestClient(env.app) as c:
        r = c.post(f"{INTEL}/opportunities/refresh/ethereum").json()
        assert r["status"] == "evaluated"
        assert r["candidates"] == []


def test_migration_v3_is_additive_and_idempotent(tmp_path: Path) -> None:
    """v1 data survives the upgrade; v3 adds created_contract and tables; rerun is a no-op."""
    import sqlite3

    from bip.config import load_settings
    from bip.db.engine import create_db_engine, database_path, migrate

    settings = load_settings(data_root=tmp_path / "d", env="test")
    path = database_path(settings)
    path.parent.mkdir(parents=True)
    con = sqlite3.connect(path)
    con.executescript(Path("tests/fixtures/schema_v1.sql").read_text())
    con.execute(
        "INSERT INTO schema_migrations VALUES (1,'2026-11-03T00:00:00.000000+00:00','initial')"
    )
    con.execute(
        "INSERT INTO watchlists (name, created_at, updated_at) VALUES "
        "('kept', '2026-11-03T00:00:00.000000+00:00', '2026-11-03T00:00:00.000000+00:00')"
    )
    con.commit()
    con.close()
    engine = create_db_engine(settings)
    assert migrate(engine) == schema.SCHEMA_VERSION
    assert migrate(engine) == schema.SCHEMA_VERSION
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(transactions)")}
        tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master")}
        kept = conn.exec_driver_sql("SELECT name FROM watchlists").scalar_one()
    assert "created_contract" in cols
    assert {
        "evidence_verifications",
        "provider_observations",
        "analysis_runs",
        "anomaly_observations",
        "opportunity_candidates",
    } <= tables
    assert kept == "kept"
    engine.dispose()
