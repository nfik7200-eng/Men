# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportGeneralTypeIssues=false, reportMissingParameterType=false, reportUnknownParameterType=false
# Justification: these regression tests mutate raw, untyped JSON-RPC payloads on purpose.
"""Regression tests for defects found by the Stage 2 independent review (each failed first)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bip.chains.common import ParseError, SourceInfo
from bip.chains.ethereum import parse as eth
from bip.chains.solana import parse as sol
from bip.config import load_settings
from bip.contracts.provenance import Finality
from bip.db.engine import create_db_engine, database_path, migrate
from tests.stage2.fakes import FakeEthNode, FakeSolNode, h32, sig
from tests.stage2.test_ingestion_api import API, Env

SSRC = SourceInfo("solana-public", "getTransaction", datetime(2026, 11, 3, tzinfo=UTC), None)
ESRC = SourceInfo(
    "publicnode-eth", "eth_getTransactionByHash", datetime(2026, 11, 3, tzinfo=UTC), None
)


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    e = Env(tmp_path)
    yield e
    e.engine.dispose()


# 1. Solana cursor must not skip signatures when more new ones than `limit` arrive
def test_solana_cursor_never_skips_when_new_signatures_exceed_limit(env: Env) -> None:
    node = env.sol
    with TestClient(env.app) as c:
        c.post(f"{API}/ingestion/wallet/solana/{node.wallet}?limit=1")  # cursor = sig_a only
        # 3 brand-new signatures arrive, newer than sig_a
        new = [
            {
                "signature": sig(0x40 + i),
                "slot": node.head_slot + 10 - i,
                "blockTime": None,
                "err": None,
                "confirmationStatus": "finalized",
            }
            for i in range(3)
        ]
        node.sigs = new + node.sigs
        tx_a = node._tx_a()  # pyright: ignore[reportPrivateUsage]
        orig = node.handle

        def handle(method: str, params: list[object]) -> object:
            if method == "getTransaction" and params[0] in {s["signature"] for s in new}:
                slot = next(s["slot"] for s in new if s["signature"] == params[0])
                return {
                    **tx_a,
                    "slot": slot,
                    "transaction": {**tx_a["transaction"], "signatures": [params[0]]},
                }  # type: ignore[dict-item]
            return orig(method, params)

        env.transport.nodes["api.mainnet.solana.com"] = handle
        seen: set[str] = set()
        for _ in range(6):
            c.post(f"{API}/ingestion/wallet/solana/{node.wallet}?limit=1")
            items = c.get(f"{API}/wallets/solana/{node.wallet}/transactions?limit=200").json()[
                "items"
            ]
            seen = {t["tx_id"] for t in items}
        assert {s["signature"] for s in new} <= seen, "a new signature was skipped by the cursor"


# 2. Ethereum: transactions dropped by the per-run limit must be picked up by the next run
def test_ethereum_truncated_window_is_resumed(env: Env) -> None:
    with TestClient(env.app) as c:
        first = c.post(f"{API}/ingestion/wallet/ethereum/{env.eth.wallet}?limit=1").json()
        assert first["transactions_stored"] == 1
        second = c.post(f"{API}/ingestion/wallet/ethereum/{env.eth.wallet}?limit=1").json()
        assert second["transactions_stored"] == 1, "the older tx in the scanned window was lost"
        total = c.get(f"{API}/wallets/ethereum/{env.eth.wallet}/transactions").json()["total"]
    assert total == 2


# 3. Failed transactions must not produce transfer observations
def test_failed_solana_tx_yields_no_transfers() -> None:
    node = FakeSolNode()
    raw = node._tx_a()  # pyright: ignore[reportPrivateUsage]
    raw["meta"] = {**raw["meta"], "err": {"InstructionError": [0, "Custom"]}}  # type: ignore[dict-item]
    p = sol.parse_transaction(raw, wallet=node.wallet, src=SSRC, commitment="finalized")
    assert p.tx.status is not None
    assert p.tx.status.value == "failed"
    assert p.transfers == []
    assert any("failed" in i for i in p.issues)


def test_failed_ethereum_tx_has_no_native_transfer() -> None:
    node = FakeEthNode()
    receipt = node.handle("eth_getTransactionReceipt", [node.txb])
    assert isinstance(receipt, dict)
    parsed = eth.parse_transaction(
        node.txs[node.txb],
        {**receipt, "status": "0x0"},
        block_time=None,
        src=ESRC,
        finality=Finality.OBSERVED,
    )
    assert eth.native_transfer(parsed) is None


# 4. Out-of-range values must be ParseErrors, and runs must always finish
@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.update(blockTime=300_000_000_000),
        lambda t: t.update(slot=2**63),
        lambda t: t["meta"]["preTokenBalances"][0]["uiTokenAmount"].update(amount="²"),
    ],
)
def test_solana_out_of_range_values_are_parse_errors(mutate) -> None:
    node = FakeSolNode()
    raw = node._tx_a()  # pyright: ignore[reportPrivateUsage]
    mutate(raw)
    with pytest.raises(ParseError):
        sol.parse_transaction(raw, wallet=node.wallet, src=SSRC, commitment="finalized")


def test_ethereum_out_of_range_values_are_parse_errors() -> None:
    node = FakeEthNode()
    with pytest.raises(ParseError):
        eth.parse_block_header({**node.blocks[node.head], "timestamp": "0x" + "f" * 60})
    with pytest.raises(ParseError):
        eth.parse_block_header({**node.blocks[node.head], "number": hex(2**63)})


def test_run_always_finishes_even_on_unexpected_errors(env: Env) -> None:
    def boom(method: str, params: list[object]) -> object:
        if method == "eth_getBalance":
            raise RuntimeError("unexpected")
        return env.eth.handle(method, params)

    env.transport.nodes["ethereum-rpc.publicnode.com"] = boom
    with TestClient(env.app, raise_server_exceptions=False) as c:
        resp = c.post(f"{API}/ingestion/wallet/ethereum/{env.eth.wallet}")
        assert resp.status_code == 201
        assert resp.json()["status"] in {"failed", "partial"}
        assert resp.json()["errors"][-1]["error_code"] == "internal_error"


def test_latest_block_with_malformed_provider_data_is_502(env: Env) -> None:
    env.eth.blocks[env.eth.head]["timestamp"] = "0x" + "f" * 60
    with TestClient(env.app) as c:
        resp = c.get(f"{API}/chains/ethereum/latest-block")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream_invalid"


# 5. Migration v2 must recover from a partially applied previous attempt
def test_migration_v2_recovers_from_partial_application(tmp_path: Path) -> None:
    settings = load_settings(data_root=tmp_path / "d", env="test")
    path = database_path(settings)
    path.parent.mkdir(parents=True)
    con = sqlite3.connect(path)
    con.executescript(Path("tests/fixtures/schema_v1.sql").read_text())
    con.execute(
        "INSERT INTO schema_migrations VALUES (1,'2026-11-03T00:00:00.000000+00:00','initial')"
    )
    # simulate a crash midway through v2: two columns added, version still 1
    con.execute("ALTER TABLE transactions ADD COLUMN network VARCHAR(32)")
    con.execute("ALTER TABLE transactions ADD COLUMN block_hash VARCHAR(100)")
    con.commit()
    con.close()
    engine = create_db_engine(settings)
    assert migrate(engine) == 3
    engine.dispose()


def test_provider_error_text_is_redacted_in_stored_errors(env: Env) -> None:
    secret = "https://rpc.example.com/v2/" + "Zz9" * 8
    with TestClient(env.app) as c:
        store = env.app.state.bip.ingestor.store
        run_id = store.start_run("ethereum", env.eth.wallet, 1)
        store.add_error(run_id, stage="rpc", error_code="x", detail=f"provider said {secret}")
        detail = c.get(f"{API}/ingestion/jobs/{run_id}").json()["errors"][0]["detail"]
    assert "Zz9Zz9" not in detail
    _ = h32
