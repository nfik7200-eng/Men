"""Opt-in, budgeted live capability probe (the same engine as POST /providers/probe-runs).

Usage::

    BIP_LIVE_RPC=1 uv run python -m bip.tools.probe [provider-id ...]

Optional:
    BIP_LIVE_DATA_ROOT=/path   keep the results database (default: temporary, deleted)
    BIP_RPC__USE_ENV_PROXY=true   route through HTTPS_PROXY
    BIP_PROBE__*               lower the budgets (ceilings: 8 calls/provider, 48/run, 90 s)

Only keyless providers and providers whose BIP_PROVIDER_*_URL is already set are probed. The
output separates environment network restrictions from provider behaviour; it never prints
endpoint URLs or keys.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from bip.config.settings import load_settings
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.providers.intelligence import ProbeStore
from bip.providers.prober import Prober
from bip.providers.registry import default_registry
from bip.providers.state import ProviderStateStore
from bip.router.router import ProviderRouter
from bip.rpc.transport import HttpsTransport


def main(argv: list[str]) -> int:
    if os.environ.get("BIP_LIVE_RPC") != "1":
        print("Refusing to run: set BIP_LIVE_RPC=1 to make real network requests.")  # noqa: T201
        return 2
    keep = os.environ.get("BIP_LIVE_DATA_ROOT")
    root = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="bip-probe-"))
    settings = load_settings(data_root=root, env="test", min_free_bytes=0, min_free_ratio=0)
    engine = create_db_engine(settings)
    try:
        repo = Repository(engine)
        state = ProviderStateStore(repo)
        registry = default_registry()
        router = ProviderRouter(
            registry, settings, HttpsTransport(use_env_proxy=settings.rpc.use_env_proxy), state
        )
        prober = Prober(registry, router, settings.probe, states=state.states)
        store = ProbeStore(repo, registry)
        plan = prober.plan(argv or None)
        print(  # noqa: T201
            json.dumps(
                {
                    "plan": {p.spec.id: [c.id for c in p.requests] for p in plan.providers},
                    "ineligible": plan.ineligible,
                    "budget": plan.budget,
                },
                indent=2,
            )
        )
        if not plan.providers:
            return 1
        started = utcnow()
        run_id, key = store.start_run(plan, None)
        results = prober.run(plan)
        store.finish_run(run_id, results, started=started)
        run = store.run(key)
        report = {
            "run": {
                k: run[k]
                for k in (
                    "run_id",
                    "status",
                    "requests_used",
                    "requests_skipped",
                    "duration_ms",
                    "failure_summary",
                )
            },
            "providers": run["summary"]["providers"] if run["summary"] else {},
            "observations": [
                {
                    k: o[k]
                    for k in (
                        "provider_id",
                        "capability",
                        "status",
                        "connectivity",
                        "environment_restricted",
                        "explanation",
                    )
                }
                for o in run["observations"]
            ],
            "consistency": run["consistency_checks"],
        }
        print(json.dumps(report, indent=2, default=str))  # noqa: T201
        providers: dict[str, dict[str, object]] = (
            run["summary"]["providers"] if run["summary"] else {}
        )
        reached = any(p["connectivity"] == "reachable" for p in providers.values())
        return 0 if reached else 1
    finally:
        engine.dispose()
        if not keep:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
