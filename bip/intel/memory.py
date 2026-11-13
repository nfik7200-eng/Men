"""Intelligence memory: persistent entities and an append-only fact history (Stage 3, schema v18).

``MemoryService.refresh`` folds what BIP has *already stored* into memory. It reads the local
database only and never contacts a provider. Each source table is read incrementally (by row id,
from a per-source cursor) and bounded per run, so a refresh is resumable and repeatable:

- an entity is one chain address; two addresses are never merged because they behave alike;
- a fact with the same value as the current one only raises its ``confirmations`` (or does
  nothing when the same source repeats); a different value appends a ``changed`` fact that
  supersedes the old one; conflicting reviewed labels append a ``contradiction``;
- facts carry an evidence class (observed, statistical, heuristic, hypothesis, reviewed), their
  source table and row, and the transaction they rest on, so a reorganisation invalidates them
  (``observations._invalidate_derived``) and the history shows why.

Activity measurements are recorded as *levels* (powers of two), so the history records changes in
behaviour, not every new transfer.
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, union, update
from sqlalchemy.engine import Connection

from bip.config.settings import MemorySettings
from bip.core.exceptions import BIPError
from bip.db import schema
from bip.db.canonical import canonical_transaction, orphaned_tx_ids
from bip.db.repository import Repository
from bip.db.types import utcnow

CURSOR_PREFIX = "memory:cursor"
PENDING_PREFIX = "memory:pending"
LAST_RUN_KEY = "memory:last_run"

STATISTICAL_DETECTORS = frozenset(
    {
        "large_transfer_mad",
        "activity_burst",
        "dormant_reactivation",
        "holder_concentration",
        "pool_reserve_change",
    }
)
OBSERVED_DETECTORS = frozenset(
    {"new_contract_deployment", "new_liquidity_pool", "new_token_activity", "solana_new_entities"}
)
SOLANA_KIND = {"mint": "token", "program": "program", "pool": "pool"}
KIND_RANK = {"wallet": 0, "token": 1, "pool": 1, "program": 1, "contract": 2}


@dataclass
class RefreshSummary:
    chain: str
    rows_read: dict[str, int] = field(default_factory=dict[str, int])
    entities_touched: int = 0
    facts_new: int = 0
    facts_changed: int = 0
    facts_confirmed: int = 0
    contradictions: int = 0
    facts_outdated: int = 0
    truncated: list[str] = field(default_factory=list[str])

    def public(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "rows_read": self.rows_read,
            "entities_touched": self.entities_touched,
            "facts_new": self.facts_new,
            "facts_changed": self.facts_changed,
            "facts_confirmed": self.facts_confirmed,
            "contradictions": self.contradictions,
            "facts_outdated": self.facts_outdated,
            "truncated_sources": self.truncated,
            "source": "local database only",
        }


def _level(n: int) -> int:
    """Activity level: 0 for none, then 1 + floor(log2(n))."""
    return 0 if n <= 0 else 1 + int(math.log2(n))


class MemoryBusyError(BIPError):
    """Another intelligence-memory refresh is running."""


class MemoryService:
    def __init__(self, repo: Repository, cfg: MemorySettings) -> None:
        self._repo = repo
        self._cfg = cfg
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public reads

    def entity(self, chain: str, ref: str) -> dict[str, Any] | None:
        e, f = schema.entities, schema.entity_facts
        with self._repo.transaction() as conn:
            row = (
                conn.execute(select(e).where(e.c.chain == chain, e.c.ref == ref)).mappings().first()
            )
            if row is None:
                return None
            facts = (
                conn.execute(
                    select(f).where(f.c.entity_id == row["id"]).order_by(f.c.id.desc()).limit(500)
                )
                .mappings()
                .all()
            )
        history = [self._fact(r) for r in facts]
        current = [h for h in history if h["status"] == "current"]
        return {
            "chain": row["chain"],
            "ref": row["ref"],
            "kind": row["kind"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "updated_at": row["updated_at"],
            "current_facts": current,
            "contradictions": [h for h in current if h["change"] == "contradiction"],
            "history": history,
        }

    def search(
        self, *, chain: str | None, kind: str | None, q: str | None, limit: int
    ) -> list[dict[str, Any]]:
        e = schema.entities
        stmt = select(e)
        if chain:
            stmt = stmt.where(e.c.chain == chain)
        if kind:
            stmt = stmt.where(e.c.kind == kind)
        if q:  # an exact, case-sensitive prefix (no LIKE wildcards; base58 is case-sensitive)
            stmt = stmt.where(func.substr(e.c.ref, 1, len(q)) == q)
        stmt = stmt.order_by(e.c.last_seen_at.desc().nulls_last(), e.c.id.desc()).limit(limit)
        with self._repo.transaction() as conn:
            return [
                {
                    "chain": r["chain"],
                    "ref": r["ref"],
                    "kind": r["kind"],
                    "first_seen_at": r["first_seen_at"],
                    "last_seen_at": r["last_seen_at"],
                }
                for r in conn.execute(stmt).mappings()
            ]

    def last_run(self) -> dict[str, Any] | None:
        raw = self._repo.get_meta(LAST_RUN_KEY)
        return json.loads(raw) if raw else None

    @staticmethod
    def _fact(r: Any) -> dict[str, Any]:
        value = json.loads(r["value_json"])
        value.pop("signature", None)
        return {
            "id": r["id"],
            "key": r["fact_key"],
            "value": value,
            "evidence_class": r["evidence_class"],
            "change": r["change"],
            "status": r["status"],
            "source": r["source"],
            "source_tx": r["source_tx"],
            "observed_at": r["observed_at"],
            "recorded_at": r["recorded_at"],
            "confirmed_at": r["confirmed_at"],
            "confirmations": r["confirmations"],
            "supersedes_id": r["supersedes_id"],
        }

    # ------------------------------------------------------------------ refresh

    def refresh(self, chain: str) -> dict[str, Any]:
        """Fold newly stored data for ``chain`` into memory once, within the configured bounds.

        Each source is its own transaction (its rows, its facts and its cursor commit together),
        so a failure loses nothing already folded and never holds the database writer for the
        whole run. Entities beyond the per-run aggregate cap are carried to the next run, not
        dropped. Raises ``MemoryBusyError`` when another refresh is running."""
        if not self._lock.acquire(blocking=False):
            raise MemoryBusyError("an intelligence-memory refresh is already running")
        try:
            return self._refresh(chain)
        finally:
            self._lock.release()

    def _refresh(self, chain: str) -> dict[str, Any]:
        s = RefreshSummary(chain)
        touched_total = 0
        phases = [
            self._deployments,
            self._transfers,
            self._pools,
            *([self._solana] if chain == "solana" else []),
        ]
        for phase in phases:
            with self._repo.transaction() as conn:
                touched: dict[str, str] = {}
                phase(conn, chain, s, touched)
                # queued in the same transaction as the phase's cursor: a later failure loses
                # nothing that this phase folded in
                self._queue(conn, chain, touched)
                touched_total += len(touched)
        for other in (self._signals, self._relationships, self._classifications, self._labels):
            with self._repo.transaction() as conn:
                other(conn, chain, s)
        cap = self._cfg.max_entities_per_run
        with self._repo.transaction() as conn:
            pending = self._queue(conn, chain, {})  # takes the write lock before reading
            refs = list(pending)
            if len(refs) > cap:
                s.truncated.append("aggregates")
            for ref in refs[:cap]:
                self._aggregates(conn, chain, ref, s)
            self._write_pending(conn, chain, {r: pending[r] for r in refs[cap:]})
        s.entities_touched = touched_total
        out = s.public()
        out["aggregates_pending"] = max(0, len(refs) - cap)
        out["finished_at"] = utcnow().isoformat()
        self._repo.set_meta(LAST_RUN_KEY, json.dumps(out, default=str))
        return out

    def _queue(self, conn: Connection, chain: str, refs: dict[str, str]) -> dict[str, str]:
        """Merge ``refs`` into the pending-aggregate queue and return the whole queue."""
        m = schema.app_metadata
        key = f"{PENDING_PREFIX}:{chain}"
        # Write first, so SQLite's write lock is held before the read (pysqlite opens the
        # transaction at the first write); a concurrent reorg requeue cannot be overwritten.
        conn.execute(update(m).where(m.c.key == key).values(updated_at=utcnow()))
        raw = conn.execute(select(m.c.value).where(m.c.key == key)).scalar_one_or_none()
        pending: dict[str, str] = json.loads(raw) if raw else {}
        pending.update(refs)
        self._write_pending(conn, chain, pending)
        return pending

    @staticmethod
    def _write_pending(conn: Connection, chain: str, pending: dict[str, str]) -> None:
        m = schema.app_metadata
        key = f"{PENDING_PREFIX}:{chain}"
        value = json.dumps(pending)
        hit = conn.execute(update(m).where(m.c.key == key).values(value=value, updated_at=utcnow()))
        if not hit.rowcount:
            conn.execute(insert(m).values(key=key, value=value, updated_at=utcnow()))

    # ------------------------------------------------------------------ sources

    def _rows(
        self, conn: Connection, chain: str, name: str, table: Any, s: RefreshSummary, *where: Any
    ) -> list[Any]:
        """Rows of ``table`` after this source's cursor, bounded; advances the cursor."""
        key = f"{CURSOR_PREFIX}:{name}:{chain}"
        m = schema.app_metadata
        raw = conn.execute(select(m.c.value).where(m.c.key == key)).scalar_one_or_none()
        after = int(raw) if raw else 0
        cap = self._cfg.max_rows_per_source
        rows = (
            conn.execute(
                select(table).where(table.c.id > after, *where).order_by(table.c.id).limit(cap + 1)
            )
            .mappings()
            .all()
        )
        if len(rows) > cap:
            rows = rows[:cap]
            s.truncated.append(name)
        if rows:
            value = str(rows[-1]["id"])
            if raw is None:
                conn.execute(insert(m).values(key=key, value=value, updated_at=utcnow()))
            else:
                conn.execute(
                    update(m).where(m.c.key == key).values(value=value, updated_at=utcnow())
                )
        return list(rows)

    def _rotating(
        self, conn: Connection, chain: str, name: str, table: Any, s: RefreshSummary, *where: Any
    ) -> list[Any]:
        """A bounded window of a table whose rows change in place (labels, relationships): each
        run reads the next window by id and wraps around, so no row is starved by the cap."""
        key = f"{CURSOR_PREFIX}:{name}:{chain}"
        m = schema.app_metadata
        raw = conn.execute(select(m.c.value).where(m.c.key == key)).scalar_one_or_none()
        after = int(raw) if raw else 0
        cap = self._cfg.max_rows_per_source
        rows = list(
            conn.execute(
                select(table).where(table.c.id > after, *where).order_by(table.c.id).limit(cap)
            )
            .mappings()
            .all()
        )
        if len(rows) < cap and after:
            rows += (
                conn.execute(
                    select(table)
                    .where(table.c.id <= after, *where)
                    .order_by(table.c.id)
                    .limit(cap - len(rows))
                )
                .mappings()
                .all()
            )
        if len(rows) >= cap:
            s.truncated.append(name)
        value = str(rows[-1]["id"]) if len(rows) >= cap else "0"
        if raw is None:
            conn.execute(insert(m).values(key=key, value=value, updated_at=utcnow()))
        else:
            conn.execute(update(m).where(m.c.key == key).values(value=value, updated_at=utcnow()))
        return rows

    def _deployments(
        self, conn: Connection, chain: str, s: RefreshSummary, touched: dict[str, str]
    ) -> None:
        t = schema.transactions
        # A stored transaction can be upgraded in place (partial -> complete) and gain its
        # created_contract while keeping its id, so an id cursor could pass it for good. Read a
        # rotating window instead: every deployment is revisited, and asserting is idempotent.
        rows = self._rotating(
            conn,
            chain,
            "deployments",
            t,
            s,
            t.c.chain == chain,
            t.c.created_contract.is_not(None),
            t.c.from_address.is_not(None),
            canonical_transaction(),
        )
        s.rows_read["deployments"] = len(rows)
        for r in rows:
            contract = self._entity(
                conn,
                chain,
                str(r["created_contract"]),
                "contract",
                r["block_time"],
                s,
                tx=r["tx_id"],
            )
            self._entity(conn, chain, str(r["from_address"]), "wallet", r["block_time"], s)
            outcome = self._assert(
                conn,
                contract,
                "deployed_by",
                {"deployer": r["from_address"]},
                str(r["from_address"]),
                "observed",
                "transactions",
                s,
                tx=r["tx_id"],
                chain=chain,
                observed_at=r["block_time"],
            )
            if outcome in ("new", "changed"):  # unchanged re-reads do not re-queue the deployer
                touched[str(r["from_address"])] = "wallet"

    def _transfers(
        self, conn: Connection, chain: str, s: RefreshSummary, touched: dict[str, str]
    ) -> None:
        tr = schema.transfers
        rows = self._rows(
            conn,
            chain,
            "transfers",
            tr,
            s,
            tr.c.chain == chain,
            tr.c.tx_id.not_in(orphaned_tx_ids(chain)),
        )
        s.rows_read["transfers"] = len(rows)
        for r in rows:
            for ref in (r["from_address"], r["to_address"]):
                if ref:
                    self._entity(conn, chain, str(ref), "wallet", r["block_time"], s)
                    touched.setdefault(str(ref), "wallet")
            if r["token"]:
                self._entity(conn, chain, str(r["token"]), "token", r["block_time"], s)
                touched[str(r["token"])] = "token"

    def _pools(
        self, conn: Connection, chain: str, s: RefreshSummary, touched: dict[str, str]
    ) -> None:
        pe = schema.pool_events
        rows = self._rows(conn, chain, "pools", pe, s, pe.c.chain == chain)
        s.rows_read["pools"] = len(rows)
        for r in rows:
            pool = self._entity(conn, chain, str(r["pool"]), "pool", r["block_time"], s)
            self._assert(
                conn,
                pool,
                "pool",
                {
                    "protocol": r["protocol"],
                    "factory": r["factory"],
                    "token0": r["token0"],
                    "token1": r["token1"],
                    "fee": r["fee"],
                },
                f"{r['token0']}|{r['token1']}",
                "observed",
                "pool_events",
                s,
                tx=r["tx_id"],
                chain=chain,
                observed_at=r["block_time"],
            )
            for token in (r["token0"], r["token1"]):
                self._entity(conn, chain, str(token), "token", r["block_time"], s)
                touched[str(token)] = "token"

    def _solana(
        self, conn: Connection, chain: str, s: RefreshSummary, touched: dict[str, str]
    ) -> None:
        sd = schema.solana_discoveries
        rows = self._rows(conn, chain, "solana_discoveries", sd, s)
        s.rows_read["solana_discoveries"] = len(rows)
        for r in rows:
            kind = SOLANA_KIND.get(str(r["kind"]), "program")
            ent = self._entity(conn, chain, str(r["ref"]), kind, r["block_time"], s)
            detail = json.loads(r["detail_json"] or "{}")
            self._assert(
                conn,
                ent,
                "announced",
                {"as": r["kind"], "slot": r["slot"], **detail},
                str(r["kind"]),
                "observed",
                "solana_discoveries",
                s,
                tx=r["signature"],
                chain=chain,
                observed_at=r["block_time"],
            )
            if kind == "token":
                touched[str(r["ref"])] = "token"

    def _signals(self, conn: Connection, chain: str, s: RefreshSummary) -> None:
        """New signals become facts on their subject; recently updated ones refresh their status."""
        sg = schema.signals
        rows = self._rows(conn, chain, "signals", sg, s, sg.c.chain == chain)
        recent = self._rotating(conn, chain, "signal_status", sg, s, sg.c.chain == chain)
        seen: dict[int, Any] = {int(r["id"]): r for r in [*rows, *recent]}
        s.rows_read["signals"] = len(rows)
        for r in seen.values():
            if r["subject_kind"] not in KIND_RANK:
                continue
            ent = self._entity(
                conn,
                chain,
                str(r["subject_ref"]),
                str(r["subject_kind"]),
                r["observed_at"],
                s,
                create=True,
            )
            detector = str(r["detector_id"])
            evidence_class = (
                "statistical"
                if detector in STATISTICAL_DETECTORS
                else "observed"
                if detector in OBSERVED_DETECTORS
                else "heuristic"
            )
            self._assert(
                conn,
                ent,
                f"signal:{r['id']}",
                {
                    "signal_id": r["id"],
                    "detector": detector,
                    "title": r["title"],
                    "score": r["score"],
                    "status": r["status"],
                },
                str(r["status"]),
                evidence_class,
                "signals",
                s,
                observed_at=r["observed_at"],
            )

    def _relationships(self, conn: Connection, chain: str, s: RefreshSummary) -> None:
        rel = schema.entity_relationships
        rows = self._rotating(
            conn, chain, "relationships", rel, s, rel.c.chain == chain, rel.c.kind == "funded_by"
        )
        s.rows_read["relationships"] = len(rows)
        for r in rows:
            ent = self._entity(conn, chain, str(r["source_ref"]), "wallet", r["first_seen_at"], s)
            key = f"funded_by:{r['target_ref']}"  # one fact per funder: several can be active
            if r["status"] != "active":
                self._outdate(conn, ent, key, s)
                continue
            self._assert(
                conn,
                ent,
                key,
                {"funder": r["target_ref"], "transfers": r["weight"], "note": r["note"]},
                # the basis is part of the signature, so a re-labelled link supersedes the old fact
                f"{r['target_ref']}|{r['basis']}",
                "observed" if r["basis"] == "observed" else "hypothesis",
                "entity_relationships",
                s,
                observed_at=r["first_seen_at"],
            )

    def _classifications(self, conn: Connection, chain: str, s: RefreshSummary) -> None:
        wc = schema.wallet_classifications
        rows = self._rows(conn, chain, "classifications", wc, s, wc.c.chain == chain)
        s.rows_read["classifications"] = len(rows)
        for r in rows:
            ent = self._entity(conn, chain, str(r["address"]), "wallet", None, s, create=True)
            payload = json.loads(r["payload_json"] or "{}")
            label = payload.get("label") or payload.get("top_label") or r["status"]
            self._assert(
                conn,
                ent,
                "classification",
                {
                    "label": label,
                    "status": r["status"],
                    "data_origin": r["data_origin"],
                    "feature_version": r["feature_version"],
                    "as_of": str(r["as_of"]),
                },
                f"{label}|{r['status']}",
                "statistical",
                "wallet_classifications",
                s,
                observed_at=r["as_of"],
            )

    def _labels(self, conn: Connection, chain: str, s: RefreshSummary) -> None:
        """Approved labels are reviewed facts. Labels no longer approved become outdated; an entity
        with approved labels of different categories gets a visible contradiction."""
        lb = schema.entity_labels
        rows = self._rotating(conn, chain, "labels", lb, s, lb.c.chain == chain)
        s.rows_read["labels"] = len(rows)
        approved: dict[str, list[Any]] = {}
        for ref in {str(r["ref"]) for r in rows}:  # conflicts need every approved label of a ref
            approved[ref] = list(
                conn.execute(
                    select(lb).where(
                        lb.c.chain == chain, lb.c.ref == ref, lb.c.status == "approved"
                    )
                )
                .mappings()
                .all()
            )
        for r in rows:
            ref = str(r["ref"])
            kind = str(r["entity_kind"]) if r["entity_kind"] in KIND_RANK else "wallet"
            ent = self._entity(conn, chain, ref, kind, None, s, create=r["status"] == "approved")
            if ent is None:
                continue
            key = f"label:{r['id']}"
            if r["status"] == "approved":
                self._assert(
                    conn,
                    ent,
                    key,
                    {
                        "category": r["category"],
                        "name": r["name"],
                        "label_id": r["id"],
                        "reviewed_by": r["reviewed_by"],
                    },
                    "approved",
                    "reviewed",
                    "entity_labels",
                    s,
                    observed_at=r["reviewed_at"],
                )
            else:
                self._outdate(conn, ent, key, s)
        for ref, labels in approved.items():
            categories = sorted({str(x["category"]) for x in labels})
            ent = self._entity(conn, chain, ref, "wallet", None, s, create=False)
            if ent is None:
                continue
            if len(categories) > 1:
                self._assert(
                    conn,
                    ent,
                    "label_conflict",
                    {
                        "categories": categories,
                        "labels": [
                            {"id": x["id"], "category": x["category"], "name": x["name"]}
                            for x in labels
                        ],
                    },
                    "|".join(categories),
                    "reviewed",
                    "entity_labels",
                    s,
                    change="contradiction",
                )
            else:
                self._outdate(conn, ent, "label_conflict", s)

    def _aggregates(self, conn: Connection, chain: str, ref: str, s: RefreshSummary) -> None:
        e = schema.entities
        row = conn.execute(
            select(e.c.id, e.c.kind).where(e.c.chain == chain, e.c.ref == ref)
        ).first()
        if row is None:
            return
        ent, kind = int(row.id), str(row.kind)  # the stored kind, not whatever queued it
        tr, t = schema.transfers, schema.transactions
        orphaned = orphaned_tx_ids(chain)
        if kind == "token":
            n = conn.execute(
                select(func.count()).where(
                    tr.c.chain == chain, tr.c.token == ref, tr.c.tx_id.not_in(orphaned)
                )
            ).scalar_one()
            self._assert(
                conn,
                ent,
                "token_activity",
                {"stored_transfers": n, "level": _level(n)},
                str(_level(n)),
                "observed",
                "transfers",
                s,
            )
            return
        out_n = conn.execute(
            select(func.count()).where(
                tr.c.chain == chain, tr.c.from_address == ref, tr.c.tx_id.not_in(orphaned)
            )
        ).scalar_one()
        in_n = conn.execute(
            select(func.count()).where(
                tr.c.chain == chain, tr.c.to_address == ref, tr.c.tx_id.not_in(orphaned)
            )
        ).scalar_one()
        peer_union = union(
            select(tr.c.to_address.label("peer")).where(
                tr.c.chain == chain,
                tr.c.from_address == ref,
                tr.c.tx_id.not_in(orphaned),
                tr.c.to_address.is_not(None),
            ),
            select(tr.c.from_address.label("peer")).where(
                tr.c.chain == chain,
                tr.c.to_address == ref,
                tr.c.tx_id.not_in(orphaned),
                tr.c.from_address.is_not(None),
            ),
        ).subquery()
        peers = conn.execute(select(func.count()).select_from(peer_union)).scalar_one()
        total = int(out_n) + int(in_n)
        self._assert(
            conn,
            ent,
            "activity",
            {
                "stored_transfers_out": out_n,
                "stored_transfers_in": in_n,
                "counterparties": peers,
                "level": _level(total),
            },
            str(_level(total)),
            "observed",
            "transfers",
            s,
        )
        deployed = conn.execute(
            select(func.count()).where(
                t.c.chain == chain,
                t.c.from_address == ref,
                t.c.created_contract.is_not(None),
                canonical_transaction(),
            )
        ).scalar_one()
        if deployed:
            self._assert(
                conn,
                ent,
                "role:deployer",
                {"contracts_deployed": deployed},
                str(deployed),
                "observed",
                "transactions",
                s,
            )

    # ------------------------------------------------------------------ primitives

    def _entity(
        self,
        conn: Connection,
        chain: str,
        ref: str,
        kind: str,
        seen_at: datetime | None,
        s: RefreshSummary,
        *,
        create: bool = True,
        tx: object = None,
    ) -> int | None:
        """The entity id for ``(chain, ref)``, created when allowed. Its kind only moves to a
        better-supported one (a wallet becomes a contract once a deployment is seen), and that
        change is itself recorded as a fact."""
        e = schema.entities
        now = utcnow()
        row = conn.execute(
            select(e.c.id, e.c.kind, e.c.first_seen_at, e.c.last_seen_at).where(
                e.c.chain == chain, e.c.ref == ref
            )
        ).first()
        if row is None:
            if not create:
                return None
            pk = conn.execute(
                insert(e).values(
                    chain=chain,
                    ref=ref,
                    kind=kind,
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                    created_at=now,
                    updated_at=now,
                )
            ).inserted_primary_key
            assert pk is not None  # noqa: S101
            new_id = int(pk[0])
            if kind != "wallet" and tx is not None:
                # created straight from transaction evidence (a contract seen in its own
                # deployment): record why, so a reorg of that transaction can withdraw it
                self._assert(
                    conn,
                    new_id,
                    "kind",
                    {"from": "wallet", "to": kind},
                    kind,
                    "observed",
                    "memory",
                    s,
                    tx=tx,
                    chain=chain,
                )
            return new_id
        values: dict[str, Any] = {}
        if seen_at is not None:
            if row.first_seen_at is None or seen_at < row.first_seen_at:
                values["first_seen_at"] = seen_at
            if row.last_seen_at is None or seen_at > row.last_seen_at:
                values["last_seen_at"] = seen_at
        if KIND_RANK.get(kind, 0) > KIND_RANK.get(str(row.kind), 0):
            values["kind"] = kind
            self._assert(
                conn,
                int(row.id),
                "kind",
                {"from": row.kind, "to": kind},
                kind,
                "observed",
                "memory",
                s,
                tx=tx,  # so a reorg of the evidence withdraws the upgrade
                chain=chain,
            )
        if values:
            values["updated_at"] = now
            conn.execute(update(e).where(e.c.id == row.id).values(**values))
        return int(row.id)

    def _assert(
        self,
        conn: Connection,
        entity_id: int | None,
        key: str,
        value: dict[str, Any],
        signature: str,
        evidence_class: str,
        source: str,
        s: RefreshSummary,
        *,
        tx: object = None,
        chain: str | None = None,
        observed_at: datetime | None = None,
        change: str = "changed",
    ) -> str:
        """Record ``value`` for ``key``: confirm when the current fact says the same, otherwise
        append a new fact that supersedes it (or stands beside it, for a contradiction)."""
        if entity_id is None:
            return "none"
        f = schema.entity_facts
        now = utcnow()
        current = conn.execute(
            select(f.c.id, f.c.value_json, f.c.source, f.c.source_tx)
            .where(f.c.entity_id == entity_id, f.c.fact_key == key, f.c.status == "current")
            .order_by(f.c.id.desc())
        ).first()
        if current is not None:
            old_sig = json.loads(current.value_json).get("signature")
            if old_sig == signature:
                if current.source == source and current.source_tx == (
                    None if tx is None else str(tx)
                ):
                    return "same"  # the same source said the same thing again: nothing new
                conn.execute(
                    update(f)
                    .where(f.c.id == current.id)
                    .values(confirmations=f.c.confirmations + 1, confirmed_at=now)
                )
                s.facts_confirmed += 1
                return "confirmed"
            conn.execute(update(f).where(f.c.id == current.id).values(status="superseded"))
        kind = "new" if current is None and change != "contradiction" else change
        conn.execute(
            insert(f).values(
                entity_id=entity_id,
                fact_key=key,
                value_json=json.dumps({**value, "signature": signature}, default=str),
                evidence_class=evidence_class,
                change=kind,
                source=source,
                source_chain=chain,
                source_tx=None if tx is None else str(tx),
                observed_at=observed_at,
                recorded_at=now,
                confirmed_at=now,
                confirmations=1,
                supersedes_id=None if current is None else current.id,
                status="current",
            )
        )
        if kind == "new":
            s.facts_new += 1
        elif kind == "contradiction":
            s.contradictions += 1
        else:
            s.facts_changed += 1
        return kind

    def _outdate(
        self, conn: Connection, entity_id: int | None, key: str, s: RefreshSummary
    ) -> None:
        if entity_id is None:
            return
        f = schema.entity_facts
        result = conn.execute(
            update(f)
            .where(f.c.entity_id == entity_id, f.c.fact_key == key, f.c.status == "current")
            .values(status="outdated")
        )
        s.facts_outdated += int(result.rowcount or 0)


