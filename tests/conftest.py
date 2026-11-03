"""Shared fixtures. Unit tests never touch the network, real secrets or a database."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from bip.contracts.blockchain import BlockPosition, SlotPosition
from bip.contracts.identifiers import EvmTxRef, SolanaTxRef
from bip.contracts.provenance import IngestionMethod, Provenance, SourceKind
from bip.security.secrets import clear_registered_secrets

# Synthetic identifiers (not real chain data).
EVM_TX = "0x" + "ab" * 32
EVM_BLOCK_HASH = "0x" + "cd" * 32
EVM_PARENT_HASH = "0x" + "ef" * 32
EVM_ADDR_A = "0x" + "11" * 20
EVM_ADDR_B = "0x" + "22" * 20
EVM_TOKEN = "0x" + "33" * 20
# Base58 strings that decode to 32 and 64 bytes respectively.
SOL_KEY_A = "11111111111111111111111111111111"  # 32 zero bytes (system program id)
SOL_KEY_B = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # 32 bytes

COLLECTED = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _b58encode(data: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = alphabet[r] + out
    pad = len(data) - len(data.lstrip(b"\x00"))
    return "1" * pad + out


SOL_SIGNATURE = _b58encode(bytes(range(1, 65)))
SOL_BLOCKHASH = _b58encode(bytes(range(100, 132)))
SOL_PREV_BLOCKHASH = _b58encode(bytes(range(150, 182)))
SOL_KEY_C = _b58encode(bytes(range(200, 232)))


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove developer BIP_* variables and registered secrets so tests are hermetic."""
    for key in list(os.environ):
        if key.startswith("BIP_"):
            monkeypatch.delenv(key, raising=False)
    clear_registered_secrets()
    yield
    clear_registered_secrets()


def evm_provenance(**overrides: object) -> Provenance:
    data: dict[str, object] = {
        "chain": "ethereum",
        "source_kind": SourceKind.RPC,
        "source_provider": "synthetic",
        "collected_at": COLLECTED,
        "ingestion_method": IngestionMethod.HISTORICAL,
        "schema_version": 1,
        "position": BlockPosition(block_number=100, block_hash=EVM_BLOCK_HASH),
        "tx": EvmTxRef(tx_hash=EVM_TX),
    }
    data.update(overrides)
    return Provenance.model_validate(data)


def sol_provenance(**overrides: object) -> Provenance:
    data: dict[str, object] = {
        "chain": "solana",
        "source_kind": SourceKind.RPC,
        "source_provider": "synthetic",
        "collected_at": COLLECTED,
        "ingestion_method": IngestionMethod.LIVE,
        "schema_version": 1,
        "position": SlotPosition(slot=250_000_000),
        "tx": SolanaTxRef(signature=SOL_SIGNATURE),
    }
    data.update(overrides)
    return Provenance.model_validate(data)
