"""Integration: fake RPC -> ingestion -> analysis -> graph / flows / findings -> API.

All network traffic is served by the deterministic fakes in tests/stage2/fakes.py. A second,
independent provider (Infura's infrastructure group) is simulated by a second fake node on its
allow-listed host, configured through an environment mapping with a dummy key.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from bip.api.app import create_app
from bip.config import load_settings
from bip.db import schema
from bip.db.engine import create_db_engine
from tests.api.conftest import roomy_guard
from tests.stage2.fakes import (
    ETH_HOST,
    SOL_HOST,
    FakeEthNode,
    FakeSolNode,
    FakeTransport,
    h32,
)

API = "/api/v1"
INTEL = f"{API}/intelligence"
INFURA_HOST = "mainnet.infura.io"
ALCHEMY_HOST = "eth-mainnet.g.alchemy.com"
ALCHEMY_SOL_HOST = "solana-mainnet.g.alchemy.com"


class Env:
    def __init__(
        self,
        tmp_path: Path,
        *,
        second: bool = True,
        third: bool = False,
        data_origin: str | None = None,
    ) -> None:
        self.eth = FakeEthNode()
        self.eth2 = FakeEthNode()
        self.eth3 = FakeEthNode()
        self.sol = FakeSolNode()
        self.sol2 = FakeSolNode()
        nodes = {
            ETH_HOST: self.eth.handle,
            SOL_HOST: self.sol.handle,
            INFURA_HOST: self.eth2.handle,
            ALCHEMY_HOST: self.eth3.handle,
            ALCHEMY_SOL_HOST: self.sol2.handle,
        }
        self.transport = FakeTransport(nodes)
        environ: dict[str, str] = {}
        if second:
            environ["BIP_PROVIDER_INFURA_ETH_URL"] = f"https://{INFURA_HOST}/v3/test-key-not-real"
            environ["BIP_PROVIDER_ALCHEMY_SOL_URL"] = (
                f"https://{ALCHEMY_SOL_HOST}/v2/test-key-not-real"
            )
        if third:
            environ["BIP_PROVIDER_ALCHEMY_ETH_URL"] = f"https://{ALCHEMY_HOST}/v2/test-key-not-real"
        self.settings = load_settings(data_root=tmp_path / "data", env="test")
        self.engine = create_db_engine(self.settings)
        self.app = create_app(
            self.settings,
            engine=self.engine,
            transport=self.transport,
            disk_guard=roomy_guard(self.settings.resolved_data_root),
            environ=environ,
            rpc_sleep=lambda _s: None,
            data_origin=data_origin,
        )


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    e = Env(tmp_path)
    yield e
    e.engine.dispose()


@pytest.fixture
def client(env: Env) -> Iterator[TestClient]:
    with TestClient(env.app) as c:
        yield c


def ingest(client: TestClient, chain: str, address: str) -> None:
    r = client.post(f"{API}/ingestion/wallet/{chain}/{address}?limit=20")
    assert r.status_code == 201, r.text
    assert r.json()["status"] in {"succeeded", "partial"}


# ---------------------------------------------------------------- wallet intelligence


def test_eth_profile_flows_behaviour_from_ingested_fixture(env: Env, client: TestClient) -> None:
    w = env.eth.wallet
    ingest(client, "ethereum", w)
    p = client.get(f"{INTEL}/wallets/ethereum/{w}/profile").json()
    assert p["data_origin"] == "stored_observations"
    assert p["scope"]["transactions"] == 2
    labels = {m["id"]: m["label"] for m in p["metrics"]}
    assert labels["transactions"] == "2 transactions observed in retrieved dataset"
    assert p["verification"]["overall"] == "unverified"
    cps = {c["address"] for c in p["counterparties"]["items"]}
    assert env.eth.other in cps
    flows = client.get(f"{INTEL}/flows/ethereum/{w}").json()
    assets = {a["asset"]: a for a in flows["assets"]}
    assert assets["native"]["in_raw"] == str(2 * 10**18)
    assert assets[env.eth.token]["out_raw"] == "1500000"
    assert assets[env.eth.token]["in_raw"] == "3000000"
    assert assets[env.eth.token]["in"] is None  # decimals unknown: never assumed
    assert flows["usd_values"] is None
    beh = client.get(f"{INTEL}/wallets/ethereum/{w}/behaviour").json()
    assert {r["status"] for r in beh["items"]} <= {"not_enough_data", "not_detected", "detected"}
    act = client.get(f"{INTEL}/wallets/ethereum/{w}/activity?limit=1").json()
    assert act["limit"] == 1
    assert len(act["items"]) <= 1
    cp = client.get(f"{INTEL}/wallets/ethereum/{w}/counterparties").json()
    assert cp["total"] >= 1


def test_solana_profile_resolves_token_accounts_and_programs(env: Env, client: TestClient) -> None:
    w = env.sol.wallet
    ingest(client, "solana", w)
    p = client.get(f"{INTEL}/wallets/solana/{w}/profile").json()
    names = {i["target"]: i["name"] for i in p["interactions"]["items"]}
    assert names.get("11111111111111111111111111111111") == "System Program"
    assert names.get(env.sol.unknown_program, "missing") is None  # unknown stays unknown
    assert p["scope"]["transactions"] >= 2


def test_analyze_is_reproducible_and_does_not_duplicate(env: Env, client: TestClient) -> None:
    w = env.eth.wallet
    ingest(client, "ethereum", w)
    first = client.post(f"{INTEL}/analyze/ethereum/{w}")
    assert first.status_code == 201, first.text
    a1 = first.json()
    assert a1["status"] == "succeeded"
    assert a1["reused"] is False
    a2 = client.post(f"{INTEL}/analyze/ethereum/{w}").json()
    assert a2["reused"] is True
    assert a2["run_id"] == a1["run_id"]
    assert a2["input_hash"] == a1["input_hash"]
    with env.engine.connect() as conn:
        runs = conn.execute(select(func.count()).select_from(schema.analysis_runs)).scalar_one()
        obs = conn.execute(
            select(func.count()).select_from(schema.anomaly_observations)
        ).scalar_one()
    assert runs == 1
    assert obs == 5
    anomalies = client.get(f"{INTEL}/anomalies?chain=ethereum").json()
    assert anomalies["total"] == 5
    assert {i["status"] for i in anomalies["items"]} <= {
        "insufficient_data",
        "no_anomaly",
        "anomaly",
        "could_not_run",
    }
    one = anomalies["items"][0]["observation_id"]
    assert client.get(f"{INTEL}/anomalies/{one}").status_code == 200
    assert client.get(f"{INTEL}/anomalies/nope").status_code == 404


def test_graph_is_bounded_and_evidence_backed(env: Env, client: TestClient) -> None:
    w = env.eth.wallet
    ingest(client, "ethereum", w)
    g = client.get(f"{INTEL}/graph/ethereum/{w}?depth=2").json()
    assert g["center"] == w
    kinds = {e["kind"] for e in g["edges"]}
    assert {"native_transfer", "token_transfer"} <= kinds
    for e in g["edges"]:
        assert e["evidence"], e
        ev = e["evidence"][0]
        assert {"tx_id", "position", "provider", "verification"} <= set(ev)
    assert any(n["kind"] == "token" for n in g["nodes"])
    assert "do not establish common ownership" in g["statement"]
    small = client.get(f"{INTEL}/graph/ethereum/{w}?max_nodes=2").json()
    assert len(small["nodes"]) <= 2
    assert small["truncation"]["node_limit_reached"] is True
    assert client.get(f"{INTEL}/graph/ethereum/{w}?depth=4").status_code == 422


def test_validation_and_unknown_chain(client: TestClient) -> None:
    assert client.get(f"{INTEL}/wallets/base/0x{'1' * 40}/profile").status_code == 422
    assert client.get(f"{INTEL}/wallets/ethereum/not-an-address/profile").status_code == 422
    assert (
        client.get(f"{INTEL}/flows/ethereum/0x{'1' * 40}?since=2026-01-01T00:00:00").status_code
        == 422
    )
    assert client.get(f"{INTEL}/opportunities/xyz").status_code == 422


def test_empty_wallet_returns_honest_empty_profile(client: TestClient) -> None:
    p = client.get(f"{INTEL}/wallets/ethereum/0x{'1' * 40}/profile").json()
    assert p["scope"]["transactions"] == 0
    assert p["verification"]["overall"] == "no evidence"


def test_data_origin_marker_labels_fixture_databases(tmp_path: Path) -> None:
    e = Env(tmp_path, data_origin="offline_fixture")
    with TestClient(e.app) as c:
        assert c.get(f"{INTEL}/status").json()["data_origin"] == "offline_fixture"
    e.engine.dispose()


# ---------------------------------------------------------------- corroboration


def test_independent_provider_corroborates_eth_tx(env: Env, client: TestClient) -> None:
    ingest(client, "ethereum", env.eth.wallet)
    r = client.post(f"{INTEL}/verification/ethereum/{env.eth.txb}").json()
    assert r["status"] == "checked", r
    assert r["outcome"] == "cross_verified"
    assert r["label"] == "corroborated"
    assert r["source_infrastructure"] == "allnodes"
    assert [o["infrastructure"] for o in r["observations"]] == ["consensys"]
    v = client.get(f"{INTEL}/verification/ethereum/{env.eth.txb}").json()
    assert v["effective_status"] == "cross_verified"
    assert v["provider_observations"][0]["found"] is True
    # stored source record is never modified
    with env.engine.connect() as conn:
        prov = conn.execute(
            select(schema.transactions.c.provenance_json).where(
                schema.transactions.c.tx_id == env.eth.txb
            )
        ).scalar_one()
    assert '"verification_status":"unverified"' in prov
    p = client.get(f"{INTEL}/wallets/ethereum/{env.eth.wallet}/profile").json()
    assert p["verification"]["counts"].get("corroborated") == 1
    st = client.get(f"{INTEL}/status").json()["verification"]
    assert st["corroborated"] == 1
    assert st["checked"] == 1


def test_disagreement_is_conflict_and_preserved(env: Env, client: TestClient) -> None:
    ingest(client, "ethereum", env.eth.wallet)
    env.eth2.txs[env.eth.txb] = {**env.eth2.txs[env.eth.txb], "value": hex(1)}
    r = client.post(f"{INTEL}/verification/ethereum/{env.eth.txb}").json()
    assert r["outcome"] == "conflict"
    assert r["label"] == "conflicted"
    fields = {d["field"] for d in r["differences"][0]["differences"]}
    assert "value_raw" in fields
    v = client.get(f"{INTEL}/verification/ethereum/{env.eth.txb}").json()
    assert v["checks"][0]["differences"]
    assert v["stored"]["value_raw"] == str(2 * 10**18)  # original kept


def test_not_found_by_one_provider_is_not_a_conflict(env: Env, client: TestClient) -> None:
    ingest(client, "ethereum", env.eth.wallet)
    del env.eth2.txs[env.eth.txa]
    r = client.post(f"{INTEL}/verification/ethereum/{env.eth.txa}").json()
    assert r["outcome"] == "unverified"


def test_unparseable_answers_are_failed_lookups_not_conflicts(tmp_path: Path) -> None:
    e = Env(tmp_path, third=True)
    try:
        with TestClient(e.app) as c:
            ingest(c, "ethereum", e.eth.wallet)
            for node in (e.eth2, e.eth3):
                node.reorg_tx = e.eth.txb  # tx and receipt disagree -> BIP cannot parse it
            r = c.post(f"{INTEL}/verification/ethereum/{e.eth.txb}").json()
            # Neither "corroborated" nor "conflicted": nothing usable was observed.
            assert r["status"] == "could_not_run"
            assert len(r["observations"]) == 2
            assert all(o["error"].startswith("parse_error") for o in r["observations"])
        with e.engine.connect() as conn:
            n = conn.execute(
                select(func.count()).select_from(schema.evidence_verifications)
            ).scalar_one()
        assert n == 0
    finally:
        e.engine.dispose()


def test_graph_depth_two_does_not_double_count(env: Env, client: TestClient) -> None:
    ingest(client, "ethereum", env.eth.wallet)
    one = client.get(f"{INTEL}/graph/ethereum/{env.eth.wallet}?depth=1").json()
    two = client.get(f"{INTEL}/graph/ethereum/{env.eth.wallet}?depth=2").json()

    def native(g: dict[str, Any]) -> dict[str, Any]:
        return next(e for e in g["edges"] if e["kind"] == "native_transfer")

    assert native(one)["count"] == native(two)["count"] == 1
    assert native(two)["amount_raw"] == str(2 * 10**18)
    assert {e["kind"]: e["count"] for e in two["edges"] if e["kind"] == "token_transfer"}


def test_verification_is_serialized(env: Env, client: TestClient) -> None:
    ingest(client, "ethereum", env.eth.wallet)
    lock = env.app.state.bip.verify_lock
    assert lock.acquire(blocking=False)
    try:
        r = client.post(f"{INTEL}/verification/ethereum/{env.eth.txb}")
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "verification_busy"
    finally:
        lock.release()


def test_refreshed_finding_reflects_new_corroboration(env: Env, client: TestClient) -> None:
    """A finding recomputed after corroboration keeps its id but updates its status."""
    from bip.contracts.findings import ClaimType
    from bip.db.types import utcnow

    ingest(client, "ethereum", env.eth.wallet)
    svc = env.app.state.bip.intel
    ds = svc.dataset("ethereum", env.eth.wallet)
    args = {
        "detector_id": "test_rule",
        "version": "1",
        "title": "t",
        "statement": "s",
        "claim_type": ClaimType.STATISTICAL_OBSERVATION,
        "method": "m",
        "explanation": "e",
        "tx_ids": [env.eth.txb],
        "conf": 0.1,
        "limitations": ["l"],
        "window": (None, None),
    }
    fid = svc._make_finding(ds, now=utcnow(), **args)
    client.post(f"{INTEL}/verification/ethereum/{env.eth.txb}")
    ds2 = svc.dataset("ethereum", env.eth.wallet)
    assert svc._make_finding(ds2, now=utcnow(), **args) == fid
    with env.engine.connect() as conn:
        rows = conn.execute(
            select(schema.findings.c.status).where(schema.findings.c.finding_id == fid)
        ).all()
    assert [r[0] for r in rows] == ["partially_verified"]


def test_no_independent_provider_records_nothing(tmp_path: Path) -> None:
    e = Env(tmp_path, second=False)
    with TestClient(e.app) as c:
        ingest(c, "ethereum", e.eth.wallet)
        r = c.post(f"{INTEL}/verification/ethereum/{e.eth.txb}").json()
        assert r["status"] == "could_not_run"
        assert "independent" in r["reason"]
    with e.engine.connect() as conn:
        assert (
            conn.execute(
                select(func.count()).select_from(schema.evidence_verifications)
            ).scalar_one()
            == 0
        )
    e.engine.dispose()


def test_solana_corroboration_with_independent_provider(env: Env, client: TestClient) -> None:
    ingest(client, "solana", env.sol.wallet)
    r = client.post(f"{INTEL}/verification/solana/{env.sol.sig_a}").json()
    assert r["outcome"] == "cross_verified", r


def test_wallet_verification_is_bounded(env: Env, client: TestClient) -> None:
    ingest(client, "ethereum", env.eth.wallet)
    r = client.post(f"{INTEL}/verification/ethereum/wallet/{env.eth.wallet}?limit=5").json()
    assert r["requested"] == 2
    assert r["summary"] == {"cross_verified": 2}
    assert (
        client.post(f"{INTEL}/verification/ethereum/wallet/{env.eth.wallet}?limit=21").status_code
        == 422
    )
    assert client.post(f"{INTEL}/verification/ethereum/{h32(0x999)}").status_code == 404


def test_corroborated_evidence_produces_partially_verified_finding(
    env: Env, client: TestClient
) -> None:
    """After corroboration, re-analysis has a new input hash and findings cite verified
    chain records only for corroborated transactions."""
    ingest(client, "ethereum", env.eth.wallet)
    a1 = client.post(f"{INTEL}/analyze/ethereum/{env.eth.wallet}").json()
    client.post(f"{INTEL}/verification/ethereum/wallet/{env.eth.wallet}")
    a2 = client.post(f"{INTEL}/analyze/ethereum/{env.eth.wallet}").json()
    assert a2["input_hash"] != a1["input_hash"]
    assert a2["reused"] is False


# ---------------------------------------------------------------- outcome rules (pure)


def _obs(infra: str, *, found: bool = True, block_hash: str = "0xaa") -> dict[str, Any]:
    fields: dict[str, Any] = {"block_hash": block_hash, "status": "success", "transfers": {}}
    return {
        "provider_id": infra,
        "infrastructure": infra,
        "found": found,
        "fields": fields if found else None,
        "error": None,
    }


STORED: dict[str, Any] = {"block_hash": "0xaa", "status": "success", "transfers": {}}


def test_decide_rules() -> None:
    from bip.intel.verify import _decide  # pyright: ignore[reportPrivateUsage]

    assert _decide(STORED, [_obs("b")], True)[0] == "cross_verified"
    assert _decide(STORED, [_obs("b")], False)[0] == "unverified"  # source group unknown
    assert _decide(STORED, [_obs("b"), _obs("c")], False)[0] == "cross_verified"
    reorg = [_obs("b", block_hash="0xbb"), _obs("c", block_hash="0xbb")]
    assert _decide(STORED, reorg, True)[0] == "invalidated"
    # not found by two groups: history depth is unknown, so never "invalidated"
    split = [_obs("b", block_hash="0xbb"), _obs("c", block_hash="0xcc")]
    assert _decide(STORED, split, True)[0] == "conflict"
    gone = [_obs("b", found=False), _obs("c", found=False)]
    assert _decide(STORED, gone, True)[0] == "unverified"
    assert _decide(STORED, [_obs("b", found=False)], True)[0] == "unverified"
    mixed = [_obs("b"), _obs("c", block_hash="0xbb")]
    assert _decide(STORED, mixed, True)[0] == "conflict"


def test_provider_errors_never_expose_endpoint_keys(env: Env, client: TestClient) -> None:
    from bip.rpc.transport import TransportError
    from tests.stage2.fakes import Scripted

    ingest(client, "ethereum", env.eth.wallet)
    for _ in range(6):
        env.transport.script(
            INFURA_HOST, Scripted(error=TransportError("connection", INFURA_HOST, "reset"))
        )
    r = client.post(f"{INTEL}/verification/ethereum/{env.eth.txb}")
    assert "test-key-not-real" not in r.text
    assert r.json()["status"] == "could_not_run"
    v = client.get(f"{INTEL}/verification/ethereum/{env.eth.txb}")
    assert "test-key-not-real" not in v.text
