# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingParameterType=false, reportUnknownParameterType=false
# Justification: these tests mutate raw, untyped JSON-RPC payloads on purpose.
"""Regression tests for gaps found by the independent Stage 1 review.

Each of these passed neither before the fix nor under the original 523-test suite:
incoming token-account transfers, orphan handling in intelligence, receipt-less Ethereum rows,
unbounded retries of unavailable rows, and coverage lost when the call budget ran out.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from bip.chains.common import SourceInfo
from bip.db.repository import Repository
from bip.ingest.wallet import _receipt_logs
from bip.intel.dataset import load_wallet
from tests.stage2.fakes import (
    ETH_HOST,
    SOL_HOST,
    TOKEN,
    TRANSFER,
    _RpcFaultError,
    addr,
    key,
    sig,
    topic,
    transfer_checked_data,
)
from tests.stage2.test_ingestion_api import API, Env

TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PQnBqCXEpPxuEb"


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    e = Env(tmp_path)
    yield e
    e.engine.dispose()


def incoming_to_token_account(node: Any, signature: str) -> dict[str, Any]:
    """A third party sends tokens to the wallet's token account (wallet is not an account key)."""
    sender, sender_ata = key(10), key(11)
    return {
        "slot": node.head_slot - 5,
        "blockTime": 1_760_000_300,
        "transaction": {
            "signatures": [signature],
            "message": {
                "accountKeys": [sender, sender_ata, node.dst_ata, node.mint, TOKEN],
                "instructions": [
                    {
                        "programIdIndex": 4,
                        "accounts": [1, 3, 2, 0],
                        "data": transfer_checked_data(1_000_000, 6),
                    }
                ],
            },
        },
        "meta": {
            "err": None,
            "fee": 5000,
            "preBalances": [10**9, 2039280, 2039280, 1461600, 1],
            "postBalances": [10**9 - 5000, 2039280, 2039280, 1461600, 1],
            "preTokenBalances": [
                {
                    "accountIndex": 1,
                    "mint": node.mint,
                    "owner": sender,
                    "uiTokenAmount": {"amount": "5000000", "decimals": 6},
                },
                {
                    "accountIndex": 2,
                    "mint": node.mint,
                    "owner": node.wallet,
                    "uiTokenAmount": {"amount": "0", "decimals": 6},
                },
            ],
            "postTokenBalances": [
                {
                    "accountIndex": 1,
                    "mint": node.mint,
                    "owner": sender,
                    "uiTokenAmount": {"amount": "4000000", "decimals": 6},
                },
                {
                    "accountIndex": 2,
                    "mint": node.mint,
                    "owner": node.wallet,
                    "uiTokenAmount": {"amount": "1000000", "decimals": 6},
                },
            ],
            "innerInstructions": [],
        },
        "version": "legacy",
    }


def serve_token_account(env: Env, signature: str) -> None:
    node = env.sol
    orig = node.handle

    def handle(method: str, params: list[object]) -> object:
        if method == "getTokenAccountsByOwner":
            opts = params[1]
            assert isinstance(opts, dict)
            accounts = [{"pubkey": node.dst_ata, "account": {}}]
            value = accounts if opts.get("programId") == TOKEN else []
            return {"context": {"slot": node.head_slot}, "value": value}
        if method == "getSignaturesForAddress" and params[0] == node.dst_ata:
            return [
                {
                    "signature": signature,
                    "slot": node.head_slot - 5,
                    "blockTime": 1_760_000_300,
                    "err": None,
                    "confirmationStatus": "finalized",
                }
            ]
        if method == "getTransaction" and params[0] == signature:
            return incoming_to_token_account(node, signature)
        return orig(method, params)

    env.transport.nodes[SOL_HOST] = handle


# ---------- Solana: wallet-level aggregation of token-account activity ----------


