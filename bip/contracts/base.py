"""Shared base model and constrained scalar types."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, Strict

UINT256_MAX = 2**256 - 1

NonNegInt = Annotated[int, Strict(), Field(ge=0)]
"""Non-negative integer. Strict: rejects ``bool`` and numeric strings, so nothing is coerced."""

PositiveInt = Annotated[int, Strict(), Field(ge=1)]

UInt256 = Annotated[int, Strict(), Field(ge=0, le=UINT256_MAX)]
"""Raw on-chain integer amount (wei, base units). Never a float."""

Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.\-]{0,63}$")]
"""Lower-case identifier for providers, endpoints, finding types and similar."""

Probability = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]


class ContractModel(BaseModel):
    """Base for all contracts: immutable, unknown fields rejected, defaults validated."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)
