"""The 11 built-in case templates. Playbooks only; they assert nothing about any address."""

from __future__ import annotations

from bip.investigations.models import CaseTemplate, CaseType


def _t(
    case_type: CaseType,
    name: str,
    description: str,
    required_evidence: tuple[str, ...],
    suggested_hypotheses: tuple[str, ...],
    initial_leads: tuple[str, ...],
    analysis_steps: tuple[str, ...],
) -> CaseTemplate:
    return CaseTemplate(
        case_type=case_type,
        name=name,
        description=description,
        required_evidence=required_evidence,
        suggested_hypotheses=suggested_hypotheses,
        initial_leads=initial_leads,
        analysis_steps=analysis_steps,
    )


_TEMPLATES: tuple[CaseTemplate, ...] = (
    _t(
        "rugpull",
        "Rug pull investigation",
        "Token or pool abandoned after liquidity was removed by insiders.",
        ("token contract", "liquidity add/remove transactions", "deployer address"),
        ("Deployer removed liquidity shortly after launch", "Top holders are linked to deployer"),
        ("Trace deployer funding source", "List LP token holders at removal time"),
        ("Reconstruct liquidity timeline", "Map holder concentration", "Trace proceeds onward"),
    ),
    _t(
        "hack_tracking",
        "Hack fund tracking",
        "Follow stolen funds from an exploited protocol or wallet.",
        ("exploit transactions", "victim address", "attacker receiving addresses"),
        ("Funds were split and bridged cross-chain", "Funds moved to a mixer or exchange"),
        ("Follow largest outflows from attacker", "Check bridge and exchange deposits"),
        ("Compute loss from transfers", "Trace hops by value", "Flag service endpoints"),
    ),
    _t(
        "cluster",
        "Wallet cluster analysis",
        "Evaluate whether a set of addresses is plausibly controlled by one actor.",
        ("seed addresses", "shared funding evidence", "co-spend or timing signals"),
        ("Addresses share a common funder", "Addresses act in synchronised timing"),
        ("Find common funders", "Compare activity timing"),
        ("Build heuristic links", "Score cluster confidence", "Document false-positive risks"),
    ),
    _t(
        "insider",
        "Insider activity review",
        "Detect trading ahead of public events by connected wallets.",
        ("event timestamp", "pre-event trades", "wallet funding history"),
        ("Wallets accumulated before announcement", "Wallets are connected to project team"),
        ("List early buyers before event", "Check funding links to project wallets"),
        ("Align trades to event timeline", "Test against random baseline"),
    ),
    _t(
        "ecosystem",
        "Ecosystem mapping",
        "Map protocols, contracts and actors around a project.",
        ("core contracts", "top interacting addresses", "token flows"),
        ("A small set of actors dominates activity",),
        ("Enumerate deployed contracts", "Rank counterparties by volume"),
        ("Build interaction graph", "Identify hubs", "Summarise flows"),
    ),
    _t(
        "sanctions_aml",
        "Sanctions and AML exposure",
        "Assess exposure to sanctioned or high-risk counterparties using published lists.",
        ("address under review", "published list match with source", "counterparty paths"),
        ("Address has direct exposure to a listed entity", "Exposure is indirect only"),
        ("Check against official lists", "Trace counterparties within N hops"),
        ("Record list source and date", "Measure exposure by hop and value"),
    ),
    _t(
        "mev_sandwich",
        "MEV sandwich attack",
        "Identify front-run and back-run transactions around a victim swap.",
        ("victim swap tx", "adjacent block transactions", "pool state"),
        ("Same actor placed trades immediately before and after the victim",),
        ("Inspect neighbouring transactions in block", "Check bot repeat behaviour"),
        ("Order transactions by index", "Compute victim slippage and attacker profit"),
    ),
    _t(
        "flash_loan",
        "Flash loan analysis",
        "Examine a single-transaction borrow, manipulation and repayment.",
        ("transaction hash", "loan provider events", "price-affecting calls"),
        ("Loan was used to manipulate an oracle or pool price",),
        ("Decode call trace", "Identify manipulated pool"),
        ("Decompose transaction steps", "Compute net profit and victim loss"),
    ),
    _t(
        "nft_wash_trading",
        "NFT wash trading",
        "Detect circular or self-funded NFT trades inflating volume.",
        ("collection contract", "sale transactions", "buyer/seller funding links"),
        ("Sales cycle among few wallets", "Buyers funded by sellers"),
        ("Find round-trip sales", "Check buyer funding sources"),
        ("Build trade graph", "Detect cycles", "Quantify inflated volume"),
    ),
    _t(
        "pump_dump",
        "Pump and dump",
        "Assess coordinated price inflation followed by distribution.",
        ("token price/volume series", "early holder list", "sell transactions"),
        ("Early holders sold into the rally", "Buying was coordinated"),
        ("Identify top sellers at peak", "Compare holder funding sources"),
        ("Detect volume anomaly window", "Trace sellers", "Estimate realised gains"),
    ),
    _t(
        "phishing_tracking",
        "Phishing campaign tracking",
        "Track approvals or transfers drained via a phishing contract or address.",
        ("drainer address", "victim approvals/transfers", "reporting source"),
        ("Multiple victims drained by the same address set",),
        ("List victims by approval target", "Follow proceeds to exits"),
        ("Aggregate losses", "Cluster drainer infrastructure", "Trace cash-out"),
    ),
)

TEMPLATES: dict[CaseType, CaseTemplate] = {t.case_type: t for t in _TEMPLATES}


def get_template(case_type: CaseType) -> CaseTemplate:
    return TEMPLATES[case_type]
