"""Developer activity tracking (heuristic).

Lists contracts recorded as created by the wallet and the earliest observed native funding.
GitHub links and upgrade authority are not checked by BIP, so they are reported as unknown
(``None``) and named in ``not_checked``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bip.intel.dataset import TransferRow, WalletDataset
from bip.strategies.evidence import DATASET_LIMITATION, HYPOTHESIS, deployments

FUNDER_BASIS = (
    "hypothesis: the sender of the earliest inbound native transfer in the retrieved dataset; "
    "earlier funding may not be stored"
)
LIMITATIONS = [
    DATASET_LIMITATION,
    "Solana program deployments are not detected from wallet data (the slot collector "
    "records them).",
    "GitHub links and upgrade authority are not checked.",
]


def _when(t: TransferRow) -> datetime:
    # Rows without a time are filtered out before this is used.
    return t.block_time or datetime.max.replace(tzinfo=UTC)


def track_developer_activity(ds: WalletDataset, deployer_address: str) -> dict[str, Any]:
    found = deployments(ds)
    inbound = [
        t
        for t in ds.transfers
        if t.direction == "in"
        and t.asset == "native"
        and t.counterparty is not None
        and t.counterparty not in ds.is_mine
        and t.block_time is not None
    ]
    first = min(inbound, key=_when) if inbound else None
    return {
        "deployer_address": deployer_address,
        "deployments_count": len(found.contracts),
        "deployments": list(found.contracts),
        "unconfirmed_creations": found.unconfirmed,
        "funder": first.counterparty if first else None,
        "funder_basis": FUNDER_BASIS if first else None,
        "github_linked": None,
        "upgrade_authority_held": None,
        "not_checked": ["github_linked", "upgrade_authority_held"],
        "provenance": "Tracked from contract creations and transfers in the retrieved dataset",
        "evidence_class": HYPOTHESIS,
        "limitations": LIMITATIONS,
    }
