"""A populated development preview. EVERYTHING IT SHOWS IS SYNTHETIC.

    uv run python -m tests.preview.serve_preview [--port 8000] [--auth] [--data-root DIR]

It builds a throwaway database, inserts invented wallets, tokens, pools and Solana discoveries with
timestamps near "now", runs the real detectors over them, and serves the real app wired to the
offline fake nodes. The database is marked ``offline_fixture`` so every page labels it "Demo data".
Nothing here is blockchain data and no provider is contacted.

With ``--auth`` the app runs with sign-in required and three SYNTHETIC users (viewer, analyst,
admin); their shared password is printed once at start-up and exists only for this preview.
Pair it with the Vite dev server (``cd frontend && npm run dev``), which proxies /api to port 8000.
"""

from __future__ import annotations

import argparse
import os
import secrets
import tempfile
from datetime import timedelta
from pathlib import Path

import uvicorn
from sqlalchemy import Engine, insert

from bip.api.app import create_app
from bip.config import Settings, load_settings
from bip.db import schema
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.discovery.service import DiscoveryService
from bip.entities.labels import EntityLabelService
from bip.security.auth import AuthService
from tests.discovery.seed import E18, ETH, SOL, Seed, eth_addr, eth_tx
from tests.stage2.fakes import ETH_HOST, SOL_HOST, FakeEthNode, FakeSolNode, FakeTransport, key, sig

TOKEN = eth_addr(0xDEC0DE)
POOL, FACTORY = eth_addr(0xB00B), eth_addr(0xFAC0)
DEMO_ADDRESSES = {"deployer": eth_addr(0xD3), "contract": eth_addr(0xC0)}


def seed_synthetic(engine: Engine, settings: Settings) -> dict[str, int]:
    """Insert invented rows near now and run the real detectors over them. Returns counts."""
    now = utcnow()
    seed = Seed(engine)
    n = 0

    def tx(label: int) -> str:
        nonlocal n
        n += 1
        return eth_tx(0x700000 + label * 1000 + n)

    # a token whose transfers are ordinary for ten days, then one enormous transfer
    for i in range(18):
        when = now - timedelta(days=9, hours=-i * 11)
        t = tx(1)
        seed.tx(t, eth_addr(0x100 + i), when)
        seed.transfer(
            t, eth_addr(0x100 + i), eth_addr(0x200 + i), (900 + i * 20) * E18, when, token=TOKEN
        )
    big = tx(1)
    seed.tx(big, eth_addr(0x300), now - timedelta(hours=2))
    seed.transfer(
        big,
        eth_addr(0x300),
        eth_addr(0x400),
        9_000_000 * E18,
        now - timedelta(hours=2),
        token=TOKEN,
    )

    # a deployer with several deployments, one of them just now
    for i in range(3):
        seed.tx(
            tx(2),
            DEMO_ADDRESSES["deployer"],
            now - timedelta(hours=40 - i * 15),
            created=eth_addr(0xC0 + i),
        )

    # a wallet that was quiet for months and then moved
    seed.tx(tx(3), eth_addr(0x500), now - timedelta(days=150))
    seed.tx(tx(3), eth_addr(0x500), now - timedelta(hours=1))

    # one sender funding four wallets with no earlier activity
    for i in range(4):
        t = tx(4)
        when = now - timedelta(hours=3 - i * 0.2)
        seed.tx(t, eth_addr(0x600), when, to=eth_addr(0x610 + i))
        seed.transfer(t, eth_addr(0x600), eth_addr(0x610 + i), 3 * E18, when)

    # a pool, its reserves (one sharp move), a new token and concentrated net inflow
    t = tx(5)
    seed.tx(t, eth_addr(0x700), now - timedelta(hours=6))
    with engine.begin() as conn:
        conn.execute(
            insert(schema.pool_events).values(
                chain=ETH, protocol="v2_like", factory=FACTORY, pool=POOL,
                token0=TOKEN, token1=eth_addr(0xA0), fee=None, tx_id=t, log_index=0,
                block_number=1, block_hash=eth_tx(0x9000), block_time=now - timedelta(hours=6),
                finality="finalized", stored_at=now,
            )
        )  # fmt: skip
        for i, (r0, r1) in enumerate([(1000, 2000), (1010, 1990), (2600, 800)]):
            conn.execute(
                insert(schema.pool_reserves).values(
                    chain=ETH, pool=POOL, tx_id=eth_tx(0x9100 + i), log_index=0,
                    block_number=10 + i, block_hash=eth_tx(0x9200 + i),
                    block_time=now - timedelta(hours=5 - 2 * i), reserve0=str(r0 * 10**18),
                    reserve1=str(r1 * 10**18), finality="finalized", stored_at=now,
                )
            )  # fmt: skip
        conn.execute(
            insert(schema.token_stats).values(
                chain=ETH, token=eth_addr(0xFEED), first_seen_block=5,
                first_seen_at=now - timedelta(hours=4), first_tx_id=tx(6),
                transfers_seen=240, last_seen_block=9,
            )
        )  # fmt: skip
    for i in range(3):
        for k in range(10):
            t = tx(7)
            when = now - timedelta(hours=20 - k)
            seed.tx(t, eth_addr(0x800), when)
            seed.transfer(t, eth_addr(0x800), eth_addr(0x900 + i), 100 * E18, when, token=TOKEN)
    for i in range(12):
        t = tx(8)
        when = now - timedelta(hours=8)
        seed.tx(t, eth_addr(0x800), when)
        seed.transfer(t, eth_addr(0x800), eth_addr(0xA00 + i), 10 * E18, when, token=TOKEN)

    # Solana: a mint, a program and a pool
    with engine.begin() as conn:
        for kind, ref, detail in (
            ("mint", key(0x41), '{"decimals": 6, "instruction": "InitializeMint2"}'),
            ("program", key(0x42), '{"payer": "synthetic"}'),
            (
                "pool",
                key(0x43),
                '{"dex": "Raydium AMM v4", "mint_a": "synthetic", "mint_b": "synthetic"}',
            ),
        ):
            conn.execute(
                insert(schema.solana_discoveries).values(
                    kind=kind, ref=ref, slot=100, blockhash=key(0x44),
                    signature=sig(0x45 + len(kind)), block_time=now - timedelta(hours=1),
                    detail_json=detail, finality="finalized", stored_at=now,
                )
            )  # fmt: skip

    repo = Repository(engine)
    service = DiscoveryService(repo, settings)
    chains = {c: service.run(c) for c in (ETH, SOL)}
    labels = EntityLabelService(repo)
    proposed = labels.propose(
        chain=ETH,
        ref=DEMO_ADDRESSES["deployer"],
        entity_kind="wallet",
        category="protocol",
        name="SYNTHETIC Demo Protocol",
        evidence=[{"kind": "url", "ref": "https://example.org/synthetic-demo-source"}],
        actor="demo-proposer",
        note="SYNTHETIC demo label: this organisation does not exist.",
    )
    labels.review(
        int(proposed["id"]),
        decision="approve",
        reviewer="demo-reviewer",
        note="SYNTHETIC demo approval, not a real review.",
    )
    labels.propose(
        chain=ETH,
        ref=eth_addr(0x300),
        entity_kind="wallet",
        category="exchange",
        name="SYNTHETIC Demo Exchange (awaiting review)",
        evidence=[{"kind": "note", "ref": "a synthetic proposal waiting in the review queue"}],
        actor="demo-proposer",
    )
    return {
        "signals": sum(
            sum(d["created"] for d in run["detectors"].values()) for run in chains.values()
        )
    }