def test_incoming_token_account_transfer_reaches_wallet_views_and_intelligence(env: Env) -> None:
    """The wallet's own history cannot contain this transaction (its key is not an account key)."""
    node = env.sol
    incoming = sig(0x77)
    serve_token_account(env, incoming)
    with TestClient(env.app) as c:
        job = c.post(f"{API}/ingestion/wallet/solana/{node.wallet}?limit=2").json()
        assert job["coverage"]["token_accounts_scanned"] == 1
        txs = c.get(f"{API}/wallets/solana/{node.wallet}/transactions?limit=200").json()["items"]
        transfers = c.get(f"{API}/wallets/solana/{node.wallet}/transfers?limit=200").json()["items"]
    assert incoming in {t["tx_id"] for t in txs}, "token-account transaction missing from wallet"
    assert incoming in {t["tx_id"] for t in transfers}, "token-account transfer missing from wallet"
    ds = load_wallet(Repository(env.engine), "solana", node.wallet, 1000)
    assert incoming in {t.tx_id for t in ds.transactions}, (
        "intelligence dataset missed the transfer"
    )


# ---------- Ethereum: orphans, finality and derived data ----------


def _scalar(env: Env, sql: str, **params: object) -> Any:
    with env.engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def test_orphaned_block_is_excluded_from_intelligence_and_derived_data_is_invalidated(
    env: Env,
) -> None:
    wallet = env.eth.wallet
    with TestClient(env.app) as c:
        c.post(f"{API}/ingestion/wallet/ethereum/{wallet}?limit=5")
        analysis = c.post(f"{API}/intelligence/analyze/ethereum/{wallet}")
        assert analysis.status_code == 201
        before = load_wallet(Repository(env.engine), "ethereum", wallet, 1000)
        assert before.transactions, "fixture must have stored transactions"
        with env.engine.begin() as conn:
            conn.execute(
                text("UPDATE transactions SET block_hash = :h WHERE chain = 'ethereum'"),
                {"h": "0x" + "ab" * 32},
            )
        env.eth.head += 500  # the stored blocks are now below the finalized height
        job = c.post(f"{API}/ingestion/wallet/ethereum/{wallet}?limit=5").json()

    cov = job["coverage"]
    assert cov["finality_transactions_orphaned"] >= len(before.transactions)
    assert cov["analyses_marked_stale"] >= 1
    assert _scalar(env, "SELECT COUNT(*) FROM transactions WHERE finality = 'orphaned'") >= 1
    assert _scalar(env, "SELECT COUNT(*) FROM transactions WHERE finality = 'finalized'") == 0
    # derived data: the analysis left the current views, and the evidence is flagged invalidated
    assert _scalar(env, "SELECT COUNT(*) FROM analysis_runs WHERE status = 'succeeded'") == 0
    assert (
        _scalar(env, "SELECT COUNT(*) FROM evidence_verifications WHERE outcome = 'invalidated'")
        >= 1
    )
    # downstream intelligence no longer sees the orphaned transactions
    after = load_wallet(Repository(env.engine), "ethereum", wallet, 1000)
    orphaned = {t.tx_id for t in before.transactions}
    assert not orphaned & {t.tx_id for t in after.transactions}
    assert not {tr.tx_id for tr in after.transfers} & orphaned
    # the audit row is kept and is still listed, labelled orphaned
    with TestClient(env.app) as c:
        listed = c.get(f"{API}/wallets/ethereum/{wallet}/transactions?limit=50").json()["items"]
    assert listed
    assert {t["finality"] for t in listed} == {"orphaned"}
    with TestClient(env.app) as c:
        transfers = c.get(f"{API}/wallets/ethereum/{wallet}/transfers?limit=50").json()["items"]
    assert transfers, "fixture must have stored transfers"
    assert {t["finality"] for t in transfers} == {"orphaned"}, "transfer listing kept a stale label"


def test_orphaned_rows_are_not_rechecked_every_run(env: Env) -> None:
    wallet = env.eth.wallet
    with TestClient(env.app) as c:
        c.post(f"{API}/ingestion/wallet/ethereum/{wallet}?limit=5")
        with env.engine.begin() as conn:
            conn.execute(text("UPDATE transactions SET block_hash = :h"), {"h": "0x" + "cd" * 32})
        env.eth.head += 500
        first = c.post(f"{API}/ingestion/wallet/ethereum/{wallet}?limit=5").json()
        second = c.post(f"{API}/ingestion/wallet/ethereum/{wallet}?limit=5").json()
    assert first["coverage"]["finality_hash_mismatches"] >= 1
    assert second["coverage"]["finality_blocks_checked"] == 0, "orphaned rows were re-checked"


