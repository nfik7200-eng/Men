# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingParameterType=false, reportUnknownParameterType=false
# Justification: these tests mutate raw, untyped JSON-RPC payloads on purpose.
"""Stage 1 ingestion correctness: Solana transaction versions, placeholder repair,
token-account coverage, Ethereum oversized log responses and finality upgrades."""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bip.chains.common import ParseError, SourceInfo
from bip.chains.ethereum.adapter import EthereumAdapter
from bip.chains.solana import parse as sol
from bip.chains.solana.adapter import SolanaAdapter
from bip.rpc.jsonrpc import RpcError
from tests.stage2.fakes import SOL_HOST, FakeSolNode, _RpcFaultError
from tests.stage2.test_ingestion_api import API, Env

SSRC = SourceInfo("solana-public", "getTransaction", datetime(2026, 11, 3, tzinfo=UTC), None)
ESRC = SourceInfo("publicnode-eth", "eth_getLogs", datetime(2026, 11, 3, tzinfo=UTC), None)


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    e = Env(tmp_path)
    yield e
    e.engine.dispose()


def as_v1(raw: dict[str, Any]) -> dict[str, Any]:
    """Shape a legacy fixture as a v1 transaction: version 1 plus the message transactionConfig."""
    out = copy.deepcopy(raw)
    out["version"] = 1
    out["transaction"]["message"]["transactionConfig"] = {
        "computeUnitLimit": 200_000,
        "loadedAccountsDataSizeLimit": 65_536,
        "heapSize": None,
        "priorityFee": 0,
    }
    return out


def serve_v1_only(node: FakeSolNode, previous: Callable[..., object]) -> Callable[..., object]:
    """A node whose transaction sig_a is v1: it fails -32015 unless the client opts in to 1."""

    def handle(method: str, params: list[object]) -> object:
        if method == "getTransaction" and params[0] == node.sig_a:
            opts = params[1]
            assert isinstance(opts, dict)
            if opts.get("maxSupportedTransactionVersion") != 1:
                raise _RpcFaultError(
                    -32015, "Transaction version (1) is not supported by the requesting client"
                )
            return as_v1(node._tx_a())  # type: ignore[attr-defined]
        return previous(method, params)

    return handle


# ---------- Solana transaction versions (unit) ----------


def test_v1_transaction_parses_as_version_one() -> None:
    node = FakeSolNode()
    p = sol.parse_transaction(
        as_v1(node._tx_a()),
        wallet=node.wallet,
        src=SSRC,
        commitment="finalized",  # type: ignore[attr-defined]
    )
    assert p.tx.version == 1
    assert p.parse_status == "complete"
    kinds = sorted(k for _, k in p.transfers)
    assert kinds == ["solana_system_transfer_ix", "spl_transfer_checked_ix"]


def test_unknown_transaction_version_is_still_rejected() -> None:
    node = FakeSolNode()
    raw = copy.deepcopy(node._tx_a())  # type: ignore[attr-defined]
    raw["version"] = 2
    with pytest.raises(ParseError):
        sol.parse_transaction(raw, wallet=node.wallet, src=SSRC, commitment="finalized")


def test_adapter_requests_version_one_for_transactions_and_blocks() -> None:
    calls: list[tuple[str, object]] = []

    class Recording(SolanaAdapter):
        def call(self, method: str, params: list[object] | dict[str, object]):  # type: ignore[no-untyped-def]
            calls.append((method, params))
            if method == "getBlock":
                return None, SSRC
            return {}, SSRC

    a = Recording(None)  # type: ignore[arg-type]
    a.transaction("sig", "finalized")
    a.block_summary(10, "finalized")
    tx_opts = next(p for m, p in calls if m == "getTransaction")
    blk_opts = next(p for m, p in calls if m == "getBlock")
    assert isinstance(tx_opts, list)
    assert isinstance(blk_opts, list)
    assert tx_opts[1]["maxSupportedTransactionVersion"] == 1  # type: ignore[index]
    assert blk_opts[1]["maxSupportedTransactionVersion"] == 1  # type: ignore[index]


# ---------- Solana transaction versions and recovery (ingestion) ----------


def test_v1_transaction_is_ingested_with_transfers(env: Env) -> None:
    node = env.sol
    env.transport.nodes[SOL_HOST] = serve_v1_only(node, node.handle)
    with TestClient(env.app) as c:
        c.post(f"{API}/ingestion/wallet/solana/{node.wallet}?limit=1")
        transfers = c.get(f"{API}/wallets/solana/{node.wallet}/transfers?limit=200").json()["items"]
    assert any(t["tx_id"] == node.sig_a for t in transfers), "v1 transaction was not decoded"


def test_placeholder_left_by_a_failed_fetch_is_repaired_on_next_run(env: Env) -> None:
    node = env.sol
    orig = node.handle

    def unavailable(method: str, params: list[object]) -> object:
        if method == "getTransaction" and params[0] == node.sig_a:
            raise _RpcFaultError(-32011, "Transaction history is not available from this node")
        return orig(method, params)

    env.transport.nodes[SOL_HOST] = unavailable
    with TestClient(env.app) as c:
        c.post(f"{API}/ingestion/wallet/solana/{node.wallet}?limit=1")
        before = c.get(f"{API}/wallets/solana/{node.wallet}/transfers?limit=200").json()["items"]
        assert not any(t["tx_id"] == node.sig_a for t in before)

        env.transport.nodes[SOL_HOST] = orig
        c.post(f"{API}/ingestion/wallet/solana/{node.wallet}?limit=1")
        after = c.get(f"{API}/wallets/solana/{node.wallet}/transfers?limit=200").json()["items"]
    assert any(t["tx_id"] == node.sig_a for t in after), "placeholder was not repaired"


