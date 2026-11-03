# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false
# Justification: these fixtures build and mutate raw, untyped JSON-RPC payloads on purpose.
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from bip.chains.common import ParseError, SourceInfo
from bip.chains.ethereum import parse as eth
from bip.chains.solana import parse as sol
from bip.contracts.provenance import Finality, VerificationStatus
from bip.contracts.transfers import TokenStandard, TransferClassification
from tests.stage2.fakes import TRANSFER, FakeEthNode, FakeSolNode, addr, h32, topic

SRC = SourceInfo("publicnode-eth", "eth_getLogs", datetime(2026, 11, 3, tzinfo=UTC), "a" * 64)


def test_quantities_and_identifiers_are_strict() -> None:
    assert eth.quantity("0x1b4", "f") == 436
    for bad in ("1b4", "0x", "0xzz", 436, None, "0x" + "f" * 65):
        with pytest.raises(ParseError):
            eth.quantity(bad, "f")
    with pytest.raises(ParseError):
        eth.address("0x123", "a")
    assert eth.address("0x" + "AB" * 20, "a") == "0x" + "ab" * 20


def test_block_header() -> None:
    node = FakeEthNode()
    h = eth.parse_block_header(node.blocks[node.head])
    assert h.number == node.head
    assert h.tx_count == 1
    assert h.timestamp.tzinfo is not None
    block = eth.to_block_contract(h, SRC, Finality.OBSERVED)
    assert block.provenance.position is not None
    assert block.provenance.verification_status is VerificationStatus.UNVERIFIED


def test_transaction_with_receipt_and_native_transfer() -> None:
    node = FakeEthNode()
    raw = node.txs[node.txb]
    receipt = node.handle("eth_getTransactionReceipt", [node.txb])
    parsed = eth.parse_transaction(
        raw, receipt, block_time=None, src=SRC, finality=Finality.OBSERVED
    )
    assert parsed.tx.status is not None
    assert parsed.tx.status.value == "success"
    assert parsed.gas_used == 51_000
    native = eth.native_transfer(parsed)
    assert native is not None
    assert native.amount_raw == 2 * 10**18
    assert native.standard is TokenStandard.NATIVE
    a = eth.parse_transaction(
        node.txs[node.txa], None, block_time=None, src=SRC, finality=Finality.OBSERVED
    )
    assert a.tx.status is None  # no receipt: unknown, not assumed
    assert eth.native_transfer(a) is None  # zero value


def test_receipt_mismatch_detected() -> None:
    node = FakeEthNode()
    original = node.handle("eth_getTransactionReceipt", [node.txb])
    assert isinstance(original, dict)
    receipt: dict[str, object] = {**original, "blockHash": h32(0xDEAD)}
    with pytest.raises(ParseError, match="reorg"):
        eth.parse_transaction(
            node.txs[node.txb], receipt, block_time=None, src=SRC, finality=Finality.OBSERVED
        )
    pending = {**node.txs[node.txb], "blockNumber": None, "blockHash": None}
    with pytest.raises(ParseError, match="pending"):
        eth.parse_transaction(pending, None, block_time=None, src=SRC, finality=Finality.OBSERVED)


def test_erc20_and_erc721_decoding() -> None:
    node = FakeEthNode()
    lg = eth.parse_log(node.logs[0], SRC, block_time=None, finality=Finality.OBSERVED)
    t, reason = eth.decode_transfer_log(lg)
    assert reason is None
    assert t is not None
    assert t.standard is TokenStandard.ERC20
    assert t.amount_raw == 1_500_000
    assert t.decimals is None  # unknown, never assumed 18
    assert t.from_address is not None
    assert t.from_address.address == node.wallet
    nft_raw = {
        **node.logs[0],
        "topics": [TRANSFER, topic(addr(0)), topic(node.wallet), h32(42)],
        "data": "0x",
    }
    nft, _ = eth.decode_transfer_log(
        eth.parse_log(nft_raw, SRC, block_time=None, finality=Finality.OBSERVED)
    )
    assert nft is not None
    assert nft.standard is TokenStandard.ERC721
    assert nft.token_id == 42
    assert nft.classification is TransferClassification.MINT


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ({"topics": [TRANSFER, "0x" + "f" * 64, topic(addr(1))]}, "malformed"),
        ({"data": "0x" + "00" * 31}, "non-standard"),
        ({"removed": True}, "removed"),
    ],
)
def test_nonstandard_transfer_logs_are_reported_not_guessed(
    patch: dict[str, object], reason: str
) -> None:
    node = FakeEthNode()
    lg = eth.parse_log({**node.logs[0], **patch}, SRC, block_time=None, finality=Finality.OBSERVED)
    t, why = eth.decode_transfer_log(lg)
    assert t is None
    assert why is not None
    assert reason in why