def preview_origins() -> tuple[str, ...]:
    """The local Vite origins, plus the forwarded HTTPS origin of port 5173 when this runs in a
    GitHub Codespace (from the environment variables Codespaces sets), so a phone or browser using
    the private forwarded URL can sign in and make changes through the Vite proxy."""
    origins = ["http://127.0.0.1:5173", "http://localhost:5173"]
    name = os.environ.get("CODESPACE_NAME")
    domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
    if name:
        origins.append(f"https://{name}-5173.{domain}")
    return tuple(origins)


def main() -> None:
    ap = argparse.ArgumentParser(description="Populated, SYNTHETIC development preview.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--auth", action="store_true", help="require sign-in with synthetic users")
    ap.add_argument("--data-root", type=Path, default=None)
    args = ap.parse_args()
    root = args.data_root or Path(tempfile.mkdtemp(prefix="bip-preview-"))
    auth: dict[str, object] = (
        {"mode": "required", "cookie_secure": False, "rate_limit_per_minute": 100_000}
        if args.auth
        else {}
    )
    settings = load_settings(
        data_root=root / "data",
        env="test",
        min_free_bytes=0,
        min_free_ratio=0,
        api={"port": args.port, "cors_origins": preview_origins()},
        discovery={"baseline_days": 200, "lookback_hours": 72},
        auth=auth,
    )
    engine = create_db_engine(settings)
    counts = seed_synthetic(engine, settings)
    password = ""
    if args.auth:
        password = "synthetic-" + secrets.token_urlsafe(9)
        svc = AuthService(Repository(engine), settings.auth)
        for name, role in (("viewer", "viewer"), ("analyst", "analyst"), ("admin", "admin")):
            svc.create_user(name, password, role)
    app = create_app(
        settings,
        engine=engine,
        transport=FakeTransport({ETH_HOST: FakeEthNode().handle, SOL_HOST: FakeSolNode().handle}),
        environ={},
        rpc_sleep=lambda _s: None,
        data_origin="offline_fixture",
    )
    print(  # noqa: T201
        f"SYNTHETIC PREVIEW: nothing here is blockchain data. {counts['signals']} synthetic "
        f"signals, data root {root}",
        flush=True,
    )
    if args.auth:
        print(f"SYNTHETIC users viewer/analyst/admin, password: {password}", flush=True)  # noqa: T201
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
