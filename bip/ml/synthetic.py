"""Synthetic wallet datasets for exercising the ML pipeline (``data_kind = "synthetic"``).

These wallets are **generated**, not observed. They exist to test feature extraction,
training, calibration, thresholding, persistence and the ML→AI hand-off end to end. Metrics
computed on them measure how well the pipeline recovers the generator's own rules. They say
nothing about real-world performance, and every model trained on them is registered as
``experimental_synthetic``.

Archetypes (labels are the generator's ground truth):

* ``high_frequency_activity`` and ``automated_bot``: many, regularly spaced transactions;
* ``defi_trader``: many distinct call targets, mostly token transfers;
* ``contract_deployer``: contract-creation transactions;
* ``long_term_holder``: few transactions spread over a long period;
* ``low_activity_or_new``: a handful of recent transactions.

Noise is added so that labels overlap and nothing is perfectly separable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from bip.intel.dataset import TransferRow, TxRow, WalletDataset

SYNTHETIC_ROLES = (
    "high_frequency_activity",
    "automated_bot",
    "defi_trader",
    "contract_deployer",
    "long_term_holder",
    "low_activity_or_new",
)
EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


@dataclass
class SyntheticWallet:
    dataset: WalletDataset
    as_of: datetime
    labels: dict[str, bool]


def _addr(rng: random.Random) -> str:
    return "0x" + "".join(rng.choice("0123456789abcdef") for _ in range(40))


def generate(n: int = 240, *, seed: int = 7, chain: str = "ethereum") -> list[SyntheticWallet]:
    rng = random.Random(seed)  # noqa: S311 - deterministic synthetic data, not security
    out: list[SyntheticWallet] = []
    for i in range(n):
        archetype = SYNTHETIC_ROLES[i % len(SYNTHETIC_ROLES)]
        as_of = EPOCH + timedelta(days=int(300 * i / n) + 30)  # spread → out-of-time splits
        wallet = _addr(rng)
        ds = WalletDataset(chain=chain, address=wallet, owned_accounts=frozenset(), runs=1)
        labels: dict[str, bool] = dict.fromkeys(SYNTHETIC_ROLES, False)
        labels[archetype] = True
        if archetype in {"high_frequency_activity", "automated_bot"}:
            regular = archetype == "automated_bot" or rng.random() < 0.3
            labels["automated_bot"] = regular
            labels["high_frequency_activity"] = (
                archetype == "high_frequency_activity" or rng.random() < 0.6
            )
            count = rng.randint(40, 120)
            start = as_of - timedelta(days=rng.randint(2, 6))
            step = rng.uniform(300, 1200)
            times = [
                start + timedelta(seconds=step * k * (1 if regular else rng.uniform(0.2, 2.5)))
                for k in range(count)
            ]
            targets = [_addr(rng) for _ in range(rng.randint(1, 3))]
        elif archetype == "defi_trader":
            count = rng.randint(15, 60)
            times = sorted(as_of - timedelta(hours=rng.uniform(1, 24 * 45)) for _ in range(count))
            targets = [_addr(rng) for _ in range(rng.randint(6, 15))]
        elif archetype == "contract_deployer":
            count = rng.randint(4, 25)
            times = sorted(as_of - timedelta(hours=rng.uniform(1, 24 * 90)) for _ in range(count))
            targets = [_addr(rng) for _ in range(rng.randint(1, 4))]
        elif archetype == "long_term_holder":
            count = rng.randint(4, 12)
            times = sorted(as_of - timedelta(days=rng.uniform(1, 330)) for _ in range(count))
            targets = [_addr(rng)]
        else:
            count = rng.randint(1, 4)
            times = sorted(as_of - timedelta(hours=rng.uniform(1, 24 * 5)) for _ in range(count))
            targets = [_addr(rng)]
        tokens = [_addr(rng) for _ in range(rng.randint(1, 6))]
        deploy_idx: set[int] = (
            set(rng.sample(range(count), k=min(count, rng.randint(1, 3))))
            if archetype == "contract_deployer" or rng.random() < 0.05
            else set()
        )
        if deploy_idx:
            labels["contract_deployer"] = True
        for k, at in enumerate(times):
            tx_id = f"0x{seed:04x}{i:06x}{k:054x}"
            created = _addr(rng) if k in deploy_idx else None
            to = None if created else rng.choice(targets)
            ds.transactions.append(
                TxRow(
                    tx_id=tx_id,
                    block_number=1_000_000 + i * 1000 + k,
                    slot=None,
                    block_hash=None,
                    block_time=at,
                    from_address=wallet,
                    to_address=to,
                    status="failed" if rng.random() < 0.03 else "success",
                    parse_status="complete",
                    finality="finalized",
                    program_ids=(),
                    provider="synthetic",
                    verification="unverified",
                    created_contract=created,
                )
            )
            token = archetype == "defi_trader" or rng.random() < 0.3
            direction = rng.choice(["in", "out"])
            party = rng.choice(targets) if rng.random() < 0.7 else _addr(rng)
            ds.transfers.append(
                TransferRow(
                    tx_id=tx_id,
                    event_key="log:0" if token else "native",
                    kind="erc20_transfer_log" if token else "native_tx_value",
                    standard="erc20" if token else "native",
                    asset=rng.choice(tokens) if token else "native",
                    from_address=wallet if direction == "out" else party,
                    to_address=party if direction == "out" else wallet,
                    amount_raw=rng.choice([10**18, rng.randint(1, 10**20)]),
                    decimals=18,
                    block_time=at,
                    position=1_000_000 + i * 1000 + k,
                    direction=direction,
                    counterparty=party,
                    verification="unverified",
                    provider="synthetic",
                )
            )
        out.append(SyntheticWallet(ds, as_of, labels))
    return out
