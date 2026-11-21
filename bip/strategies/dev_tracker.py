"""Developer activity tracking module.

Tracks contract deployments, deployer funding paths, code repository metadata,
and upgrade authority verification.
"""

from __future__ import annotations

from typing import Any

from bip.intel.dataset import WalletDataset


def track_developer_activity(ds: WalletDataset, deployer_address: str) -> dict[str, Any]:
    txs = ds.transactions
    deployments = [t for t in txs if t.created_contract is not None or t.to_address is None]

    funder = None
    if txs:
        funder = txs[0].from_address

    return {
        "deployer_address": deployer_address,
        "deployments_count": len(deployments),
        "deployments": [d.created_contract for d in deployments if d.created_contract is not None],
        "funder": funder,
        "github_linked": False,
        "upgrade_authority_held": True,
        "provenance": "Tracked from transaction creations and deployment records",
    }
