"""Transaction metadata, separate per chain family."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from bip.contracts.base import ContractModel, NonNegInt, UInt256
from bip.contracts.blockchain import ChainFamily, ChainId
from bip.contracts.blocks import require_family
from bip.contracts.identifiers import EvmAddress, EvmHash32, SolanaPubkey, SolanaSignature
from bip.contracts.provenance import Provenance, require_matching_chain


class TxStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class EvmTransaction(ContractModel):
    chain: ChainId
    tx_hash: EvmHash32
    block_number: NonNegInt
    block_hash: EvmHash32
    tx_index: NonNegInt
    from_address: EvmAddress
    to_address: EvmAddress | None
    """``None`` for a top-level contract-creation transaction."""
    value_wei: UInt256
    nonce: NonNegInt
    status: TxStatus | None = None
    """``None`` when no receipt status is available (e.g. receipt not fetched)."""
    gas_used: NonNegInt | None = None
    created_contract: EvmAddress | None = None
    provenance: Provenance

    @model_validator(mode="after")
    def _consistency(self) -> Self:
        require_family(self.chain, ChainFamily.EVM)
        require_matching_chain(self.chain, self.provenance)
        if self.created_contract is not None and self.to_address is not None:
            raise ValueError("created_contract is only valid when to_address is None")
        return self


class SolanaTransaction(ContractModel):
    chain: ChainId
    signature: SolanaSignature
    slot: NonNegInt
    block_time: AwareDatetime | None = None
    fee_payer: SolanaPubkey
    fee_lamports: NonNegInt | None = None
    status: TxStatus | None = None
    error: Annotated[str, Field(max_length=4096)] | None = None
    version: Literal["legacy"] | NonNegInt | None = None
    """Transaction message version as reported by RPC: ``"legacy"``, ``0`` (v0) or ``1`` (v1)."""
    provenance: Provenance

    @model_validator(mode="after")
    def _consistency(self) -> Self:
        require_family(self.chain, ChainFamily.SOLANA)
        require_matching_chain(self.chain, self.provenance)
        if self.error is not None and self.status is not TxStatus.FAILED:
            raise ValueError("error is only valid when status is 'failed'")
        return self
