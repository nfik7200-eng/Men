"""Token and native-asset transfer records.

A transfer records value moving between addresses. It is **not** a purchase or sale:
``classification`` stays ``unclassified`` until an enrichment step with evidence decides
otherwise (swap, internal move, CEX deposit…). Amounts are raw base units; ``decimals`` is
``None`` when unknown and is never assumed.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from bip.contracts.base import ContractModel, NonNegInt, UInt256
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
from bip.contracts.provenance import Provenance, require_matching_chain


class TokenStandard(StrEnum):
    NATIVE = "native"
    ERC20 = "erc20"
    ERC721 = "erc721"
    ERC1155 = "erc1155"
    SPL_TOKEN = "spl_token"  # noqa: S105 - enum label, not a credential
    SPL_TOKEN_2022 = "spl_token_2022"  # noqa: S105 - enum label, not a credential


_STANDARD_FAMILY: dict[TokenStandard, ChainFamily | None] = {
    TokenStandard.NATIVE: None,
    TokenStandard.ERC20: ChainFamily.EVM,
    TokenStandard.ERC721: ChainFamily.EVM,
    TokenStandard.ERC1155: ChainFamily.EVM,
    TokenStandard.SPL_TOKEN: ChainFamily.SOLANA,
    TokenStandard.SPL_TOKEN_2022: ChainFamily.SOLANA,
}
_NFT_STANDARDS = frozenset({TokenStandard.ERC721, TokenStandard.ERC1155})


class TransferClassification(StrEnum):
    UNCLASSIFIED = "unclassified"
    TRANSFER = "transfer"
    SWAP_BUY = "swap_buy"
    SWAP_SELL = "swap_sell"
    INTERNAL_MOVE = "internal_move"
    BRIDGE = "bridge"
    CEX_DEPOSIT = "cex_deposit"
    CEX_WITHDRAWAL = "cex_withdrawal"
    MINT = "mint"
    BURN = "burn"


class TokenTransfer(ContractModel):
    chain: ChainId
    tx: TxRef
    position: ChainPosition
    log_index: NonNegInt | None = None
    """EVM: index of the emitting log. ``None`` for native value transfers."""
    instruction_path: Annotated[tuple[NonNegInt, ...], Field(min_length=1, max_length=2)] | None = (
        None
    )
    """Solana: ``(outer_index,)`` or ``(outer_index, inner_index)``."""
    standard: TokenStandard
    token: AddressRef | None
    """Token contract (EVM) or mint (Solana). ``None`` only for native transfers."""
    from_address: AddressRef | None
    """``None`` for a mint, where the source reports no sender."""
    to_address: AddressRef | None
    """``None`` for a burn, where the source reports no recipient."""
    amount_raw: UInt256
    decimals: Annotated[int, Field(ge=0, le=255, strict=True)] | None = None
    token_id: UInt256 | None = None
    classification: TransferClassification = TransferClassification.UNCLASSIFIED
    provenance: Provenance

    def amount_decimal(self) -> Decimal | None:
        """Amount scaled by ``decimals``, or ``None`` when decimals are unknown."""
        if self.decimals is None:
            return None
        return Decimal(self.amount_raw).scaleb(-self.decimals)

    @model_validator(mode="after")
    def _consistency(self) -> Self:
        family = family_of(self.chain)
        require_matching_chain(self.chain, self.provenance)
        require_tx_for_chain(self.chain, self.tx)
        require_position_for_chain(self.chain, self.position)

        expected_family = _STANDARD_FAMILY[self.standard]
        if expected_family is not None and expected_family is not family:
            raise ValueError(f"{self.standard.value} transfers are not valid on {self.chain!r}")
        if self.standard is TokenStandard.NATIVE:
            if self.token is not None:
                raise ValueError("native transfers must not reference a token contract")
        elif self.token is None:
            raise ValueError("token transfers must reference the token contract or mint")

        for ref in (self.token, self.from_address, self.to_address):
            if ref is not None and ref.chain != self.chain:
                raise ValueError("all address references must be on the transfer's chain")
        if self.from_address is None and self.to_address is None:
            raise ValueError("a transfer needs a sender, a recipient, or both")

        if self.standard in _NFT_STANDARDS:
            if self.token_id is None:
                raise ValueError(f"{self.standard.value} transfers require token_id")
            if self.standard is TokenStandard.ERC721 and self.amount_raw != 1:
                raise ValueError("erc721 transfers move exactly one token")
        elif self.token_id is not None:
            raise ValueError("token_id is only valid for NFT standards")

        if family is ChainFamily.EVM:
            if self.instruction_path is not None:
                raise ValueError("instruction_path is Solana-only")
            if self.standard is not TokenStandard.NATIVE and self.log_index is None:
                raise ValueError("EVM token transfers require log_index")
        else:
            if self.log_index is not None:
                raise ValueError("log_index is EVM-only")
            if self.instruction_path is None:
                raise ValueError("Solana transfers require instruction_path")
        return self
