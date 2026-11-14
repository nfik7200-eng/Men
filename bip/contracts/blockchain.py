"""Chain identity, chain positions and address references.

A chain is identified by a lower-case slug that must exist in :data:`KNOWN_CHAINS`. Adding a
chain means adding one :class:`ChainSpec` entry; no other contract code changes. Every chain
belongs to a :class:`ChainFamily`, which decides identifier formats and position semantics:
EVM chains have **block numbers**, Solana has **slots**. The two are separate types and are
never interchangeable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, ValidationInfo, field_validator

from bip.contracts.base import ContractModel, NonNegInt
from bip.contracts.identifiers import (
    EvmHash32,
    EvmTxRef,
    SolanaBlockhash,
    SolanaTxRef,
    TxRef,
    b58decode,
    evm_address,
)


class ChainFamily(StrEnum):
    EVM = "evm"
    SOLANA = "solana"


@dataclass(frozen=True, slots=True)
class ChainSpec:
    """Static identity of a supported chain network."""

    chain_id: str
    family: ChainFamily
    network: str
    evm_chain_id: int | None = None

    @property
    def position_kind(self) -> Literal["block", "slot"]:
        return "block" if self.family is ChainFamily.EVM else "slot"


KNOWN_CHAINS = MappingProxyType(
    {
        "ethereum": ChainSpec("ethereum", ChainFamily.EVM, "mainnet", 1),
        "solana": ChainSpec("solana", ChainFamily.SOLANA, "mainnet-beta"),
        "base": ChainSpec("base", ChainFamily.EVM, "mainnet", 8453),
        "arbitrum": ChainSpec("arbitrum", ChainFamily.EVM, "one", 42161),
        "bnb": ChainSpec("bnb", ChainFamily.EVM, "mainnet", 56),
    }
)
"""Chains BIP can *describe*. Which chains are ingested is decided by settings, not here."""


def get_chain(chain_id: str) -> ChainSpec:
    """Return the spec for ``chain_id``; raise ``ValueError`` for unknown chains."""
    spec = KNOWN_CHAINS.get(chain_id)
    if spec is None:
        raise ValueError(f"unknown chain {chain_id!r}; known: {sorted(KNOWN_CHAINS)}")
    return spec


def family_of(chain_id: str) -> ChainFamily:
    return get_chain(chain_id).family


def _known_chain(value: str) -> str:
    get_chain(value)
    return value


ChainId = Annotated[str, AfterValidator(_known_chain)]


class BlockPosition(ContractModel):
    """Position on an EVM chain: block number and, when known, block hash."""

    kind: Literal["block"] = "block"
    block_number: NonNegInt
    block_hash: EvmHash32 | None = None


class SlotPosition(ContractModel):
    """Position on Solana: slot and, when known, the slot's blockhash."""

    kind: Literal["slot"] = "slot"
    slot: NonNegInt
    blockhash: SolanaBlockhash | None = None


ChainPosition = Annotated[BlockPosition | SlotPosition, Field(discriminator="kind")]


def require_position_for_chain(chain_id: str, position: BlockPosition | SlotPosition) -> None:
    """Raise ``ValueError`` if a block position is used on Solana or a slot on an EVM chain."""
    expected = get_chain(chain_id).position_kind
    if position.kind != expected:
        raise ValueError(f"chain {chain_id!r} uses {expected} positions, got {position.kind}")


def require_tx_for_chain(chain_id: str, tx: EvmTxRef | SolanaTxRef) -> None:
    """Raise ``ValueError`` if the transaction identifier type does not fit the chain family."""
    family = family_of(chain_id)
    expected = "evm_tx_hash" if family is ChainFamily.EVM else "solana_signature"
    if tx.kind != expected:
        raise ValueError(f"chain {chain_id!r} uses {expected} identifiers, got {tx.kind}")


class AddressKind(StrEnum):
    """What an address is on-chain. Says nothing about who controls it."""

    UNKNOWN = "unknown"
    EXTERNALLY_OWNED = "externally_owned"
    CONTRACT = "contract"
    PROGRAM = "program"
    TOKEN_ACCOUNT = "token_account"  # noqa: S105 - enum label, not a credential


class AddressRef(ContractModel):
    """A chain-qualified address (EVM account or Solana public key).

    Deliberately has no owner or name field: attributing an address to a real-world person or
    organization requires evidence and lives in :class:`bip.contracts.findings.Attribution`.
    """

    chain: ChainId
    address: str
    kind: AddressKind = AddressKind.UNKNOWN

    @field_validator("address")
    @classmethod
    def _validate_format(cls, value: str, info: ValidationInfo) -> str:
        chain = info.data.get("chain")
        if not isinstance(chain, str):
            raise ValueError("address cannot be validated without a valid chain")
        if family_of(chain) is ChainFamily.EVM:
            return evm_address(value)
        try:
            decoded = b58decode(value)
        except ValueError as exc:
            raise ValueError(f"invalid Solana address: {exc}") from None
        if len(decoded) != 32:
            raise ValueError("Solana address must decode to 32 bytes")
        return value


__all__ = [
    "KNOWN_CHAINS",
    "AddressKind",
    "AddressRef",
    "BlockPosition",
    "ChainFamily",
    "ChainId",
    "ChainPosition",
    "ChainSpec",
    "SlotPosition",
    "TxRef",
    "family_of",
    "get_chain",
    "require_position_for_chain",
    "require_tx_for_chain",
]