def test_token_accounts_are_discovered_and_reported(env: Env) -> None:
    node = env.sol
    orig = node.handle
    token_program = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

    def with_token_account(method: str, params: list[object]) -> object:
        if method == "getTokenAccountsByOwner":
            opts = params[1]
            assert isinstance(opts, dict)
            if opts.get("programId") == token_program:
                return {
                    "context": {"slot": node.head_slot},
                    "value": [{"pubkey": node.src_ata, "account": {}}],
                }
            return {"context": {"slot": node.head_slot}, "value": []}
        if method == "getSignaturesForAddress" and params[0] == node.src_ata:
            return [node.sigs[0]]
        return orig(method, params)

    env.transport.nodes[SOL_HOST] = with_token_account
    with TestClient(env.app) as c:
        job = c.post(f"{API}/ingestion/wallet/solana/{node.wallet}?limit=2").json()
    cov = job["coverage"]
    assert cov["token_accounts_discovered"] == 1
    assert cov["token_accounts_scanned"] == 1
    assert cov["token_account_discovery"] == "complete"
    assert any("token account" in s for s in cov["not_covered"])


def test_token_account_discovery_failure_is_not_reported_as_complete(env: Env) -> None:
    node = env.sol
    orig = node.handle

    def broken_discovery(method: str, params: list[object]) -> object:
        if method == "getTokenAccountsByOwner":
            raise _RpcFaultError(-32011, "not available")
        return orig(method, params)

    env.transport.nodes[SOL_HOST] = broken_discovery
    with TestClient(env.app) as c:
        job = c.post(f"{API}/ingestion/wallet/solana/{node.wallet}?limit=2").json()
    assert job["coverage"]["token_account_discovery"] != "complete"


# ---------- Ethereum oversized log responses (unit) ----------


def test_oversized_log_response_is_split_not_failed() -> None:
    calls: list[tuple[int, int]] = []

    class Oversized(EthereumAdapter):
        def call(self, method: str, params: list[object] | dict[str, object]):  # type: ignore[no-untyped-def]
            assert method == "eth_getLogs"
            assert isinstance(params, list)
            spec = params[0]
            assert isinstance(spec, dict)
            lo, hi = int(str(spec["fromBlock"]), 16), int(str(spec["toBlock"]), 16)
            calls.append((lo, hi))
            if hi - lo + 1 > 100:
                return [{"n": i} for i in range(10_001)], ESRC
            return [{"n": 1}], ESRC

    a = Oversized(None)  # type: ignore[arg-type]
    out, notes = a.transfer_logs_adaptive("0x" + "11" * 20, 0, 999, direction="from")
    assert len(out) == len([c for c in calls if c[1] - c[0] + 1 <= 100])
    assert notes, "a split must be recorded in the notes"


def test_unsplittable_oversized_block_fails_loudly() -> None:
    class AlwaysOversized(EthereumAdapter):
        def call(self, method: str, params: list[object] | dict[str, object]):  # type: ignore[no-untyped-def]
            return [{"n": i} for i in range(10_001)], ESRC

    a = AlwaysOversized(None)  # type: ignore[arg-type]
    with pytest.raises(RpcError) as info:
        a.transfer_logs_adaptive("0x" + "11" * 20, 5, 5, direction="to")
    assert info.value.error_class == "range_too_large"


# ---------- Ethereum finality upgrade (ingestion) ----------


def _eth_finality_rows(env: Env, wallet: str) -> list[tuple[str, str]]:
    with TestClient(env.app) as c:
        items = c.get(f"{API}/wallets/ethereum/{wallet}/transactions?limit=200").json()["items"]
    return [(t["tx_id"], t["finality"]) for t in items]


def test_stored_transactions_are_upgraded_to_finalized_when_canonical(env: Env) -> None:
    wallet = env.eth.wallet
    with TestClient(env.app) as c:
        c.post(f"{API}/ingestion/wallet/ethereum/{wallet}?limit=5")
        before = _eth_finality_rows(env, wallet)
        assert before, "fixture stored no transactions"
        assert all(f != "finalized" for _, f in before), "fixture must start unfinalized"
        env.eth.head += 500  # the finalized tag moves past the stored blocks
        job = c.post(f"{API}/ingestion/wallet/ethereum/{wallet}?limit=5").json()
    after = _eth_finality_rows(env, wallet)
    assert all(f == "finalized" for _, f in after)
    with TestClient(env.app) as c:
        transfers = c.get(f"{API}/wallets/ethereum/{wallet}/transfers?limit=50").json()["items"]
    assert transfers
    assert {t["finality"] for t in transfers} == {"finalized"}, (
        "transfer listing kept a stale label"
    )
    assert job["coverage"]["finality_transactions_upgraded"] >= len(before)
    assert job["coverage"]["finality_hash_mismatches"] == 0


def test_non_canonical_stored_block_is_reported_and_orphaned(env: Env) -> None:
    from sqlalchemy import text

    wallet = env.eth.wallet
    with TestClient(env.app) as c:
        c.post(f"{API}/ingestion/wallet/ethereum/{wallet}?limit=5")
        with env.engine.begin() as conn:
            conn.execute(
                text("UPDATE transactions SET block_hash = :h WHERE chain = 'ethereum'"),
                {"h": "0x" + "ab" * 32},
            )
        env.eth.head += 500
        job = c.post(f"{API}/ingestion/wallet/ethereum/{wallet}?limit=5").json()
    rows = _eth_finality_rows(env, wallet)
    assert rows, "fixture stored no transactions"
    assert all(f == "orphaned" for _, f in rows), "a non-canonical block was not orphaned"
    assert job["coverage"]["finality_hash_mismatches"] >= 1
    assert any(e["error_code"] == "reorg_detected" for e in job.get("errors", []))
