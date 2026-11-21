"""Smart-contract / program deployment metadata.

Records what is observable about a deployment. ``deployer`` is the address that created it, not
a person or organization; ``deployer`` and ``is_proxy`` are ``None`` when unknown.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import model_validator

from bip.contracts.base import ContractModel
from bip.contracts.blockchain import (
    AddressRef,
    ChainFamily,
    ChainId,
    ChainPosition,
    TxRef,
    family_of,
    require_position_for_chain,
    require_tx_for_chain,
)
from bip.contracts.identifiers import EvmHash32
from bip.contracts.provenance import Provenance, require_matching_chain


class DeploymentMethod(StrEnum):
    EVM_TOP_LEVEL_CREATE = "evm_top_level_create"
    """Transaction with no ``to`` address; deployer is the sender."""
    EVM_INTERNAL_CREATE = "evm_internal_create"
    """CREATE/CREATE2 from another contract (factory); requires trace data."""
    SOLANA_PROGRAM_DEPLOY = "solana_program_deploy"
    UNKNOWN = "unknown"


_METHOD_FAMILY = {
    DeploymentMethod.EVM_TOP_LEVEL_CREATE: ChainFamily.EVM,
    DeploymentMethod.EVM_INTERNAL_CREATE: ChainFamily.EVM,
    DeploymentMethod.SOLANA_PROGRAM_DEPLOY: ChainFamily.SOLANA,
}


class ContractDeployment(ContractModel):
    chain: ChainId
    contract: AddressRef
    deployer: AddressRef | None
    creation_tx: TxRef
    position: ChainPosition
    method: DeploymentMethod
    factory: AddressRef | None = None
    code_hash: EvmHash32 | None = None
    is_proxy: bool | None = None
    """``None`` until proxy detection has run. ``False`` means checked and not a proxy."""
    upgrade_authority: AddressRef | None = None
    """Solana upgradeable programs only."""
    provenance: Provenance

    @model_validator(mode="after")
    def _consistency(self) -> Self:
        family = family_of(self.chain)
        require_matching_chain(self.chain, self.provenance)
        require_tx_for_chain(self.chain, self.creation_tx)
        require_position_for_chain(self.chain, self.position)
        for ref in (self.contract, self.deployer, self.factory, self.upgrade_authority):
            if ref is not None and ref.chain != self.chain:
                raise ValueError("all address references must be on the deployment's chain")
        expected = _METHOD_FAMILY.get(self.method)
        if expected is not None and expected is not family:
            raise ValueError(
                f"deployment method {self.method.value} is not valid on {self.chain!r}"
            )
        if self.factory is not None and self.method is not DeploymentMethod.EVM_INTERNAL_CREATE:
            raise ValueError("factory is only valid for evm_internal_create")
        if family is ChainFamily.EVM and self.upgrade_authority is not None:
            raise ValueError("upgrade_authority is Solana-only")
        if family is ChainFamily.SOLANA and self.code_hash is not None:
            raise ValueError("code_hash is EVM-only")
        return self
