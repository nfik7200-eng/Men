"""Chain-specific identifiers.

EVM hashes and addresses are ``0x``-prefixed hex and are normalized to lower case. EIP-55
checksums are **not** verified (stdlib has no Keccak-256); mixed-case input is accepted and
lower-cased. Solana public keys, blockhashes and signatures are base58 and are validated by
decoding to the exact byte length (32 for keys and blockhashes, 64 for signatures).
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from bip.contracts.base import ContractModel

_HEX32 = re.compile(r"^0x[0-9a-fA-F]{64}$")
_HEX20 = re.compile(r"^0x[0-9a-fA-F]{40}$")
_HEXDATA = re.compile(r"^0x(?:[0-9a-fA-F]{2})*$")

BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {ch: i for i, ch in enumerate(BASE58_ALPHABET)}


def b58decode(value: str) -> bytes:
    """Decode a Bitcoin/Solana-alphabet base58 string. Raises ``ValueError`` if invalid."""
    if not value:
        raise ValueError("empty base58 string")
    number = 0
    for ch in value:
        idx = _B58_INDEX.get(ch)
        if idx is None:
            raise ValueError(f"invalid base58 character {ch!r}")
        number = number * 58 + idx
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_zeros = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading_zeros + body


def _evm_hash(value: str) -> str:
    if not _HEX32.fullmatch(value):
        raise ValueError("expected 0x-prefixed 32-byte hex (66 characters)")
    return value.lower()


def evm_address(value: str) -> str:
    if not _HEX20.fullmatch(value):
        raise ValueError("expected 0x-prefixed 20-byte hex address (42 characters)")
    return value.lower()


def _hex_data(value: str) -> str:
    if not _HEXDATA.fullmatch(value):
        raise ValueError("expected 0x-prefixed hex with an even number of digits")
    return value.lower()


def _b58_of_length(expected: int, what: str):
    def check(value: str) -> str:
        try:
            decoded = b58decode(value)
        except ValueError as exc:
            raise ValueError(f"invalid {what}: {exc}") from None
        if len(decoded) != expected:
            raise ValueError(
                f"invalid {what}: decodes to {len(decoded)} bytes, expected {expected}"
            )
        return value

    return check


EvmHash32 = Annotated[str, AfterValidator(_evm_hash)]
"""Transaction hash, block hash or log topic."""
EvmAddress = Annotated[str, AfterValidator(evm_address)]
HexData = Annotated[str, Field(max_length=2 + 2 * 1024 * 1024), AfterValidator(_hex_data)]
SolanaPubkey = Annotated[
    str,
    Field(min_length=32, max_length=44),
    AfterValidator(_b58_of_length(32, "Solana public key")),
]
SolanaBlockhash = Annotated[
    str, Field(min_length=32, max_length=44), AfterValidator(_b58_of_length(32, "Solana blockhash"))
]
SolanaSignature = Annotated[
    str, Field(min_length=64, max_length=88), AfterValidator(_b58_of_length(64, "Solana signature"))
]


class EvmTxRef(ContractModel):
    """Reference to an EVM transaction by hash."""

    kind: Literal["evm_tx_hash"] = "evm_tx_hash"
    tx_hash: EvmHash32


class SolanaTxRef(ContractModel):
    """Reference to a Solana transaction by its first signature."""

    kind: Literal["solana_signature"] = "solana_signature"
    signature: SolanaSignature


TxRef = Annotated[EvmTxRef | SolanaTxRef, Field(discriminator="kind")]
"""A transaction identifier that keeps EVM hashes and Solana signatures distinct."""
