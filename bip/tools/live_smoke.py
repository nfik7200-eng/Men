"""Opt-in live smoke test against real public/configured RPC providers.

Usage::

    BIP_LIVE_RPC=1 uv run python -m bip.tools.live_smoke

Optional:
    BIP_LIVE_WALLET_ETHEREUM=0x...   also run a bounded ingestion (limit 3) for this wallet
    BIP_LIVE_WALLET_SOLANA=<base58>  same for Solana
    BIP_LIVE_DATA_ROOT=/path         keep results (default: a temporary directory, deleted)
    BIP_RPC__USE_ENV_PROXY=true      route through HTTPS_PROXY (e.g. restricted sandboxes)

It never writes to your normal data root unless BIP_LIVE_DATA_ROOT points there, performs only
read-only requests, and exits non-zero if any usable provider fails. Results are real-network
evidence only for the providers and time shown.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from bip.chains.common import integer, obj
from bip.chains.ethereum.parse import quantity
from bip.chains.identity import EXPECTED_IDENTITY, identity_matches
from bip.config.settings import load_settings
from bip.core.exceptions import BIPError
from bip.db.engine import create_db_engine
from bip.db.observations import ObservationStore
from bip.db.repository import Repository
from bip.ingest.wallet import WalletIngestor
from bip.providers.registry import default_registry
from bip.providers.state import ProviderStateStore
from bip.providers.status import compute_view
from bip.router.router import ProviderRouter
from bip.rpc.transport import HttpsTransport
from bip.security.secrets import redact_text

# Bounded read-only probes: balances of well-known, non-personal system addresses.
PROBE_ADDRESS = {
    "ethereum": "0x0000000000000000000000000000000000000000",
    "solana": "11111111111111111111111111111111",
}


def main() -> int:
    if os.environ.get("BIP_LIVE_RPC") != "1":
        print("Refusing to run: set BIP_LIVE_RPC=1 to make real network requests.")  # noqa: T201
        return 2
    keep = os.environ.get("BIP_LIVE_DATA_ROOT")
    root = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="bip-live-"))
    settings = load_settings(data_root=root, env="test", min_free_bytes=0, min_free_ratio=0)
    engine = create_db_engine(settings)
    repo = Repository(engine)
    state = ProviderStateStore(repo)
    registry = default_registry()
    router = ProviderRouter(
        registry, settings, HttpsTransport(use_env_proxy=settings.rpc.use_env_proxy), state
    )
    report: list[dict[str, object]] = []
    failures = 0
    try:
        for spec in registry.all():
            if not spec.has_adapter:
                continue
            view = compute_view(spec, None)
            chain = spec.chains[0]
            row: dict[str, object] = {
                "provider": spec.id,
                "chain": chain,
                "lifecycle_before": view.lifecycle,
            }
            if not view.usable:
                row["result"] = "skipped"
                row["reason"] = redact_text(view.resolution.reason)
                report.append(row)
                continue
            started = time.monotonic()
            try:
                ident = router.call(chain, EXPECTED_IDENTITY[chain].method, [], provider_id=spec.id)
                row["identity"] = str(ident.result)
                row["identity_ok"] = identity_matches(chain, ident.result)
                if chain == "ethereum":
                    head = router.call(chain, "eth_blockNumber", [], provider_id=spec.id)
                    row["head_block"] = quantity(head.result, "eth_blockNumber")
                    bal = router.call(
                        chain,
                        "eth_getBalance",
                        [PROBE_ADDRESS[chain], "latest"],
                        provider_id=spec.id,
                    )
                    row["probe_balance_wei"] = str(quantity(bal.result, "eth_getBalance"))
                else:
                    health = router.call(chain, "getHealth", [], provider_id=spec.id)
                    row["health"] = health.result
                    head = router.call(
                        chain, "getSlot", [{"commitment": "finalized"}], provider_id=spec.id
                    )
                    row["head_slot"] = integer(head.result, "getSlot")
                    bal = router.call(
                        chain, "getBalance", [PROBE_ADDRESS[chain]], provider_id=spec.id
                    )
                    value = obj(bal.result, "getBalance").get("value")
                    row["probe_balance_lamports"] = integer(value, "getBalance.value")
                row["latency_ms_total"] = int((time.monotonic() - started) * 1000)
                row["result"] = "ok" if row.get("identity_ok") else "network_mismatch"
            except BIPError as exc:
                row["result"] = "failed"
                row["error"] = redact_text(f"{type(exc).__name__}: {exc}")[:400]
            if row["result"] != "ok":
                failures += 1
            report.append(row)

        ingest_rows: list[dict[str, object]] = []
        ingestor = WalletIngestor(router, repo, settings)
        for chain in ("ethereum", "solana"):
            wallet = os.environ.get(f"BIP_LIVE_WALLET_{chain.upper()}")
            if not wallet:
                continue
            run_id = ingestor.run(chain, wallet, 3)
            run = ObservationStore(repo).get_run(run_id) or {}
            ingest_rows.append(
                {
                    k: run.get(k)
                    for k in (
                        "chain",
                        "address",
                        "status",
                        "transactions_seen",
                        "transactions_stored",
                        "transfers_stored",
                        "balance_changes_stored",
                        "error_count",
                        "message",
                    )
                }
            )
            if run.get("status") == "failed":
                failures += 1
    finally:
        engine.dispose()
        if not keep:
            shutil.rmtree(root, ignore_errors=True)

    print(json.dumps({"providers": report, "ingestion": ingest_rows}, indent=2, default=str))  # noqa: T201
    mode = "proxy" if settings.rpc.use_env_proxy else "direct (IP-pinned)"
    print(f"\n{failures} failure(s). Mode: {mode}.")  # noqa: T201
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
