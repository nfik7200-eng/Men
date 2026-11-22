"""Shared evidence helpers for the strategy heuristics.

Strategy outputs are heuristics over the *retrieved* wallet dataset, so every output is a
``hypothesis`` (CLAUDE.md §2.12) and states its limitations.
"""

from __future__ import annotations

from dataclasses import dataclass

from bip.intel.dataset import WalletDataset

HYPOTHESIS = "hypothesis"
DATASET_LIMITATION = (
    "Computed from the transactions and transfers BIP has stored for this wallet, not its full "
    "on-chain history."
)


@dataclass(frozen=True, slots=True)
class Deployments:
    contracts: tuple[str, ...]
    """Contracts recorded as created by a transaction this wallet sent."""
    unconfirmed: int
    """Ethereum transactions sent by the wallet with no recipient but no recorded contract."""


def deployments(ds: WalletDataset) -> Deployments:
    """Contract creations by the wallet. Solana transactions have no recipient field, so a
    missing ``to_address`` is only read as a possible creation on EVM chains."""
    mine = ds.is_mine
    contracts: list[str] = []
    unconfirmed = 0
    for t in ds.transactions:
        if t.from_address not in mine:
            continue
        if t.created_contract is not None:
            contracts.append(t.created_contract)
        elif ds.chain != "solana" and t.to_address is None:
            unconfirmed += 1
    return Deployments(tuple(contracts), unconfirmed)