def test_ethereum_transaction_without_receipt_is_completed_when_the_receipt_arrives(
    env: Env,
) -> None:
    wallet = env.eth.wallet
    node = env.eth
    orig = node.handle

    def no_receipts(method: str, params: list[object]) -> object:
        return None if method == "eth_getTransactionReceipt" else orig(method, params)

    env.transport.nodes[ETH_HOST] = no_receipts
    with TestClient(env.app) as c:
        c.post(f"{API}/ingestion/wallet/ethereum/{wallet}?limit=5")
        partial = _scalar(env, "SELECT COUNT(*) FROM transactions WHERE parse_status = 'partial'")
        assert partial >= 1, "fixture must start with receipt-less rows"
        env.transport.nodes[ETH_HOST] = orig
        # Move the head past the lookback window so the log scan can no longer re-find the old
        # rows: only the dedicated repair pass can complete them.
        env.eth.head += env.settings.ingest.ethereum_lookback_blocks + 100
        job = c.post(f"{API}/ingestion/wallet/ethereum/{wallet}?limit=5").json()
    assert job["coverage"]["partial_transactions_retried"] == partial
    assert _scalar(env, "SELECT COUNT(*) FROM transactions WHERE parse_status = 'partial'") == 0
    assert _scalar(env, "SELECT COUNT(*) FROM transactions WHERE parse_status = 'complete'") >= 1


def test_receipt_logs_keep_only_the_wallets_transfer_logs() -> None:
    src = SourceInfo("p", "eth_getTransactionReceipt", datetime(2026, 11, 8, tzinfo=UTC), None)
    wallet, other = addr(1), addr(2)

    def log(index: int, topics: list[str]) -> dict[str, object]:
        return {
            "blockHash": "0x" + "11" * 32,
            "logIndex": hex(index),
            "address": addr(9),
            "topics": topics,
            "data": "0x" + "00" * 32,
        }

    receipt = {
        "logs": [
            log(0, [TRANSFER, topic(wallet), topic(other)]),  # wallet is the sender
            log(1, [TRANSFER, topic(other), topic(wallet)]),  # wallet is the recipient
            log(2, [TRANSFER, topic(other), topic(other)]),  # unrelated transfer
            log(3, ["0x" + "22" * 32, topic(wallet)]),  # not a Transfer
        ]
    }
    found = _receipt_logs(receipt, wallet, src)
    assert sorted(i for _, i in found) == [0, 1]
    assert _receipt_logs(None, wallet, src) == {}


# ---------- Solana: bounded retries and honest coverage under a budget ----------


def test_unavailable_transaction_is_not_retried_forever(env: Env) -> None:
    node = env.sol
    orig = node.handle
    attempts = {"n": 0}

    def unavailable(method: str, params: list[object]) -> object:
        if method == "getTransaction" and params[0] == node.sig_a:
            attempts["n"] += 1
            raise _RpcFaultError(-32011, "Transaction history is not available from this node")
        return orig(method, params)

    env.transport.nodes[SOL_HOST] = unavailable
    with TestClient(env.app) as c:
        for _ in range(6):
            c.post(f"{API}/ingestion/wallet/solana/{node.wallet}?limit=1")
        after_six = attempts["n"]
        for _ in range(4):
            c.post(f"{API}/ingestion/wallet/solana/{node.wallet}?limit=1")
    assert after_six <= env.settings.ingest.repair_max_attempts
    assert attempts["n"] == after_six, "an unavailable transaction kept being re-fetched"


def test_token_scan_coverage_survives_call_budget_exhaustion(tmp_path: Path) -> None:
    # head slot + balance + wallet signatures + 3 transactions use most of a small budget, so the
    # token-account discovery or scan is cut off part-way.
    e = Env(tmp_path, ingest={"max_rpc_calls_per_run": 9})
    try:
        serve_token_account(e, sig(0x78))
        with TestClient(e.app) as c:
            job = c.post(f"{API}/ingestion/wallet/solana/{e.sol.wallet}?limit=3").json()
        cov = job["coverage"]
        assert job["status"] == "partial"
        assert cov["token_account_discovery"] != "complete" or cov["token_accounts_scanned"] < 1
        assert any("token account" in s for s in cov["not_covered"])
    finally:
        e.engine.dispose()
