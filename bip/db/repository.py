"""Repository functions over the operational database.

Every function runs in its own transaction and maps database failures to typed errors:
:class:`DatabaseError` (unavailable, failed write: never silently ignored),
:class:`DuplicateRecordError`, :class:`RecordNotFoundError` and :class:`InvalidRecordError`.
All SQL is built with SQLAlchemy Core expressions, so values are always bound parameters.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import ColumnElement, Engine, Table, delete, func, insert, or_, select, update
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.sql.selectable import ScalarSelect

from bip.contracts.blockchain import AddressRef, get_chain
from bip.contracts.findings import EvidenceKind, Finding
from bip.contracts.provenance import Provenance
from bip.contracts.transactions import EvmTransaction, SolanaTransaction
from bip.contracts.transfers import TokenTransfer
from bip.db import records, schema
from bip.db.errors import (
    DatabaseError,
    DuplicateRecordError,
    InvalidRecordError,
    RecordNotFoundError,
)
from bip.db.types import utcnow

RECENT_LIMIT = 50


def canonical_address(chain: str, address: str) -> AddressRef:
    """Validate ``chain`` and ``address`` with the BIP contracts; raise InvalidRecordError."""
    try:
        get_chain(chain)
        return AddressRef(chain=chain, address=address)
    except (ValueError, ValidationError) as exc:
        message = _first_error(exc)
        raise InvalidRecordError(message) from None


def _first_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        err = exc.errors(include_input=False, include_url=False)[0]
        return str(err["msg"]).removeprefix("Value error, ")
    return str(exc)


class Repository:
    """Thin, explicit data-access layer. One instance per engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def engine(self) -> Engine:
        return self._engine

    @contextmanager
    def _tx(self) -> Generator[Connection]:
        try:
            with self._engine.begin() as conn:
                yield conn
        except (DuplicateRecordError, RecordNotFoundError, InvalidRecordError):
            raise
        except IntegrityError as exc:
            raise DuplicateRecordError("record violates a uniqueness or integrity rule") from exc
        except SQLAlchemyError as exc:
            raise DatabaseError(f"database operation failed: {type(exc).__name__}") from exc

    def _log(self, conn: Connection, event_type: str, summary: str) -> None:
        conn.execute(
            insert(schema.activity_log).values(
                event_type=event_type, summary=summary[:300], created_at=utcnow()
            )
        )

    # ------------------------------------------------------------------ watchlists

    def create_watchlist(self, name: str, description: str | None) -> records.Watchlist:
        now = utcnow()
        try:
            with self._tx() as conn:
                pk = conn.execute(
                    insert(schema.watchlists).values(
                        name=name, description=description, created_at=now, updated_at=now
                    )
                ).inserted_primary_key
                assert pk is not None  # noqa: S101 - SQLite always returns the rowid
                self._log(conn, "watchlist.created", f"Created watchlist '{name}'")
        except DuplicateRecordError:
            raise DuplicateRecordError(f"a watchlist named '{name}' already exists") from None
        return self.get_watchlist(int(pk[0]))

    def list_watchlists(self) -> list[records.WatchlistSummary]:
        w, e = schema.watchlists, schema.watchlist_entries
        count = select(func.count()).where(e.c.watchlist_id == w.c.id).scalar_subquery()
        with self._tx() as conn:
            rows = (
                conn.execute(select(w, count.label("entry_count")).order_by(w.c.name))
                .mappings()
                .all()
            )
        return [records.WatchlistSummary.model_validate(dict(r)) for r in rows]

    def get_watchlist(self, watchlist_id: int) -> records.Watchlist:
        w, e = schema.watchlists, schema.watchlist_entries
        with self._tx() as conn:
            row = conn.execute(select(w).where(w.c.id == watchlist_id)).mappings().first()
            if row is None:
                raise RecordNotFoundError(f"watchlist {watchlist_id} not found")
            entries = (
                conn.execute(
                    select(e.c.id, e.c.chain, e.c.address, e.c.note, e.c.added_at)
                    .where(e.c.watchlist_id == watchlist_id)
                    .order_by(e.c.added_at, e.c.id)
                )
                .mappings()
                .all()
            )
        entry_models = tuple(records.WatchlistEntry.model_validate(dict(x)) for x in entries)
        return records.Watchlist(**dict(row), entry_count=len(entry_models), entries=entry_models)

    def delete_watchlist(self, watchlist_id: int) -> None:
        with self._tx() as conn:
            name = conn.execute(
                select(schema.watchlists.c.name).where(schema.watchlists.c.id == watchlist_id)
            ).scalar()
            if name is None:
                raise RecordNotFoundError(f"watchlist {watchlist_id} not found")
            conn.execute(delete(schema.watchlists).where(schema.watchlists.c.id == watchlist_id))
            self._log(conn, "watchlist.deleted", f"Deleted watchlist '{name}'")

    def add_watchlist_entry(
        self, watchlist_id: int, chain: str, address: str, note: str | None
    ) -> records.WatchlistEntry:
        ref = canonical_address(chain, address)
        e = schema.watchlist_entries
        now = utcnow()
        try:
            with self._tx() as conn:
                name = conn.execute(
                    select(schema.watchlists.c.name).where(schema.watchlists.c.id == watchlist_id)
                ).scalar()
                if name is None:
                    raise RecordNotFoundError(f"watchlist {watchlist_id} not found")
                pk = conn.execute(
                    insert(e).values(
                        watchlist_id=watchlist_id,
                        chain=ref.chain,
                        address=ref.address,
                        note=note,
                        added_at=now,
                    )
                ).inserted_primary_key
                assert pk is not None  # noqa: S101
                conn.execute(
                    update(schema.watchlists)
                    .where(schema.watchlists.c.id == watchlist_id)
                    .values(updated_at=now)
                )
                self._log(
                    conn, "watchlist.entry_added", f"Added {ref.chain} {ref.address} to '{name}'"
                )
        except DuplicateRecordError:
            raise DuplicateRecordError(
                f"{ref.chain} address {ref.address} is already on this watchlist"
            ) from None
        return records.WatchlistEntry(
            id=int(pk[0]), chain=ref.chain, address=ref.address, note=note, added_at=now
        )

    def remove_watchlist_entry(self, watchlist_id: int, entry_id: int) -> None:
        e = schema.watchlist_entries
        with self._tx() as conn:
            row = conn.execute(
                select(e.c.chain, e.c.address).where(
                    e.c.id == entry_id, e.c.watchlist_id == watchlist_id
                )
            ).first()
            if row is None:
                raise RecordNotFoundError(f"entry {entry_id} not found on watchlist {watchlist_id}")
            conn.execute(delete(e).where(e.c.id == entry_id))
            conn.execute(
                update(schema.watchlists)
                .where(schema.watchlists.c.id == watchlist_id)
                .values(updated_at=utcnow())
            )
            self._log(
                conn,
                "watchlist.entry_removed",
                f"Removed {row.chain} {row.address} from watchlist {watchlist_id}",
            )

    # ------------------------------------------------------------------ investigations

    def create_investigation(
        self,
        title: str,
        description: str | None,
        subject: AddressRef | None,
    ) -> records.Investigation:
        now = utcnow()
        with self._tx() as conn:
            pk = conn.execute(
                insert(schema.investigations).values(
                    title=title,
                    description=description,
                    status="open",
                    subject_chain=subject.chain if subject else None,
                    subject_address=subject.address if subject else None,
                    created_at=now,
                    updated_at=now,
                )
            ).inserted_primary_key
            assert pk is not None  # noqa: S101
            self._log(conn, "investigation.created", f"Opened investigation '{title}'")
        return self.get_investigation(int(pk[0]))

    def _finding_count(self) -> ScalarSelect[int]:
        f, i = schema.findings, schema.investigations
        return select(func.count()).where(f.c.investigation_id == i.c.id).scalar_subquery()

    def list_investigations(
        self, status: records.InvestigationStatus | None = None
    ) -> list[records.InvestigationSummary]:
        i = schema.investigations
        query = select(i, self._finding_count().label("finding_count")).order_by(
            i.c.updated_at.desc(), i.c.id.desc()
        )
        if status is not None:
            query = query.where(i.c.status == status)
        with self._tx() as conn:
            rows = conn.execute(query).mappings().all()
        return [records.InvestigationSummary.model_validate(dict(r)) for r in rows]

    def get_investigation(self, investigation_id: int) -> records.Investigation:
        i = schema.investigations
        with self._tx() as conn:
            row = (
                conn.execute(
                    select(i, self._finding_count().label("finding_count")).where(
                        i.c.id == investigation_id
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise RecordNotFoundError(f"investigation {investigation_id} not found")
            found = self._finding_summaries(conn, investigation_id=investigation_id)
        return records.Investigation(**dict(row), findings=tuple(found))

    def update_investigation_status(
        self, investigation_id: int, status: records.InvestigationStatus
    ) -> records.Investigation:
        i = schema.investigations
        with self._tx() as conn:
            result = conn.execute(
                update(i)
                .where(i.c.id == investigation_id)
                .values(status=status, updated_at=utcnow())
            )
            if result.rowcount == 0:
                raise RecordNotFoundError(f"investigation {investigation_id} not found")
            self._log(
                conn,
                "investigation.status_changed",
                f"Investigation {investigation_id} set to {status}",
            )
        return self.get_investigation(investigation_id)

    def add_finding(
        self,
        investigation_id: int | None,
        finding: Finding,
        *,
        require_stored_verified_evidence: bool = True,
    ) -> records.FindingSummary:
        """Store a validated :class:`Finding` and its evidence references.

        With ``require_stored_verified_evidence`` (the default, and always on for the API),
        evidence that *claims* verified provenance must be a chain record whose transaction is
        stored in this database. Self-asserted verification is never trusted.
        """
        f, ev = schema.findings, schema.evidence_refs
        now = utcnow()
        try:
            with self._tx() as conn:
                if require_stored_verified_evidence:
                    self._check_verified_evidence(conn, finding)
                if investigation_id is not None:
                    exists = conn.execute(
                        select(schema.investigations.c.id).where(
                            schema.investigations.c.id == investigation_id
                        )
                    ).first()
                    if exists is None:
                        raise RecordNotFoundError(f"investigation {investigation_id} not found")
                pk = conn.execute(
                    insert(f).values(
                        investigation_id=investigation_id,
                        finding_id=finding.finding_id,
                        finding_type=finding.finding_type,
                        status=finding.status.value,
                        detected_at=finding.detected_at,
                        payload_json=finding.model_dump_json(),
                        created_at=now,
                    )
                ).inserted_primary_key
                assert pk is not None  # noqa: S101
                for ref in finding.evidence:
                    conn.execute(
                        insert(ev).values(
                            finding_pk=int(pk[0]),
                            evidence_id=ref.evidence_id,
                            kind=ref.kind.value,
                            locator=ref.locator,
                            verified=ref.is_verified,
                            provenance_json=ref.provenance.model_dump_json()
                            if ref.provenance
                            else None,
                        )
                    )
                if investigation_id is not None:
                    conn.execute(
                        update(schema.investigations)
                        .where(schema.investigations.c.id == investigation_id)
                        .values(updated_at=now)
                    )
                self._log(conn, "finding.recorded", f"Recorded finding {finding.finding_id}")
        except DuplicateRecordError:
            raise DuplicateRecordError(f"finding {finding.finding_id} already exists") from None
        return _summarize_finding(finding, investigation_id)

    def _check_verified_evidence(self, conn: Connection, finding: Finding) -> None:
        t, tr = schema.transactions, schema.transfers
        for ref in finding.evidence:
            if not ref.is_verified:
                continue
            prov = ref.provenance
            tx = prov.tx if prov is not None else None
            if (
                ref.kind is not EvidenceKind.CHAIN_RECORD
                or prov is None
                or tx is None
                or prov.chain is None
            ):
                raise InvalidRecordError(
                    f"evidence {ref.evidence_id} claims verified provenance, but only a stored "
                    "chain record with a transaction reference can be verified"
                )
            tx_id = tx.tx_hash if tx.kind == "evm_tx_hash" else tx.signature
            stored = (
                conn.execute(
                    select(t.c.id).where(t.c.chain == prov.chain, t.c.tx_id == tx_id)
                ).first()
                or conn.execute(
                    select(tr.c.id).where(tr.c.chain == prov.chain, tr.c.tx_id == tx_id)
                ).first()
            )
            if stored is None:
                raise InvalidRecordError(
                    f"evidence {ref.evidence_id} claims verified provenance for {prov.chain} "
                    f"transaction {tx_id}, which is not a stored chain record"
                )

    def _finding_summaries(
        self, conn: Connection, *, investigation_id: int | None = None, limit: int = 100
    ) -> list[records.FindingSummary]:
        f = schema.findings
        query = select(f.c.payload_json, f.c.investigation_id).order_by(
            f.c.detected_at.desc(), f.c.id.desc()
        )
        if investigation_id is not None:
            query = query.where(f.c.investigation_id == investigation_id)
        rows = conn.execute(query.limit(limit)).all()
        return [
            _summarize_finding(Finding.model_validate_json(r.payload_json), r.investigation_id)
            for r in rows
        ]

    def recent_findings(self, limit: int = 5) -> list[records.FindingSummary]:
        with self._tx() as conn:
            return self._finding_summaries(conn, limit=limit)

    # ------------------------------------------------------------------ chain data

    def store_transaction(self, tx: EvmTransaction | SolanaTransaction) -> bool:
        """Insert a validated transaction; return False if it was already stored.

        Not exposed through the API: chain data arrives only from ingestion (later stages).
        """
        block_number: int | None
        slot: int | None
        to_address: str | None
        if isinstance(tx, EvmTransaction):
            tx_id, block_number, slot = tx.tx_hash, tx.block_number, None
            block_time = tx.provenance.event_time
            from_address, to_address = tx.from_address, tx.to_address
            parties = [a for a in (tx.from_address, tx.to_address) if a]
        else:
            tx_id, block_number, slot = tx.signature, None, tx.slot
            block_time = tx.block_time
            from_address, to_address = tx.fee_payer, None
            parties = [tx.fee_payer]
        t = schema.transactions
        with self._tx() as conn:
            exists = conn.execute(
                select(t.c.id).where(t.c.chain == tx.chain, t.c.tx_id == tx_id)
            ).first()
            if exists is not None:
                return False
            conn.execute(
                insert(t).values(
                    chain=tx.chain,
                    status=tx.status.value if tx.status else None,
                    provenance_json=tx.provenance.model_dump_json(),
                    stored_at=utcnow(),
                    tx_id=tx_id,
                    block_number=block_number,
                    slot=slot,
                    block_time=block_time,
                    from_address=from_address,
                    to_address=to_address,
                )
            )
            observed = block_time or tx.provenance.event_time
            for address in parties:
                self._touch_wallet(conn, tx.chain, address, observed, tx.provenance)
        return True

    def store_transfer(self, transfer: TokenTransfer) -> bool:
        """Insert a validated transfer; return False if it was already stored."""
        tx_id = transfer.tx.tx_hash if transfer.tx.kind == "evm_tx_hash" else transfer.tx.signature
        if transfer.instruction_path is not None:
            event_key = "ix:" + ".".join(str(i) for i in transfer.instruction_path)
        elif transfer.log_index is not None:
            event_key = f"log:{transfer.log_index}"
        else:
            event_key = "native"
        position = transfer.position
        t = schema.transfers
        with self._tx() as conn:
            exists = conn.execute(
                select(t.c.id).where(
                    t.c.chain == transfer.chain, t.c.tx_id == tx_id, t.c.event_key == event_key
                )
            ).first()
            if exists is not None:
                return False
            conn.execute(
                insert(t).values(
                    chain=transfer.chain,
                    tx_id=tx_id,
                    event_key=event_key,
                    block_number=position.block_number if position.kind == "block" else None,
                    slot=position.slot if position.kind == "slot" else None,
                    standard=transfer.standard.value,
                    token=transfer.token.address if transfer.token else None,
                    from_address=transfer.from_address.address if transfer.from_address else None,
                    to_address=transfer.to_address.address if transfer.to_address else None,
                    amount_raw=str(transfer.amount_raw),
                    decimals=transfer.decimals,
                    token_id=str(transfer.token_id) if transfer.token_id is not None else None,
                    classification=transfer.classification.value,
                    provenance_json=transfer.provenance.model_dump_json(),
                    stored_at=utcnow(),
                )
            )
            for ref in (transfer.from_address, transfer.to_address):
                if ref is not None:
                    self._touch_wallet(
                        conn,
                        transfer.chain,
                        ref.address,
                        transfer.provenance.event_time,
                        transfer.provenance,
                    )
        return True

    def _touch_wallet(
        self,
        conn: Connection,
        chain: str,
        address: str,
        observed: datetime | None,
        provenance: Provenance,
    ) -> None:
        w = schema.wallets
        row = conn.execute(
            select(w.c.id, w.c.first_observed_at, w.c.last_observed_at).where(
                w.c.chain == chain, w.c.address == address
            )
        ).first()
        if row is None:
            conn.execute(
                insert(w).values(
                    chain=chain,
                    address=address,
                    first_observed_at=observed,
                    last_observed_at=observed,
                    first_provenance_json=provenance.model_dump_json(),
                    created_at=utcnow(),
                )
            )
            return
        if observed is None:
            return
        first = (
            row.first_observed_at
            if row.first_observed_at and row.first_observed_at <= observed
            else observed
        )
        last = (
            row.last_observed_at
            if row.last_observed_at and row.last_observed_at >= observed
            else observed
        )
        conn.execute(
            update(w).where(w.c.id == row.id).values(first_observed_at=first, last_observed_at=last)
        )

    # ------------------------------------------------------------------ wallets

    def _wallet_counts(self, conn: Connection, chain: str, address: str) -> tuple[int, int]:
        t, tr = schema.transactions, schema.transfers
        tx_count = conn.execute(
            select(func.count()).where(
                t.c.chain == chain, or_(t.c.from_address == address, t.c.to_address == address)
            )
        ).scalar_one()
        transfer_count = conn.execute(
            select(func.count()).where(
                tr.c.chain == chain, or_(tr.c.from_address == address, tr.c.to_address == address)
            )
        ).scalar_one()
        return int(tx_count), int(transfer_count)

    def list_wallets(self, limit: int, offset: int) -> tuple[list[records.WalletSummary], int]:
        w = schema.wallets
        with self._tx() as conn:
            total = int(conn.execute(select(func.count()).select_from(w)).scalar_one())
            rows = (
                conn.execute(
                    select(w.c.chain, w.c.address, w.c.first_observed_at, w.c.last_observed_at)
                    .order_by(w.c.last_observed_at.desc(), w.c.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
                .mappings()
                .all()
            )
            out: list[records.WalletSummary] = []
            for r in rows:
                txc, trc = self._wallet_counts(conn, r["chain"], r["address"])
                out.append(
                    records.WalletSummary(**dict(r), transaction_count=txc, transfer_count=trc)
                )
        return out, total

    def get_wallet(self, chain: str, address: str) -> records.WalletProfile:
        ref = canonical_address(chain, address)
        w, t, tr = schema.wallets, schema.transactions, schema.transfers
        with self._tx() as conn:
            row = conn.execute(
                select(w).where(w.c.chain == ref.chain, w.c.address == ref.address)
            ).first()
            if row is None:
                raise RecordNotFoundError(
                    f"no data is stored for {ref.chain} address {ref.address}"
                )
            txc, trc = self._wallet_counts(conn, ref.chain, ref.address)
            party = or_(t.c.from_address == ref.address, t.c.to_address == ref.address)
            txs = (
                conn.execute(
                    select(t)
                    .where(t.c.chain == ref.chain, party)
                    .order_by(t.c.block_number.desc(), t.c.slot.desc(), t.c.id.desc())
                    .limit(RECENT_LIMIT)
                )
                .mappings()
                .all()
            )
            tparty = or_(tr.c.from_address == ref.address, tr.c.to_address == ref.address)
            trs = (
                conn.execute(
                    select(tr)
                    .where(tr.c.chain == ref.chain, tparty)
                    .order_by(tr.c.block_number.desc(), tr.c.slot.desc(), tr.c.id.desc())
                    .limit(RECENT_LIMIT)
                )
                .mappings()
                .all()
            )
        first = Provenance.model_validate_json(row.first_provenance_json)
        return records.WalletProfile(
            chain=ref.chain,
            address=ref.address,
            first_observed_at=row.first_observed_at,
            last_observed_at=row.last_observed_at,
            transaction_count=txc,
            transfer_count=trc,
            first_seen_source=first.source_provider,
            first_seen_ingestion=first.ingestion_method.value,
            first_seen_verification=first.verification_status.value,
            recent_transactions=tuple(_tx_record(x) for x in txs),
            recent_transfers=tuple(_transfer_record(x) for x in trs),
        )

    # ------------------------------------------------------------------ summaries

    def counts(self) -> dict[str, int]:
        s = schema
        with self._tx() as conn:

            def n(table: Table, *where: ColumnElement[bool]) -> int:
                q = select(func.count()).select_from(table)
                if where:
                    q = q.where(*where)
                return int(conn.execute(q).scalar_one())

            return {
                "wallets_stored": n(s.wallets),
                "watchlisted_addresses": int(
                    conn.execute(
                        select(func.count()).select_from(
                            select(s.watchlist_entries.c.chain, s.watchlist_entries.c.address)
                            .distinct()
                            .subquery()
                        )
                    ).scalar_one()
                ),
                "transactions_stored": n(s.transactions),
                "transfers_stored": n(s.transfers),
                "watchlists": n(s.watchlists),
                "open_investigations": n(s.investigations, s.investigations.c.status != "closed"),
                "findings_stored": n(s.findings),
            }

    def chain_data_stats(self, chain: str) -> dict[str, int | None]:
        t = schema.transactions
        with self._tx() as conn:
            row = conn.execute(
                select(func.count(), func.max(t.c.block_number), func.max(t.c.slot)).where(
                    t.c.chain == chain
                )
            ).one()
        return {"transactions": int(row[0]), "latest_block": row[1], "latest_slot": row[2]}

    def provider_checks(self) -> dict[str, tuple[datetime | None, datetime | None, str | None]]:
        p = schema.provider_checks
        with self._tx() as conn:
            rows = conn.execute(select(p)).all()
        return {r.provider_id: (r.last_check_at, r.last_success_at, r.last_outcome) for r in rows}

    def recent_activity(self, limit: int = 10) -> list[records.ActivityEvent]:
        a = schema.activity_log
        with self._tx() as conn:
            rows = (
                conn.execute(
                    select(a.c.event_type, a.c.summary, a.c.created_at)
                    .order_by(a.c.created_at.desc(), a.c.id.desc())
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        return [records.ActivityEvent.model_validate(dict(r)) for r in rows]


def _summarize_finding(finding: Finding, investigation_id: int | None) -> records.FindingSummary:
    return records.FindingSummary(
        finding_id=finding.finding_id,
        finding_type=finding.finding_type,
        status=finding.status.value,
        detected_at=finding.detected_at,
        investigation_id=investigation_id,
        claim_count=len(finding.claims),
        evidence_count=len(finding.evidence),
    )


def _tx_record(r: RowMapping) -> records.TransactionRecord:
    prov = Provenance.model_validate_json(r["provenance_json"])
    return records.TransactionRecord(
        chain=r["chain"],
        tx_id=r["tx_id"],
        block_number=r["block_number"],
        slot=r["slot"],
        block_time=r["block_time"],
        from_address=r["from_address"],
        to_address=r["to_address"],
        status=r["status"],
        source_provider=prov.source_provider,
        verification_status=prov.verification_status.value,
        finality=prov.finality.value,
    )


def _transfer_record(r: RowMapping) -> records.TransferRecord:
    prov = Provenance.model_validate_json(r["provenance_json"])
    return records.TransferRecord(
        chain=r["chain"],
        tx_id=r["tx_id"],
        event_key=r["event_key"],
        block_number=r["block_number"],
        slot=r["slot"],
        standard=r["standard"],
        token=r["token"],
        from_address=r["from_address"],
        to_address=r["to_address"],
        amount_raw=r["amount_raw"],
        decimals=r["decimals"],
        classification=r["classification"],
        source_provider=prov.source_provider,
        verification_status=prov.verification_status.value,
    )
