"""Serve the real BIP app wired to the deterministic fake RPC nodes (offline e2e only).

    BIP_DATA_ROOT=/tmp/x uv run python -m tests.stage2.serve_offline 8766

Every response comes from tests/stage2/fakes.py. This is NOT a real provider and must never be
presented as real blockchain data; the e2e script labels these checks "[offline fixture]".
"""

from __future__ import annotations

import os
import sys

import uvicorn

from bip.api.app import create_app
from bip.config import load_settings
from tests.stage2.fakes import ETH_HOST, SOL_HOST, FakeEthNode, FakeSolNode, FakeTransport


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    eth, sol = FakeEthNode(), FakeSolNode()
    settings = load_settings(
        data_root=os.environ["BIP_DATA_ROOT"],
        env="test",
        min_free_bytes=0,
        min_free_ratio=0,
        api={"port": port, "cors_origins": ("http://127.0.0.1:4173",)},
    )
    app = create_app(
        settings,
        transport=FakeTransport({ETH_HOST: eth.handle, SOL_HOST: sol.handle}),
        environ={},
        rpc_sleep=lambda _s: None,
        # Marks the database so every page labels these records as offline fixtures, never as
        # real blockchain data (the marker stays with the database).
        data_origin="offline_fixture",
    )
    print(f"OFFLINE FIXTURE BACKEND eth_wallet={eth.wallet} sol_wallet={sol.wallet}", flush=True)  # noqa: T201
    uvicorn.run(app, host="127.0.0.1", port=port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
