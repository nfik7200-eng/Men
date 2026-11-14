"""Expected network identity per chain, checked before a provider's data is trusted.

Ethereum mainnet: ``eth_chainId`` must return ``0x1`` (EIP-155 chain id 1).
Solana mainnet-beta: ``getGenesisHash`` must return the mainnet-beta genesis hash.
A provider whose answer differs is never used for that chain.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IdentityCheck:
    method: str
    expected: str
    label: str


EXPECTED_IDENTITY: dict[str, IdentityCheck] = {
    "ethereum": IdentityCheck("eth_chainId", "0x1", "chain id 1 (Ethereum mainnet)"),
    "solana": IdentityCheck(
        "getGenesisHash",
        "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d",
        "mainnet-beta genesis hash",
    ),
}


def identity_matches(chain: str, result: object) -> bool:
    check = EXPECTED_IDENTITY[chain]
    if not isinstance(result, str):
        return False
    if chain == "ethereum":
        try:
            return int(result, 16) == int(check.expected, 16)
        except ValueError:
            return False
    return result == check.expected