def test_other_events_are_ignored() -> None:
    node = FakeEthNode()
    lg = eth.parse_log(
        {**node.logs[0], "topics": [h32(1)]}, SRC, block_time=None, finality=Finality.OBSERVED
    )
    assert eth.decode_transfer_log(lg) == (None, None)


# ---------- Solana ----------

SSRC = SourceInfo("solana-public", "getTransaction", datetime(2026, 11, 3, tzinfo=UTC), "b" * 64)


def test_solana_legacy_transfer_tx() -> None:
    node = FakeSolNode()
    p = sol.parse_transaction(node._tx_a(), wallet=node.wallet, src=SSRC, commitment="finalized")  # pyright: ignore[reportPrivateUsage]
    assert p.parse_status == "complete"
    assert p.tx.fee_lamports == 5000
    assert p.tx.provenance.finality is Finality.FINALIZED
    kinds = sorted(k for _, k in p.transfers)
    assert kinds == ["solana_system_transfer_ix", "spl_transfer_checked_ix"]
    system = next(t for t, k in p.transfers if k == "solana_system_transfer_ix")
    assert system.amount_raw == 10**9
    assert system.from_address is not None
    assert system.from_address.address == node.wallet
    spl = next(t for t, k in p.transfers if k == "spl_transfer_checked_ix")
    assert spl.amount_raw == 2_500_000
    assert spl.decimals == 6
    assert spl.token is not None
    assert spl.token.address == node.mint
    native = [b for b in p.balance_changes if b.kind == "native_balance_change"]
    assert len(native) == 1
    assert native[0].delta_raw == -(10**9) - 5000
    token = [b for b in p.balance_changes if b.kind == "token_balance_change"]
    assert [(b.owner, b.delta_raw) for b in token] == [
        (node.wallet, -2_500_000)
    ]  # recipient's not kept
    assert p.unknown_instruction_count == 0


def test_solana_v0_lookup_and_unknown_instruction() -> None:
    node = FakeSolNode()
    p = sol.parse_transaction(node._tx_b(), wallet=node.wallet, src=SSRC, commitment="confirmed")  # pyright: ignore[reportPrivateUsage]
    assert node.lut_key in p.account_keys
    assert p.tx.version == 0
    assert p.tx.block_time is None  # null blockTime is not fabricated
    assert p.tx.provenance.finality is Finality.CONFIRMED
    assert p.unknown_instruction_count == 1
    assert p.transfers == []


def test_solana_missing_meta_is_partial() -> None:
    node = FakeSolNode()
    raw = {**node._tx_a(), "meta": None}  # pyright: ignore[reportPrivateUsage]
    p = sol.parse_transaction(raw, wallet=node.wallet, src=SSRC, commitment="finalized")
    assert p.parse_status == "partial"
    assert p.tx.status is None
    assert p.tx.fee_lamports is None
    assert p.balance_changes == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.update(version=1),
        lambda t: t["transaction"].update(signatures=["nope"]),
        lambda t: t["transaction"]["message"].update(accountKeys=["0xabc"]),
        lambda t: t.update(slot=-1),
    ],
)
def test_solana_invalid_transactions_raise(mutate: Callable[[dict[str, Any]], None]) -> None:
    node = FakeSolNode()
    raw = node._tx_a()  # pyright: ignore[reportPrivateUsage]
    mutate(raw)
    with pytest.raises(ParseError):
        sol.parse_transaction(raw, wallet=node.wallet, src=SSRC, commitment="finalized")


def test_solana_signatures_and_null_tx() -> None:
    node = FakeSolNode()
    sigs = sol.parse_signatures(node.sigs)
    assert [s.failed for s in sigs] == [False, False, True]
    assert sigs[1].block_time is None
    with pytest.raises(ParseError, match="null"):
        sol.parse_transaction(None, wallet=node.wallet, src=SSRC, commitment="finalized")
