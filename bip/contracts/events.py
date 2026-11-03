"""Event-level records: EVM logs and Solana instructions."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import Field, model_validator

from bip.contracts.base import ContractModel, NonNegInt, PositiveInt
from bip.contracts.blockchain import ChainFamily, ChainId
from bip.contracts.blocks import require_family
from bip.contracts.identifiers import (
    EvmAddress,
    EvmHash32,
    HexData,
    SolanaPubkey,
    SolanaSignature,
)
from bip.contracts.provenance import Provenance, require_matching_chain


class EvmLog(ContractModel):
    chain: ChainId
    block_number: NonNegInt
    block_hash: EvmHash32
    tx_hash: EvmHash32
    tx_index: NonNegInt
    log_index: NonNegInt
    address: EvmAddress
    topics: Annotated[tuple[EvmHash32, ...], Field(max_length=4)]
    data: HexData
    removed: bool = False
    """True when a provider reports the log as removed by a reorg."""
    provenance: Provenance

    @property
    def natural_key(self) -> tuple[str, str, int]:
        """``(chain, block_hash, log_index)``, unique per canonical log."""
        return (self.chain, self.block_hash, self.log_index)

    @model_validator(mode="after")
    def _consistency(self) -> Self:
        require_family(self.chain, ChainFamily.EVM)
        require_matching_chain(self.chain, self.provenance)
        return self


class SolanaInstruction(ContractModel):
    chain: ChainId
    slot: NonNegInt
    signature: SolanaSignature
    outer_index: NonNegInt
    inner_index: NonNegInt | None = None
    """``None`` for a top-level instruction; set for inner (CPI) instructions."""
    stack_height: PositiveInt | None = None
    program_id: SolanaPubkey
    accounts: tuple[SolanaPubkey, ...] = ()
    data_base58: Annotated[str, Field(max_length=1_000_000)] | None = None
    provenance: Provenance

    @property
    def natural_key(self) -> tuple[str, str, int, int | None]:
        """``(chain, signature, outer_index, inner_index)``."""
        return (self.chain, self.signature, self.outer_index, self.inner_index)

    @model_validator(mode="after")
    def _consistency(self) -> Self:
        require_family(self.chain, ChainFamily.SOLANA)
        require_matching_chain(self.chain, self.provenance)
        return self