def counterparties(repo: Repository, chain: str, ref: str, limit: int = 20) -> list[dict[str, Any]]:
    """Stored transfer counterparties of an address, with counts (canonical data only)."""
    tr = schema.transfers
    orphaned = orphaned_tx_ids(chain)
    out: dict[str, dict[str, Any]] = {}
    with repo.transaction() as conn:
        for direction, mine, theirs in (
            ("out", tr.c.from_address, tr.c.to_address),
            ("in", tr.c.to_address, tr.c.from_address),
        ):
            rows = conn.execute(
                select(theirs, func.count(), func.min(tr.c.block_time), func.max(tr.c.block_time))
                .where(
                    tr.c.chain == chain,
                    mine == ref,
                    theirs.is_not(None),
                    tr.c.tx_id.not_in(orphaned),
                )
                .group_by(theirs)
                .order_by(func.count().desc())
                .limit(limit)
            ).all()
            for peer, n, first, last in rows:
                entry = out.setdefault(
                    str(peer),
                    {"ref": str(peer), "out": 0, "in": 0, "first_at": first, "last_at": last},
                )
                entry[direction] = int(n)
                entry["first_at"] = (
                    min(x for x in (entry["first_at"], first) if x)
                    if (entry["first_at"] or first)
                    else None
                )
                entry["last_at"] = (
                    max(x for x in (entry["last_at"], last) if x)
                    if (entry["last_at"] or last)
                    else None
                )
    return sorted(out.values(), key=lambda x: -(x["out"] + x["in"]))[:limit]
