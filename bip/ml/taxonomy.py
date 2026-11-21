"""Hierarchical, multi-label wallet classification taxonomy (version 1.0.0).

Two separate dimensions:

* **account type** (exactly one, possibly ``unknown_account_type``): what kind of address it is;
* **behavioural roles** (zero or more): observed operational behaviour.

Labels describe on-chain behaviour only. None of them identifies a person or organisation;
labels that sound like an entity (``exchange_hot_wallet``, ``fund_or_institutional_treasury``)
mean "behaviour consistent with that role" and require independently attributable evidence
before anyone may state the real-world operator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TAXONOMY_VERSION = "1.0.0"

Dimension = Literal["account_type", "behavioural_role"]


@dataclass(frozen=True, slots=True)
class Label:
    id: str
    dimension: Dimension
    chains: tuple[str, ...]
    description: str
    rule_detectable: bool = False
    """BIP has a deterministic, evidence-backed rule for it (see :mod:`bip.ml.rules`)."""
    sensitive: bool = False
    """Accusatory or entity-like: only ever reported with explicit caveats, never as fact."""


_BOTH = ("ethereum", "solana")
_ETH = ("ethereum",)
_SOL = ("solana",)

ACCOUNT_TYPES: tuple[Label, ...] = (
    Label(
        "externally_owned_account",
        "account_type",
        _ETH,
        "Signed at least one stored transaction (a key-controlled account).",
        True,
    ),
    Label(
        "smart_contract",
        "account_type",
        _ETH,
        "Created by a stored contract-creation transaction (receipt contractAddress).",
        True,
    ),
    Label(
        "token_contract",
        "account_type",
        _ETH,
        "Emitted ERC-20/721 Transfer logs in stored data.",
        True,
    ),
    Label("defi_protocol_contract", "account_type", _ETH, "DeFi protocol contract."),
    Label("bridge_contract", "account_type", _ETH, "Cross-chain bridge contract."),
    Label("multisig_account", "account_type", _BOTH, "Multisignature account, where identifiable."),
    Label(
        "solana_program", "account_type", _SOL, "Invoked as a program in stored transactions.", True
    ),
    Label(
        "token_account",
        "account_type",
        _SOL,
        "SPL token account (owner recorded in balance metadata).",
        True,
    ),
    Label(
        "wallet_account", "account_type", _SOL, "Paid fees for (signed) a stored transaction.", True
    ),
    Label("unknown_account_type", "account_type", _BOTH, "No reliable evidence of the type.", True),
)

BEHAVIOURAL_ROLES: tuple[Label, ...] = (
    Label(
        "exchange_hot_wallet",
        "behavioural_role",
        _BOTH,
        "Exchange-hot-wallet-like flows.",
        sensitive=True,
    ),
    Label(
        "exchange_cold_wallet",
        "behavioural_role",
        _BOTH,
        "Exchange-cold-wallet-like flows.",
        sensitive=True,
    ),
    Label(
        "exchange_deposit_wallet",
        "behavioural_role",
        _BOTH,
        "Deposit/collection-wallet-like sweeping.",
        sensitive=True,
    ),
    Label("defi_trader", "behavioural_role", _BOTH, "Frequent DeFi interactions."),
    Label(
        "high_frequency_activity",
        "behavioural_role",
        _BOTH,
        "High transaction frequency in the observed window.",
        True,
    ),
    Label("arbitrage_participant", "behavioural_role", _BOTH, "Arbitrage-like sequences."),
    Label("mev_participant", "behavioural_role", _ETH, "MEV-related patterns."),
    Label("market_maker", "behavioural_role", _BOTH, "Market-making-like two-sided flow."),
    Label("liquidity_provider", "behavioural_role", _BOTH, "Liquidity provision/removal."),
    Label(
        "high_value_participant",
        "behavioural_role",
        _BOTH,
        "Large values relative to other observed wallets (no prices are used).",
    ),
    Label("long_term_holder", "behavioural_role", _BOTH, "Long holding periods."),
    Label("short_term_trader", "behavioural_role", _BOTH, "Short holding periods."),
    Label("automated_bot", "behavioural_role", _BOTH, "Machine-regular timing."),
    Label("treasury_wallet", "behavioural_role", _BOTH, "Treasury-like disbursement."),
    Label("dao_related", "behavioural_role", _BOTH, "DAO-related activity."),
    Label("protocol_operator", "behavioural_role", _BOTH, "Protocol operation activity."),
    Label(
        "token_or_nft_deployer",
        "behavioural_role",
        _ETH,
        "Deployed a contract later observed emitting Transfer logs.",
        True,
    ),
    Label(
        "contract_deployer",
        "behavioural_role",
        _ETH,
        "Sent a stored contract-creation transaction.",
        True,
    ),
    Label("bridge_related", "behavioural_role", _BOTH, "Bridge-related activity."),
    Label("staking_participant", "behavioural_role", _BOTH, "Staking activity."),
    Label("validator_related", "behavioural_role", _BOTH, "Validator-related activity."),
    Label("miner_related", "behavioural_role", _ETH, "Pre-Merge mining-related activity."),
    Label(
        "fund_or_institutional_treasury",
        "behavioural_role",
        _BOTH,
        "Institution-treasury-like behaviour.",
        sensitive=True,
    ),
    Label(
        "low_activity_or_new",
        "behavioural_role",
        _BOTH,
        "Few observed transactions or first observed recently.",
        True,
    ),
    Label(
        "potentially_malicious_behaviour",
        "behavioural_role",
        _BOTH,
        "Behaviour resembling exploits or scams.",
        sensitive=True,
    ),
)

ALL: tuple[Label, ...] = ACCOUNT_TYPES + BEHAVIOURAL_ROLES
BY_ID: dict[str, Label] = {label.id: label for label in ALL}


def get(label_id: str) -> Label:
    label = BY_ID.get(label_id)
    if label is None:
        raise KeyError(label_id)
    return label


def roles_for(chain: str) -> tuple[Label, ...]:
    return tuple(r for r in BEHAVIOURAL_ROLES if chain in r.chains)


def account_types_for(chain: str) -> tuple[Label, ...]:
    return tuple(a for a in ACCOUNT_TYPES if chain in a.chains)
