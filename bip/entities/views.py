"""Read models over stored data: search, transaction, token and contract detail.

Everything here is computed from rows BIP stored, excludes orphaned transactions (their count is
reported instead), and states what it cannot know. A token view never claims holders or supply:
BIP has not seen the whole chain, only the wallets it ingested.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from sqlalchemy import func, select

from bip.contracts.provenance import Provenance
from bip.db import schema
from bip.db.canonical import canonical_transaction, orphaned_tx_ids
from bip.db.errors import InvalidRecordError, RecordNotFoundError
from bip.db.repository import Repository, canonical_address
from bip.intel.dataset import effective_verification

SEARCH_PER_KIND = 10
MIN_QUERY = 3
TOP_COUNTERPARTIES = 10


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _like(text_value: str) -> str:
    return text_value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search(repo: Repository, query: str, chain: str | None, limit: int) -> dict[str, Any]:
    """Prefix search over stored wallets, tokens, contracts and transactions."""
    q = query.strip()
    if len(q) < MIN_QUERY:
        raise InvalidRecordError(f"the query needs at least {MIN_QUERY} characters")
    w, tr, t = schema.wallets, schema.transfers, schema.transactions
    pattern = _like(q.lower())
    hits: list[dict[str, Any]] = []
    with repo.transaction() as conn:
        chain_w = [w.c.chain == chain] if chain else []
        chain_t = [t.c.chain == chain] if chain else []
        chain_x = [tr.c.chain == chain] if chain else []
        for row in conn.execute(
            select(w.c.chain, w.c.address)
            .where(func.lower(w.c.address).like(pattern, escape="\\"), *chain_w)
            .order_by(w.c.chain, w.c.address)
            .limit(SEARCH_PER_KIND)
        ).all():
            hits.append({"kind": "wallet", "chain": row.chain, "ref": row.address})
        for row in conn.execute(
            select(tr.c.chain, tr.c.token)
            .where(
                tr.c.token.is_not(None), func.lower(tr.c.token).like(pattern, escape="\\"), *chain_x
            )
            .distinct()
            .limit(SEARCH_PER_KIND)
        ).all():
            hits.append({"kind": "token", "chain": row.chain, "ref": row.token})
        for row in conn.execute(
            select(t.c.chain, t.c.created_contract)
            .where(
                t.c.created_contract.is_not(None),
                func.lower(t.c.created_contract).like(pattern, escape="\\"),
                *chain_t,
            )
            .distinct()
            .limit(SEARCH_PER_KIND)
        ).all():
            hits.append({"kind": "contract", "chain": row.chain, "ref": row.created_contract})
        pe = schema.pool_events
        for row in conn.execute(
            select(pe.c.chain, pe.c.pool)
            .where(
                func.lower(pe.c.pool).like(pattern, escape="\\"),
                *([pe.c.chain == chain] if chain else []),
            )
            .distinct()
            .limit(SEARCH_PER_KIND)
        ).all():
            hits.append({"kind": "pool", "chain": row.chain, "ref": row.pool})
        for row in conn.execute(
            select(t.c.chain, t.c.tx_id)
            .where(func.lower(t.c.tx_id).like(pattern, escape="\\"), *chain_t)
            .order_by(t.c.id.desc())
            .limit(SEARCH_PER_KIND)
        ).all():
            hits.append({"kind": "transaction", "chain": row.chain, "ref": row.tx_id})
    return {
        "query": q,
        "total": len(hits[:limit]),
        "items": hits[:limit],
        "scope": "Only entities BIP has stored. A miss does not mean the entity does not exist.",
    }


def transaction_detail(repo: Repository, chain: str, tx_id: str) -> dict[str, Any]:
    t, tr, bc = schema.transactions, schema.transfers, schema.balance_changes
    with repo.transaction() as conn:
        row = (
            conn.execute(select(t).where(t.c.chain == chain, t.c.tx_id == tx_id)).mappings().first()
        )
        if row is None:
            raise RecordNotFoundError(f"no stored {chain} transaction {tx_id}")
        transfers = (
            conn.execute(
                select(tr).where(tr.c.chain == chain, tr.c.tx_id == tx_id).order_by(tr.c.id)
            )
            .mappings()
            .all()
        )
        changes = (
            conn.execute(
                select(bc).where(bc.c.chain == chain, bc.c.tx_id == tx_id).order_by(bc.c.id)
            )
            .mappings()
            .all()
        )
        sig = schema.signal_evidence
        signals = conn.execute(
            select(schema.signals.c.id, schema.signals.c.title, schema.signals.c.status)
            .join(sig, sig.c.signal_id == schema.signals.c.id)
            .where(sig.c.chain == chain, sig.c.tx_id == tx_id)
        ).all()
        rev = schema.relationship_evidence
        rels = conn.execute(
            select(
                schema.entity_relationships.c.id,
                schema.entity_relationships.c.kind,
                schema.entity_relationships.c.status,
            )
            .join(rev, rev.c.relationship_id == schema.entity_relationships.c.id)
            .where(rev.c.chain == chain, rev.c.tx_id == tx_id)
        ).all()
    prov = Provenance.model_validate_json(row["provenance_json"])
    verification = effective_verification(repo, chain, [tx_id]).get(tx_id)
    return {
        "chain": chain,
        "tx_id": tx_id,
        "position": {"block_number": row["block_number"], "slot": row["slot"]},
        "block_time": _iso(row["block_time"]),
        "from": row["from_address"],
        "to": row["to_address"],
        "status": row["status"],
        "finality": row["finality"],
        "orphaned": row["finality"] == "orphaned",
        "parse_status": row["parse_status"],
        "created_contract": row["created_contract"],
        "verification": verification or prov.verification_status.value,
        "provenance": {
            "source_provider": prov.source_provider,
            "source_method": prov.source_method,
            "collected_at": _iso(prov.collected_at),
            "verification_status": prov.verification_status.value,
        },
        "transfers": [
            {
                "event_key": x["event_key"],
                "standard": x["standard"],
                "token": x["token"],
                "from": x["from_address"],
                "to": x["to_address"],
                "amount_raw": x["amount_raw"],
                "decimals": x["decimals"],
                "observation_kind": x["observation_kind"],
            }
            for x in transfers
        ],
        "balance_changes": [
            {
                "account": x["account"],
                "owner": x["owner"],
                "mint": x["mint"],
                "kind": x["observation_kind"],
                "pre_raw": x["pre_raw"],
                "post_raw": x["post_raw"],
            }
            for x in changes
        ],
        "signals": [{"id": s.id, "title": s.title, "status": s.status} for s in signals],
        "relationships": [{"id": r.id, "kind": r.kind, "status": r.status} for r in rels],
    }


def token_detail(repo: Repository, chain: str, address: str) -> dict[str, Any]:
    ref = canonical_address(chain, address)
    tr, t = schema.transfers, schema.transactions
    orphans = orphaned_tx_ids(chain)
    with repo.transaction() as conn:
        base = (tr.c.chain == chain, tr.c.token == ref.address, tr.c.tx_id.not_in(orphans))
        count = int(conn.execute(select(func.count()).select_from(tr).where(*base)).scalar_one())
        excluded = int(
            conn.execute(
                select(func.count())
                .select_from(tr)
                .where(tr.c.chain == chain, tr.c.token == ref.address, tr.c.tx_id.in_(orphans))
            ).scalar_one()
        )
        created = conn.execute(
            select(t.c.tx_id, t.c.from_address, t.c.block_time).where(
                t.c.chain == chain, t.c.created_contract == ref.address, canonical_transaction()
            )
        ).first()
        if count == 0 and created is None:
            raise RecordNotFoundError(f"BIP has stored no transfers of {ref.address}")
        rows = conn.execute(
            select(
                tr.c.from_address, tr.c.to_address, tr.c.block_time, tr.c.amount_raw, tr.c.decimals
            )
            .where(*base)
            .order_by(tr.c.block_time.asc())
            .limit(20_000)
        ).all()
    senders = Counter(str(r.from_address) for r in rows if r.from_address)
    receivers = Counter(str(r.to_address) for r in rows if r.to_address)
    times = [r.block_time for r in rows if r.block_time is not None]
    decimals = next((r.decimals for r in rows if r.decimals is not None), None)
    volume = sum(int(r.amount_raw) for r in rows if str(r.amount_raw).isdigit())
    counterparties: Counter[str] = Counter()
    counterparties.update(senders)
    counterparties.update(receivers)
    return {
        "chain": chain,
        "token": ref.address,
        "transfers": count,
        "transfers_read": len(rows),
        "orphaned_transfers_excluded": excluded,
        "distinct_senders": len(senders),
        "distinct_recipients": len(receivers),
        "first_transfer_at": _iso(times[0]) if times else None,
        "last_transfer_at": _iso(times[-1]) if times else None,
        "decimals": decimals,
        "volume_raw": str(volume),
        "top_counterparties": [
            {"address": a, "transfers": n}
            for a, n in counterparties.most_common(TOP_COUNTERPARTIES)
        ],
        "created": None
        if created is None
        else {
            "tx_id": created.tx_id,
            "deployer": created.from_address,
            "at": _iso(created.block_time),
        },
        "coverage": {
            "basis": "stored transfers of ingested wallets",
            "not_known": [
                "holders and their balances",
                "total supply",
                "transfers between wallets BIP did not ingest",
            ],
        },
    }


def contract_detail(repo: Repository, chain: str, address: str) -> dict[str, Any]:
    ref = canonical_address(chain, address)
    t, tr = schema.transactions, schema.transfers
    with repo.transaction() as conn:
        created = conn.execute(
            select(t.c.tx_id, t.c.from_address, t.c.block_time, t.c.finality).where(
                t.c.chain == chain, t.c.created_contract == ref.address, canonical_transaction()
            )
        ).first()
        if chain == "solana":
            calls = and_or_program(t, ref.address)
        else:
            calls = t.c.to_address == ref.address
        called = conn.execute(
            select(
                func.count(),
                func.count(func.distinct(t.c.from_address)),
                func.min(t.c.block_time),
                func.max(t.c.block_time),
            ).where(t.c.chain == chain, calls, canonical_transaction())
        ).one()
        emitted = int(
            conn.execute(
                select(func.count())
                .select_from(tr)
                .where(
                    tr.c.chain == chain,
                    tr.c.token == ref.address,
                    tr.c.tx_id.not_in(orphaned_tx_ids(chain)),
                )
            ).scalar_one()
        )
    if created is None and called[0] == 0 and emitted == 0:
        raise RecordNotFoundError(f"BIP has stored nothing about {ref.address}")
    return {
        "chain": chain,
        "address": ref.address,
        "kind": "program" if chain == "solana" else "contract",
        "created": None
        if created is None
        else {
            "tx_id": created.tx_id,
            "deployer": created.from_address,
            "at": _iso(created.block_time),
            "finality": created.finality,
        },
        "interactions": {
            "transactions": int(called[0]),
            "distinct_callers": int(called[1]),
            "first_at": _iso(called[2]),
            "last_at": _iso(called[3]),
        },
        "transfer_events_emitted": emitted,
        "coverage": {
            "basis": "stored transactions of ingested wallets",
            "not_known": [
                "calls from wallets BIP did not ingest",
                "source code, verification status or ownership",
            ],
        },
    }


def and_or_program(t: Any, program: str) -> Any:
    """Condition matching Solana transactions whose program list contains ``program`` exactly."""
    return func.instr(t.c.program_ids_json, json.dumps(program)) > 0
