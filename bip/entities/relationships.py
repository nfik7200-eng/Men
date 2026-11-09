"""Build, store and query relationships between entities from stored canonical data.

A rebuild is bounded (``discovery.max_rows_scanned``), idempotent (rows are keyed by chain, kind,
source and target) and reads only canonical rows, never orphaned ones. A relationship that a
complete scan no longer finds is marked ``invalidated`` rather than deleted. A scan that hit the
row cap proves nothing about what it did not read, so it invalidates nothing.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.engine import Connection

from bip.config.settings import Settings
from bip.db import schema
from bip.db.canonical import canonical_transaction, orphaned_tx_ids
from bip.db.errors import InvalidRecordError
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.intel.opportunities import KNOWN_PROGRAMS

MAX_EVIDENCE = 20
MIN_REPEATED_TRANSFERS = 3
SIBLING_LIMIT = 50
KINDS = ("funded_by", "deployed_by", "transferred_to", "interacted_with")

EXPLANATIONS: dict[str, dict[str, str]] = {
    "deployed_by": {
        "statement": ("The stored creation transaction of this contract was sent by the target."),
        "limitations": (
            "It names who sent the transaction, not who controls or operates the project."
        ),
    },
    "funded_by": {
        "statement": (
            "The first native transfer this wallet received in the stored data came from the "
            "target."
        ),
        "limitations": (
            "Earlier funding may exist outside the stored data. A funder can be an exchange, a "
            "faucet or a service. It does not show common ownership."
        ),
    },
    "transferred_to": {
        "statement": "The source sent the target several transfers in the stored data.",
        "limitations": "Repeated transfers are common for exchanges and payment flows.",
    },
    "interacted_with": {
        "statement": "Stored transactions of the source invoked this program.",
        "limitations": "Interaction does not imply endorsement, ownership or intent.",
    },
}


@dataclass(slots=True)
class _Found:
    kind: str
    source_kind: str
    source_ref: str
    target_kind: str
    target_ref: str
    confidence: float
    weight: int
    tx_ids: list[str]
    seen_first: datetime
    seen_last: datetime
    note: str | None = None
    basis: str = "observed"


@dataclass(slots=True)
class _Scan:
    found: list[_Found] = field(default_factory=list[_Found])
    rows: int = 0
    truncated: bool = False


class RelationshipService:
    def __init__(
        self,
        repo: Repository,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._repo = repo
        self._cfg = settings.discovery
        self._clock = clock

    # ------------------------------------------------------------------ rebuild

    def rebuild(self, chain: str | None = None) -> dict[str, Any]:
        chains = [chain] if chain else self._chains()
        started = self._clock()
        summary: dict[str, Any] = {"chains": {}, "started_at": started.isoformat()}
        for c in chains:
            scans = {
                "deployed_by": self._deployments(c),
                "funded_by": self._funding(c),
                "transferred_to": self._repeated_transfers(c),
                "interacted_with": self._program_use(c),
            }
            per_kind: dict[str, Any] = {}
            for kind, scan in scans.items():
                stats = self._store(c, kind, scan, started)
                per_kind[kind] = {
                    "found": len(scan.found),
                    "rows_scanned": scan.rows,
                    "row_cap_reached": scan.truncated,
                    **stats,
                }
            summary["chains"][c] = per_kind
        summary["finished_at"] = self._clock().isoformat()
        return summary

    def _chains(self) -> list[str]:
        t = schema.transactions
        with self._repo.transaction() as conn:
            return sorted(str(c) for (c,) in conn.execute(select(t.c.chain).distinct()).all())

    def _tx_rows(self, chain: str, *extra: Any) -> tuple[Sequence[Any], bool]:
        t = schema.transactions
        cap = self._cfg.max_rows_scanned
        with self._repo.transaction() as conn:
            rows = conn.execute(
                select(
                    t.c.tx_id,
                    t.c.from_address,
                    t.c.block_time,
                    t.c.created_contract,
                    t.c.parse_status,
                    t.c.program_ids_json,
                )
                .where(
                    t.c.chain == chain, t.c.block_time.is_not(None), canonical_transaction(), *extra
                )
                .order_by(t.c.block_time.asc())
                .limit(cap + 1)
            ).all()
        return rows[:cap], len(rows) > cap

    def _deployments(self, chain: str) -> _Scan:
        t = schema.transactions
        rows, truncated = self._tx_rows(chain, t.c.created_contract.is_not(None))
        scan = _Scan(rows=len(rows), truncated=truncated)
        for r in rows:
            if not r.from_address:
                continue
            scan.found.append(
                _Found(
                    "deployed_by",
                    "contract",
                    str(r.created_contract),
                    "wallet",
                    str(r.from_address),
                    0.9 if r.parse_status == "complete" else 0.6,
                    1,
                    [str(r.tx_id)],
                    r.block_time,
                    r.block_time,
                )
            )
        return scan

    def _native_transfers(self, chain: str) -> tuple[Sequence[Any], bool]:
        tr = schema.transfers
        cap = self._cfg.max_rows_scanned
        with self._repo.transaction() as conn:
            rows = conn.execute(
                select(tr.c.tx_id, tr.c.from_address, tr.c.to_address, tr.c.block_time)
                .where(
                    tr.c.chain == chain,
                    tr.c.token.is_(None),
                    tr.c.from_address.is_not(None),
                    tr.c.to_address.is_not(None),
                    tr.c.block_time.is_not(None),
                    tr.c.tx_id.not_in(orphaned_tx_ids(chain)),
                )
                .order_by(tr.c.block_time.asc())
                .limit(cap + 1)
            ).all()
        return rows[:cap], len(rows) > cap

    def _funding(self, chain: str) -> _Scan:
        rows, truncated = self._native_transfers(chain)
        scan = _Scan(rows=len(rows), truncated=truncated)
        first: dict[str, Any] = {}
        pair_count: dict[tuple[str, str], int] = defaultdict(int)
        for r in rows:  # ascending in time: the first inbound transfer per recipient wins
            if r.from_address == r.to_address:
                continue
            first.setdefault(str(r.to_address), r)
            pair_count[(str(r.to_address), str(r.from_address))] += 1
        for recipient, r in first.items():
            funder = str(r.from_address)
            scan.found.append(
                _Found(
                    "funded_by",
                    "wallet",
                    recipient,
                    "wallet",
                    funder,
                    0.5,
                    pair_count[(recipient, funder)],
                    [str(r.tx_id)],
                    r.block_time,
                    r.block_time,
                    note="first inbound native transfer in the stored data",
                )
            )
        return scan

    def _repeated_transfers(self, chain: str) -> _Scan:
        tr = schema.transfers
        cap = self._cfg.max_rows_scanned
        with self._repo.transaction() as conn:
            rows = conn.execute(
                select(tr.c.tx_id, tr.c.from_address, tr.c.to_address, tr.c.block_time)
                .where(
                    tr.c.chain == chain,
                    tr.c.from_address.is_not(None),
                    tr.c.to_address.is_not(None),
                    tr.c.block_time.is_not(None),
                    tr.c.tx_id.not_in(orphaned_tx_ids(chain)),
                )
                .order_by(tr.c.block_time.asc())
                .limit(cap + 1)
            ).all()
        truncated = len(rows) > cap
        rows = rows[:cap]
        scan = _Scan(rows=len(rows), truncated=truncated)
        pairs: dict[tuple[str, str], list[Any]] = defaultdict(list)
        for r in rows:
            if r.from_address != r.to_address:
                pairs[(str(r.from_address), str(r.to_address))].append(r)
        for (source, target), items in pairs.items():
            if len(items) < MIN_REPEATED_TRANSFERS:
                continue
            scan.found.append(
                _Found(
                    "transferred_to",
                    "wallet",
                    source,
                    "wallet",
                    target,
                    round(min(1.0, len(items) / 10) * 0.8, 3),
                    len(items),
                    list(dict.fromkeys(str(i.tx_id) for i in items))[-MAX_EVIDENCE:],
                    items[0].block_time,
                    items[-1].block_time,
                )
            )
        return scan

    def _program_use(self, chain: str) -> _Scan:
        if chain != "solana":
            return _Scan()
        rows, truncated = self._tx_rows(chain)
        scan = _Scan(rows=len(rows), truncated=truncated)
        uses: dict[tuple[str, str], list[Any]] = defaultdict(list)
        for r in rows:
            if not r.from_address:
                continue
            for program in json.loads(r.program_ids_json or "[]"):
                if program not in KNOWN_PROGRAMS:
                    uses[(str(r.from_address), str(program))].append(r)
        for (wallet, program), items in uses.items():
            scan.found.append(
                _Found(
                    "interacted_with",
                    "wallet",
                    wallet,
                    "program",
                    program,
                    round(min(1.0, len(items) / 10) * 0.8, 3),
                    len(items),
                    [str(i.tx_id) for i in items[-MAX_EVIDENCE:]],
                    items[0].block_time,
                    items[-1].block_time,
                )
            )
        return scan

    # ------------------------------------------------------------------ storing

    def _store(self, chain: str, kind: str, scan: _Scan, started: datetime) -> dict[str, int]:
        r, e = schema.entity_relationships, schema.relationship_evidence
        created = updated = 0
        with self._repo.transaction() as conn:
            for f in scan.found:
                existing = conn.execute(
                    select(r.c.id).where(
                        r.c.chain == chain,
                        r.c.kind == f.kind,
                        r.c.source_ref == f.source_ref,
                        r.c.target_ref == f.target_ref,
                    )
                ).first()
                values = {
                    "source_kind": f.source_kind,
                    "target_kind": f.target_kind,
                    "basis": f.basis,
                    "confidence": f.confidence,
                    "weight": f.weight,
                    "status": "active",
                    "note": f.note,
                    "first_seen_at": f.seen_first,
                    "last_seen_at": f.seen_last,
                }
                if existing is None:
                    pk = conn.execute(
                        insert(r).values(
                            chain=chain,
                            kind=f.kind,
                            source_ref=f.source_ref,
                            target_ref=f.target_ref,
                            **values,
                        )
                    ).inserted_primary_key
                    assert pk is not None  # noqa: S101
                    rel_id = int(pk[0])
                    created += 1
                else:
                    rel_id = int(existing[0])
                    conn.execute(update(r).where(r.c.id == rel_id).values(**values))
                    conn.execute(delete(e).where(e.c.relationship_id == rel_id))
                    updated += 1
                for tx in dict.fromkeys(f.tx_ids):
                    conn.execute(insert(e).values(relationship_id=rel_id, chain=chain, tx_id=tx))
            invalidated = 0
            if not scan.truncated:
                seen = {(f.source_ref, f.target_ref) for f in scan.found}
                stale = conn.execute(
                    select(r.c.id, r.c.source_ref, r.c.target_ref).where(
                        r.c.chain == chain, r.c.kind == kind, r.c.status == "active"
                    )
                ).all()
                gone = [int(x.id) for x in stale if (x.source_ref, x.target_ref) not in seen]
                for start in range(0, len(gone), 400):
                    invalidated += int(
                        conn.execute(
                            update(r)
                            .where(r.c.id.in_(gone[start : start + 400]))
                            .values(
                                status="invalidated",
                                note="no longer supported by canonical stored data",
                            )
                        ).rowcount
                        or 0
                    )
        return {"created": created, "updated": updated, "invalidated": invalidated}

    # ------------------------------------------------------------------ queries

    def for_entity(
        self,
        chain: str,
        ref: str,
        *,
        kind: str | None = None,
        direction: str = "both",
        status: str = "active",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        if kind is not None and kind not in KINDS:
            raise InvalidRecordError(f"kind must be one of {', '.join(KINDS)}")
        r = schema.entity_relationships
        where_ref = {
            "out": r.c.source_ref == ref,
            "in": r.c.target_ref == ref,
            "both": or_(r.c.source_ref == ref, r.c.target_ref == ref),
        }.get(direction)
        if where_ref is None:
            raise InvalidRecordError("direction must be out, in or both")
        conds = [r.c.chain == chain, where_ref]
        if kind:
            conds.append(r.c.kind == kind)
        if status != "any":
            conds.append(r.c.status == status)
        with self._repo.transaction() as conn:
            total = int(
                conn.execute(select(func.count()).select_from(r).where(*conds)).scalar_one()
            )
            rows = (
                conn.execute(
                    select(r)
                    .where(*conds)
                    .order_by(r.c.weight.desc(), r.c.id)
                    .limit(limit)
                    .offset(offset)
                )
                .mappings()
                .all()
            )
            items = [self._item(conn, row) for row in rows]
            siblings = self._siblings(conn, chain, ref)
        return {
            "entity": {"chain": chain, "ref": ref},
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": items,
            "derived": siblings,
            "scope": (
                "Relationships observed in data BIP stored. They describe transactions, "
                "not people or organisations."
            ),
        }

    def _item(self, conn: Connection, row: Any) -> dict[str, Any]:
        e = schema.relationship_evidence
        tx_ids = [
            str(t)
            for (t,) in conn.execute(
                select(e.c.tx_id).where(e.c.relationship_id == row["id"]).limit(MAX_EVIDENCE)
            ).all()
        ]
        info = EXPLANATIONS[row["kind"]]
        return {
            "id": row["id"],
            "kind": row["kind"],
            "source": {"kind": row["source_kind"], "ref": row["source_ref"]},
            "target": {"kind": row["target_kind"], "ref": row["target_ref"]},
            "basis": row["basis"],
            "confidence": row["confidence"],
            "weight": row["weight"],
            "status": row["status"],
            "first_seen_at": row["first_seen_at"].isoformat(),
            "last_seen_at": row["last_seen_at"].isoformat(),
            "evidence": [{"kind": "transaction", "chain": row["chain"], "ref": t} for t in tx_ids],
            "explanation": {**info, **({"note": row["note"]} if row["note"] else {})},
        }

    def _siblings(self, conn: Connection, chain: str, ref: str) -> dict[str, Any] | None:
        """Wallets funded by the same first funder: a hypothesis, never an identity claim."""
        r = schema.entity_relationships
        funder = conn.execute(
            select(r.c.target_ref).where(
                r.c.chain == chain,
                r.c.kind == "funded_by",
                r.c.source_ref == ref,
                r.c.status == "active",
            )
        ).first()
        if funder is None:
            return None
        others = [
            str(x)
            for (x,) in conn.execute(
                select(r.c.source_ref)
                .where(
                    r.c.chain == chain,
                    r.c.kind == "funded_by",
                    r.c.target_ref == funder[0],
                    r.c.source_ref != ref,
                    r.c.status == "active",
                )
                .limit(SIBLING_LIMIT)
            ).all()
        ]
        return {
            "shares_first_funder_with": others,
            "first_funder": str(funder[0]),
            "basis": "hypothesis",
            "confidence": 0.2,
            "note": (
                "These wallets received their first stored native transfer from the same sender. "
                "Exchanges, faucets and payroll produce this too; it is not evidence of common "
                "ownership."
            ),
        }

    def counts(self) -> dict[str, dict[str, int]]:
        r = schema.entity_relationships
        with self._repo.transaction() as conn:
            rows = conn.execute(
                select(r.c.kind, r.c.status, func.count()).group_by(r.c.kind, r.c.status)
            ).all()
        out: dict[str, dict[str, int]] = {}
        for kind, status, n in rows:
            out.setdefault(str(kind), {})[str(status)] = int(n)
        return out
