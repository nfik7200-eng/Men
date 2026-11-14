"""Deterministic, evidence-backed baseline classifier ``bip-rules`` (version 1.0.0).

Always available, independent of trained models and of any LLM. Every rule cites the stored
records that triggered it. Rule matches carry ``status = "rule_based"`` and **no probability**:
their precision has not been measured, so they are never presented as calibrated confidence.

Account type (first match wins):

* Ethereum: created by a stored contract-creation receipt → ``smart_contract``; emitted stored
  Transfer logs → ``token_contract``; sent a stored transaction → ``externally_owned_account``
  (only key-controlled accounts originate transactions); otherwise ``unknown_account_type``.
* Solana: invoked as a program → ``solana_program``; recorded as a token account with an owner
  in balance metadata → ``token_account``; paid a fee (signed) → ``wallet_account``.

Roles: ``contract_deployer``, ``token_or_nft_deployer``, ``high_frequency_activity``,
``low_activity_or_new``. All other roles need a trained model with validated thresholds.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from bip.db import schema
from bip.db.canonical import canonical_transaction, orphaned_tx_ids
from bip.db.repository import Repository
from bip.intel.dataset import WalletDataset
from bip.ml.features import FeatureVector

RULES_NAME = "bip-rules"
RULES_VERSION = "1.0.0"
J = dict[str, Any]


def _ev(kind: str, ref: str, note: str) -> J:
    return {"type": kind, "ref": ref, "note": note}


def account_type(repo: Repository, ds: WalletDataset) -> J:
    t, tr, bc = schema.transactions, schema.transfers, schema.balance_changes
    a, chain = ds.address, ds.chain
    with repo.transaction() as conn:
        if chain == "ethereum":
            created = conn.execute(
                select(t.c.tx_id).where(
                    t.c.chain == chain, t.c.created_contract == a, canonical_transaction()
                )
            ).first()
            if created:
                return _result(
                    "smart_contract",
                    [
                        _ev(
                            "transaction",
                            created[0],
                            "receipt.contractAddress of a stored creation transaction",
                        )
                    ],
                )
            token = conn.execute(
                select(tr.c.tx_id).where(
                    tr.c.chain == chain, tr.c.token == a, tr.c.tx_id.not_in(orphaned_tx_ids(chain))
                )
            ).first()
            if token:
                return _result(
                    "token_contract",
                    [_ev("transfer", token[0], "address emitted a stored Transfer log")],
                )
            sent = conn.execute(
                select(t.c.tx_id).where(
                    t.c.chain == chain, t.c.from_address == a, canonical_transaction()
                )
            ).first()
            if sent:
                return _result(
                    "externally_owned_account",
                    [_ev("transaction", sent[0], "address signed (sent) a stored transaction")],
                )
        else:
            program = conn.execute(
                select(t.c.tx_id).where(
                    t.c.chain == chain,
                    func.instr(t.c.program_ids_json, f'"{a}"') > 0,
                    canonical_transaction(),
                )
            ).first()
            if program:
                return _result(
                    "solana_program",
                    [_ev("transaction", program[0], "address invoked as a program")],
                )
            acct = conn.execute(
                select(bc.c.tx_id, bc.c.owner).where(
                    bc.c.chain == chain,
                    bc.c.account == a,
                    bc.c.owner.is_not(None),
                    bc.c.owner != a,
                    bc.c.tx_id.not_in(orphaned_tx_ids(chain)),
                )
            ).first()
            if acct:
                return _result(
                    "token_account",
                    [_ev("balance_change", acct[0], f"token account owned by {acct[1]}")],
                )
            payer = conn.execute(
                select(t.c.tx_id).where(
                    t.c.chain == chain, t.c.from_address == a, canonical_transaction()
                )
            ).first()
            if payer:
                return _result(
                    "wallet_account",
                    [
                        _ev(
                            "transaction",
                            payer[0],
                            "address paid the fee (signed) a stored transaction",
                        )
                    ],
                )
    return {
        "label": "unknown_account_type",
        "status": "insufficient_evidence",
        "confidence": None,
        "source": f"{RULES_NAME}@{RULES_VERSION}",
        "evidence": [],
        "explanation": "No stored record establishes the account type.",
    }


def _result(label: str, evidence: list[J]) -> J:
    return {
        "label": label,
        "status": "rule_based",
        "confidence": None,
        "source": f"{RULES_NAME}@{RULES_VERSION}",
        "evidence": evidence,
        "explanation": "Deterministic rule over stored evidence; precision not measured.",
    }


def roles(repo: Repository, ds: WalletDataset, fv: FeatureVector, as_of: datetime) -> list[J]:
    out: list[J] = []
    v = fv.values
    deploys = [
        t
        for t in ds.transactions
        if t.created_contract
        and t.from_address == ds.address
        and t.block_time is not None
        and t.block_time <= as_of
    ]
    if deploys:
        out.append(
            _role(
                "contract_deployer",
                [
                    _ev("transaction", t.tx_id, f"created {t.created_contract}")
                    for t in deploys[:10]
                ],
                f"{len(deploys)} contract-creation transaction(s) observed.",
            )
        )
        created = {t.created_contract for t in deploys}
        tr = schema.transfers
        with repo.transaction() as conn:
            tokens = conn.execute(
                select(tr.c.token, func.min(tr.c.tx_id))
                .where(
                    tr.c.chain == ds.chain,
                    tr.c.token.in_(created),
                    # point in time: only Transfer logs at or before as_of count
                    tr.c.block_time.is_not(None),
                    tr.c.block_time <= as_of,
                    tr.c.tx_id.not_in(orphaned_tx_ids(ds.chain)),
                )
                .group_by(tr.c.token)
            ).all()
        if tokens:
            out.append(
                _role(
                    "token_or_nft_deployer",
                    [
                        _ev("transfer", r[1], f"deployed contract {r[0]} emitted Transfer logs")
                        for r in tokens
                    ],
                    f"{len(tokens)} deployed contract(s) emitted Transfer logs by as_of.",
                )
            )
    if (
        v["tx_per_active_day"] is not None
        and v["tx_count"] is not None
        and v["tx_count"] >= 10
        and v["tx_per_active_day"] >= 10
    ):
        out.append(
            _role(
                "high_frequency_activity",
                [_ev("feature", "tx_per_active_day", f"{v['tx_per_active_day']:.1f}")],
                f"{v['tx_per_active_day']:.1f} transactions per active day (≥ 10, with "
                "≥ 10 transactions).",
            )
        )
    first = v["days_since_first_seen"]
    if v["tx_count"] is not None and (v["tx_count"] < 5 or (first is not None and first <= 7)):
        why = (
            f"only {int(v['tx_count'])} transactions observed"
            if v["tx_count"] < 5
            else f"first observed {first:.1f} days before as_of"
        )
        out.append(
            _role(
                "low_activity_or_new",
                [_ev("feature", "tx_count", str(int(v["tx_count"])))],
                f"Low or new activity: {why}. This describes BIP's observations, not "
                "the wallet's real creation date.",
            )
        )
    return out


def _role(label: str, evidence: list[J], explanation: str) -> J:
    return {
        "label": label,
        "status": "rule_based",
        "confidence": None,
        "source": f"{RULES_NAME}@{RULES_VERSION}",
        "evidence": evidence,
        "explanation": explanation,
    }


def recent(as_of: datetime, days: int) -> datetime:
    return as_of - timedelta(days=days)
