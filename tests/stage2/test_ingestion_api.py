from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bip.api.app import create_app
from bip.config import Settings, load_settings
from bip.db.engine import create_db_engine
from tests.api.conftest import roomy_guard
from tests.stage2.fakes import ETH_HOST, SOL_HOST, FakeEthNode, FakeSolNode, FakeTransport

API = "/api/v1"


class Env:
    def __init__(self, tmp_path: Path, **settings: object) -> None:
        self.eth = FakeEthNode()
        self.sol = FakeSolNode()
        self.transport = FakeTransport({ETH_HOST: self.eth.handle, SOL_HOST: self.sol.handle})
        self.settings: Settings = load_settings(data_root=tmp_path / "data", env="test", **settings)
        self.engine = create_db_engine(self.settings)
        self.app = create_app(
            self.settings,
            engine=self.engine,
            transport=self.transport,
            disk_guard=roomy_guard(self.settings.resolved_data_root),
            environ={},
            rpc_sleep=lambda _s: None,
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


# ---------- providers and chains ----------


def test_health_check_makes_provider_healthy_and_chain_connected(
    env: Env, client: TestClient
) -> None:
    resp = client.post(f"{API}/providers/publicnode-eth/health-check")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["head_block"] == env.eth.head
    assert body["observed_network"] == "0x1"
    p = client.get(f"{API}/providers/publicnode-eth").json()
    assert p["lifecycle"] == "healthy"
    assert p["recent_checks"][0]["ok"] is True
    eth = next(c for c in client.get(f"{API}/chains").json() if c["chain"] == "ethereum")
    assert eth["connected"] is True
    assert eth["latest_observation"]["head_block"] == env.eth.head
    sol = next(c for c in client.get(f"{API}/chains").json() if c["chain"] == "solana")
    assert sol["connected"] is False  # Solana not contacted yet


def test_health_check_reports_network_mismatch(env: Env, client: TestClient) -> None:
    env.eth.chain_id = "0x2105"  # Base, not Ethereum
    body = client.post(f"{API}/providers/publicnode-eth/health-check").json()
    assert body["ok"] is False
    assert body["outcome"] in {"network_mismatch", "RoutingFailedError", "NoEligibleProviderError"}
    assert client.get(f"{API}/providers/publicnode-eth").json()["lifecycle"] == "unavailable"


def test_health_check_rejects_unusable_and_non_rpc(client: TestClient) -> None:
    assert client.post(f"{API}/providers/helius-sol/health-check").status_code == 409
    assert client.post(f"{API}/providers/etherscan/health-check").status_code == 409
    assert client.post(f"{API}/providers/nope/health-check").status_code == 404


def test_disable_and_enable_provider(client: TestClient) -> None:
    off = client.patch(
        f"{API}/providers/solana-public", json={"enabled": False, "reason": "testing"}
    )
    assert off.json()["lifecycle"] == "disabled"
    resp = client.get(f"{API}/chains/solana/latest-block")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "no_eligible_provider"
    on = client.patch(f"{API}/providers/solana-public", json={"enabled": True})
    assert on.json()["lifecycle"] in {"untested", "healthy"}


def test_latest_block_and_network(env: Env, client: TestClient) -> None:
    assert client.get(f"{API}/chains/ethereum/network").status_code == 404
    eth = client.get(f"{API}/chains/ethereum/latest-block").json()
    assert eth["head_block"] == env.eth.head
    assert eth["finalized_block"] == env.eth.head - 64
    assert eth["identity"] == "chain id 1 (Ethereum mainnet)"
    sol = client.get(f"{API}/chains/solana/latest-block").json()
    assert sol["head_slot"] == env.sol.head_slot
    assert sol["commitment"] == "finalized"
    assert sol["head_hash"] is not None  # head slot skipped -> previous slot's block used
    assert client.get(f"{API}/chains/solana/network").json()["head_slot"] == env.sol.head_slot
    assert client.get(f"{API}/chains/base/latest-block").status_code == 422


# ---------- Ethereum ingestion ----------


def test_ethereum_wallet_ingestion_end_to_end(env: Env, client: TestClient) -> None:
    w = env.eth.wallet
    job = client.post(f"{API}/ingestion/wallet/ethereum/{w.upper().replace('0X', '0x')}?limit=20")
    assert job.status_code == 201
    j = job.json()
    assert j["status"] == "succeeded", j
    assert j["providers"] == ["publicnode-eth"]
    assert j["transactions_stored"] == 2
    assert j["transfers_stored"] == 3  # 2 ERC-20 logs + 1 native ETH value
    assert j["coverage"]["balance_wei_at_head"] == str(5 * 10**18)
    assert "internal" in " ".join(j["coverage"]["not_covered"])
    assert client.get(f"{API}/ingestion/jobs/{j['id']}").json()["status"] == "succeeded"

    txs = client.get(f"{API}/wallets/ethereum/{w}/transactions").json()
    assert txs["total"] == 2
    first = txs["items"][0]
    assert first["block_number"] == env.eth.head - 5
    assert first["source_provider"] == "publicnode-eth"
    assert first["verification_status"] == "unverified"
    assert first["finality"] == "observed"  # newer than the finalized block
    assert first["raw_ref"] is not None
    transfers = client.get(f"{API}/wallets/ethereum/{w}/transfers").json()
    assert sorted(t["observation_kind"] for t in transfers["items"]) == [
        "erc20_transfer_log",
        "erc20_transfer_log",
        "native_tx_value",
    ]
    profile = client.get(f"{API}/wallets/ethereum/{w}").json()
    assert profile["transaction_count"] == 2

    again = client.post(f"{API}/ingestion/wallet/ethereum/{w}").json()
    assert again["transactions_stored"] == 0  # idempotent
    assert again["transfers_stored"] == 0
    assert client.get(f"{API}/wallets/ethereum/{w}/transactions").json()["total"] == 2
    summary = client.get(f"{API}/dashboard/summary").json()
    assert summary["transactions_stored"] == 2
    assert summary["ingestion"]["runs_total"] == 2


def test_ethereum_log_range_is_split_adaptively(tmp_path: Path) -> None:
    e = Env(tmp_path, ingest={"ethereum_log_chunk_blocks": 2000, "ethereum_lookback_blocks": 2000})
    e.eth.max_log_range = 1000
    with TestClient(e.app) as c:
        j = c.post(f"{API}/ingestion/wallet/ethereum/{e.eth.wallet}").json()
    assert j["status"] == "succeeded", j
    assert j["transactions_stored"] == 2
    assert any("split" in n for n in j["coverage"]["range_notes"])
    e.engine.dispose()


def test_ethereum_reorg_and_missing_receipt_are_recorded(env: Env, client: TestClient) -> None:
    env.eth.reorg_tx = env.eth.txa
    env.eth.break_receipt_for = env.eth.txb
    j = client.post(f"{API}/ingestion/wallet/ethereum/{env.eth.wallet}").json()
    assert j["status"] == "partial"
    codes = {e["error_code"] for e in j["errors"]}
    assert "reorg_detected" in codes
    assert "receipt_missing" in codes
    txs = client.get(f"{API}/wallets/ethereum/{env.eth.wallet}/transactions").json()["items"]
    assert [t["parse_status"] for t in txs] == ["partial"]  # txb kept, flagged partial; txa refused


def test_budget_exhaustion_gives_partial_run(tmp_path: Path) -> None:
    e = Env(tmp_path, ingest={"max_rpc_calls_per_run": 8})
    with TestClient(e.app) as c:
        j = c.post(f"{API}/ingestion/wallet/ethereum/{e.eth.wallet}").json()
    assert j["status"] in {"partial", "failed"}
    assert any(x["error_code"] == "budget_exhausted" for x in j["errors"])
    e.engine.dispose()


# ---------- Solana ingestion ----------


def test_solana_wallet_ingestion_end_to_end(env: Env, client: TestClient) -> None:
    w = env.sol.wallet
    j = client.post(f"{API}/ingestion/wallet/solana/{w}?limit=20").json()
    assert j["providers"] == ["solana-public"]
    assert j["transactions_seen"] == 3
    # sig_c: provider says history not available -> recorded error + signature-only row
    assert j["status"] == "partial", j
    assert any(e["error_code"] == "not_available" for e in j["errors"])
    txs = client.get(f"{API}/wallets/solana/{w}/transactions").json()
    assert txs["total"] == 3
    by_sig = {t["tx_id"]: t for t in txs["items"]}
    assert by_sig[env.sol.sig_a]["parse_status"] == "complete"
    assert by_sig[env.sol.sig_b]["unknown_instruction_count"] == 1
    assert by_sig[env.sol.sig_b]["block_time"] is None
    assert by_sig[env.sol.sig_c]["parse_status"] == "failed"
    assert by_sig[env.sol.sig_a]["slot"] == env.sol.head_slot - 10
    assert by_sig[env.sol.sig_a]["finality"] == "finalized"
    transfers = client.get(f"{API}/wallets/solana/{w}/transfers").json()["items"]
    # SPL transfer moves tokens between token accounts; included because the wallet owns the source
    assert sorted(t["observation_kind"] for t in transfers) == [
        "solana_system_transfer_ix",
        "spl_transfer_checked_ix",
    ]
    assert client.get(f"{API}/wallets/solana/{w}").json()["transfer_count"] == 2
    bcs = client.get(f"{API}/wallets/solana/{w}/balance-changes").json()["items"]
    kinds = sorted(b["observation_kind"] for b in bcs)
    assert kinds == ["native_balance_change", "native_balance_change", "token_balance_change"]
    again = client.post(f"{API}/ingestion/wallet/solana/{w}").json()
    assert again["transactions_seen"] == 0  # cursor: nothing new, no older history
    assert client.get(f"{API}/wallets/solana/{w}/transactions").json()["total"] == 3


def test_solana_cursor_resumes_older_history(tmp_path: Path) -> None:
    e = Env(tmp_path)
    with TestClient(e.app) as c:
        first = c.post(f"{API}/ingestion/wallet/solana/{e.sol.wallet}?limit=1").json()
        assert first["transactions_seen"] == 1
        second = c.post(f"{API}/ingestion/wallet/solana/{e.sol.wallet}?limit=1").json()
        assert second["transactions_seen"] == 1
        txs = c.get(f"{API}/wallets/solana/{e.sol.wallet}/transactions").json()["items"]
        assert {t["tx_id"] for t in txs} == {e.sol.sig_a, e.sol.sig_b}
    e.engine.dispose()


# ---------- validation, limits, safety ----------


def test_ingestion_input_validation(client: TestClient) -> None:
    assert client.post(f"{API}/ingestion/wallet/ethereum/0x123").status_code == 422
    assert client.post(f"{API}/ingestion/wallet/dogecoin/abc").status_code == 422
    assert client.post(f"{API}/ingestion/wallet/base/{'0x' + 'aa' * 20}").status_code == 422
    assert (
        client.post(f"{API}/ingestion/wallet/ethereum/{'0x' + 'aa' * 20}?limit=500").status_code
        == 422
    )
    assert client.get(f"{API}/ingestion/jobs/999").status_code == 404


def test_concurrent_ingestion_is_refused(env: Env, client: TestClient) -> None:
    ctx = env.app.state.bip
    ctx.ingest_lock.acquire()
    try:
        resp = client.post(f"{API}/ingestion/wallet/ethereum/{env.eth.wallet}")
    finally:
        ctx.ingest_lock.release()
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ingestion_busy"


def test_no_provider_gives_clear_error(env: Env, client: TestClient) -> None:
    client.patch(f"{API}/providers/publicnode-eth", json={"enabled": False})
    j = client.post(f"{API}/ingestion/wallet/ethereum/{env.eth.wallet}").json()
    assert j["status"] == "failed"
    assert j["errors"][0]["error_code"] == "no_eligible_provider"
    assert env.eth.calls == []


def test_raw_payloads_are_stored_and_referenced(env: Env, client: TestClient) -> None:
    client.post(f"{API}/ingestion/wallet/ethereum/{env.eth.wallet}")
    tx = client.get(f"{API}/wallets/ethereum/{env.eth.wallet}/transactions").json()["items"][0]
    raw = env.app.state.bip.ingestor.store.raw_payload(tx["raw_ref"])
    assert raw is not None
    assert env.eth.txb.encode() in raw
