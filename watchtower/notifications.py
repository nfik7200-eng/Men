"""Local in-app notifications for watchlist events (no email, SMS or push).

Schema safety: this module has its OWN declarative base, so `Database.init()` (which runs at every app start)
never creates this table. An existing database changes only when someone runs, deliberately:

    python -m watchtower.cli notify-migrate  --db COPY            # dry run: prints the SQL, changes nothing
    python -m watchtower.cli notify-migrate  --db COPY --apply    # creates the table if missing
    python -m watchtower.cli notify-rollback --db COPY --apply    # drops it (refused while it holds rows, unless --discard-rows)

The live operational database is refused by the CLI; run migrations on a copy first (see docs/NOTIFICATIONS.md).
Until the table exists, the web routes answer 503 and say so; nothing else is affected.

Idempotence: a notification's identity (owner, dedupe_key) is derived from the EVENT: a target alert from the listing,
price and target; a restock alert from the observation that records the restock (the history is append-only). Re-running generation adds nothing new; a genuinely new event
(a lower price under the target, a new restock) adds one row.
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, inspect, select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.schema import CreateTable

from .money import fmt_money
from .models import Observation, utcnow

TABLE = "notifications"


class NoteBase(DeclarativeBase):
    """Separate metadata on purpose: Base.metadata.create_all() never sees this table."""


class Notification(NoteBase):
    __tablename__ = TABLE
    __table_args__ = (UniqueConstraint("owner", "dedupe_key", name="uq_notification_owner_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String(100), index=True)
    listing_id: Mapped[int] = mapped_column(Integer)  # a plain id, no foreign key: the history stays readable
    kind: Mapped[str] = mapped_column(String(32))  # target_reached | back_in_stock
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    dedupe_key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)


# ---------------------------------------------------------------- schema management (explicit only)
def is_migrated(engine) -> bool:
    return inspect(engine).has_table(TABLE)


def migration_sql(engine) -> str:
    return str(CreateTable(NoteBase.metadata.tables[TABLE]).compile(engine)).strip() + ";"


def migrate(engine, apply: bool) -> str:
    """Dry run unless apply=True. Idempotent: an existing table is left alone."""
    if not apply:
        return "DRY RUN (nothing changed). Would run:\n" + migration_sql(engine)
    if is_migrated(engine):
        return "already present: nothing to do"
    NoteBase.metadata.create_all(engine, tables=[NoteBase.metadata.tables[TABLE]])
    return "created table notifications"


def rollback(engine, apply: bool, discard_rows: bool = False) -> str:
    """Drops the table. Refuses while it holds notifications unless discard_rows=True (the rows are user history)."""
    if not is_migrated(engine):
        return "not present: nothing to roll back"
    with engine.connect() as c:
        rows = c.exec_driver_sql(f"SELECT count(*) FROM {TABLE}").scalar()
    if not apply:
        return f"DRY RUN (nothing changed). Table holds {rows} row(s). Would run: DROP TABLE {TABLE};"
    if rows and not discard_rows:
        raise RuntimeError(f"refusing to drop {TABLE}: it holds {rows} notification row(s); pass --discard-rows to delete them")
    with engine.begin() as c:
        c.exec_driver_sql(f"DROP TABLE {TABLE}")
    return f"dropped table {TABLE} ({rows} row(s) discarded)"


def available(s: Session) -> bool:
    return inspect(s.get_bind()).has_table(TABLE)


# ---------------------------------------------------------------- generation (idempotent)
def generate(s: Session, owner: str) -> int:
    """One notification per watch EVENT. Returns the number of NEW rows (0 when everything was already recorded)."""
    from watchtower import monitor  # local import: monitor has no notification dependency
    created = 0
    for st in monitor.evaluate(s, owner):
        w, l = st.watch, st.watch.listing
        if not l.active:
            continue  # no longer found at the source: the last price seen is not a current price, so no alert
        if w.baseline_currency and l.currency != w.baseline_currency:
            continue  # the monitor pauses comparisons after a currency change: no alert without a sourced FX rate
        if w.target_price_minor and l.price_minor is not None and l.price_minor <= w.target_price_minor:
            created += _add(s, owner, l.id, "target_reached", f"{l.title}: at or below your target",
                            f"{fmt_money(l.price_minor, l.currency)} (target {fmt_money(w.target_price_minor, l.currency)}) at {l.source.key}.",
                            f"target:{l.id}:{l.price_minor}:{w.target_price_minor}")
        if w.notify_restock:
            for obs in _restock_transitions(s, l.id, since=w.created_at):
                created += _add(s, owner, l.id, "back_in_stock", f"{l.title}: back in stock",
                                f"Observed in stock at {l.source.key} on {obs.observed_at:%Y-%m-%d %H:%M} UTC.",
                                f"restock:{obs.id}")  # one alert per real restock: keyed on the transition observation
    return created


def _restock_transitions(s: Session, listing_id: int, since: datetime) -> list[Observation]:
    """Observations that mark a restock: in stock, directly after an out-of-stock observation, observed at or after `since`.
    The collection history is append-only, so every real restock is a distinct, permanent event."""
    obs = list(s.scalars(select(Observation).where(Observation.listing_id == listing_id)
                         .order_by(Observation.observed_at, Observation.id)))
    return [cur for prev, cur in zip(obs, obs[1:])
            if prev.availability == "out_of_stock" and cur.availability == "in_stock" and cur.observed_at >= since]


def _add(s: Session, owner: str, listing_id: int, kind: str, title: str, body: str, key: str) -> int:
    exists = s.scalar(select(Notification.id).where(Notification.owner == owner, Notification.dedupe_key == key))
    if exists is not None:
        return 0
    try:
        with s.begin_nested():  # a savepoint: a race on the unique key must not poison the outer transaction
            s.add(Notification(owner=owner, listing_id=listing_id, kind=kind, title=title[:300], body=body, dedupe_key=key[:200]))
            s.flush()
        return 1
    except IntegrityError:
        return 0


# ---------------------------------------------------------------- reading and read-state
def unread_count(s: Session, owner: str) -> int:
    return s.scalar(select(func.count()).select_from(Notification).where(Notification.owner == owner, Notification.read_at.is_(None))) or 0


def list_for(s: Session, owner: str, limit: int = 100) -> list[Notification]:
    return list(s.scalars(select(Notification).where(Notification.owner == owner)
                          .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)))


def list_page(s: Session, owner: str, limit: int = 100) -> tuple[list[Notification], int]:
    """The newest `limit` notifications for this owner, and the full unread count (which can exceed the rows listed)."""
    return list_for(s, owner, limit), unread_count(s, owner)


def mark_read(s: Session, owner: str, note_id: int) -> bool:
    n = s.scalar(select(Notification).where(Notification.id == note_id, Notification.owner == owner))
    if n is None:
        return False  # not yours, or does not exist: the same answer, so ids cannot be probed
    if n.read_at is None:
        n.read_at = utcnow()
    return True


def mark_all_read(s: Session, owner: str) -> int:
    rows = list(s.scalars(select(Notification).where(Notification.owner == owner, Notification.read_at.is_(None))))
    now = utcnow()
    for n in rows:
        n.read_at = now
    return len(rows)
