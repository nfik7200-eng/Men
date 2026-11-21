"""Block (EVM) and slot (Solana) metadata.

EVM and Solana blocks are separate models because their semantics differ: Solana slots can be
skipped, ``blockTime`` can be null, and ``parent_slot`` need not be ``slot - 1``.
"""

from __future__ import annotations

from typing import Self

from pydantic import AwareDatetime, model_validator

from bip.contracts.base import ContractModel, NonNegInt
from bip.contracts.blockchain import ChainFamily, ChainId, family_of
from bip.contracts.identifiers import EvmHash32, SolanaBlockhash
from bip.contracts.provenance import Provenance, require_matching_chain


def require_family(chain: str, family: ChainFamily) -> None:
    if family_of(chain) is not family:
        raise ValueError(f"chain {chain!r} is not a {family.value} chain")


class EvmBlock(ContractModel):
    chain: ChainId
    number: NonNegInt
    hash: EvmHash32
    parent_hash: EvmHash32
    timestamp: AwareDatetime
    tx_count: NonNegInt | None = None
    """``None`` when the source did not report it. Never assumed to be zero."""
    provenance: Provenance

    @model_validator(mode="after")
    def _consistency(self) -> Self:
        require_family(self.chain, ChainFamily.EVM)
        require_matching_chain(self.chain, self.provenance)
        if self.number > 0 and self.parent_hash == self.hash:
            raise ValueError("parent_hash equals hash")
        pos = self.provenance.position
        if pos is not None and pos.kind == "block" and pos.block_number != self.number:
            raise ValueError("provenance position does not match block number")
        return self


class SolanaBlock(ContractModel):
    chain: ChainId
    slot: NonNegInt
    blockhash: SolanaBlockhash
    previous_blockhash: SolanaBlockhash
    parent_slot: NonNegInt
    block_time: AwareDatetime | None = None
    """Solana may return a null block time; it stays ``None``."""
    block_height: NonNegInt | None = None
    tx_count: NonNegInt | None = None
    provenance: Provenance

    @model_validator(mode="after")
    def _consistency(self) -> Self:
        require_family(self.chain, ChainFamily.SOLANA)
        require_matching_chain(self.chain, self.provenance)
        if self.slot > 0 and self.parent_slot >= self.slot:
            raise ValueError("parent_slot must be lower than slot")
        pos = self.provenance.position
        if pos is not None and pos.kind == "slot" and pos.slot != self.slot:
            raise ValueError("provenance position does not match slot")
        return self
