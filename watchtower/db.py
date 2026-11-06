from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from . import config


class Base(DeclarativeBase):
    pass


class ImmutableObservation(RuntimeError):
    pass


class Database:
    def __init__(self, url: str | None = None):
        url = url or config.DB_URL
        kwargs = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url:
                kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _pragmas(dbapi, _):
                dbapi.execute("PRAGMA foreign_keys=ON")
                dbapi.execute("PRAGMA journal_mode=WAL")
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

        @event.listens_for(self.Session, "before_flush")
        def _observations_are_immutable(session, flush_context, instances):
            """Price history is evidence: an observation is only ever INSERTED. A changed price is a new observation."""
            from .models import Observation
            if any(isinstance(o, Observation) for o in session.deleted) or any(
                    isinstance(o, Observation) and session.is_modified(o) for o in session.dirty):
                raise ImmutableObservation("observations are append-only: record a new observation instead of changing history")

    def init(self):
        from . import models  # noqa: F401  (register tables)
        Base.metadata.create_all(self.engine)
        self.sync_columns()

    def sync_columns(self) -> list[str]:
        """Interim forward-only migration (until Alembic): ADD missing NULLABLE columns to existing tables.
        Never drops or alters anything. Returns the list of added 'table.column' names."""
        added = []
        insp = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if not insp.has_table(table.name):
                    continue
                have = {c["name"] for c in insp.get_columns(table.name)}
                for col in table.columns:
                    if col.name in have:
                        continue
                    if not col.nullable:
                        raise RuntimeError(f"cannot auto-add NOT NULL column {table.name}.{col.name}; write a migration")
                    ddl = col.type.compile(dialect=self.engine.dialect)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'))
                    added.append(f"{table.name}.{col.name}")
                    if col.index:  # sync_columns must also create the index the model declares
                        conn.execute(text(f'CREATE INDEX IF NOT EXISTS "ix_{table.name}_{col.name}" ON "{table.name}" ("{col.name}")'))
        return added

    @contextmanager
    def session(self):
        s = self.Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()


class SnapshotError(RuntimeError):
    pass


def snapshot(src, dst) -> dict:
    """Consistent, verified copy of a SQLite database, safe while the app or a crawl is using it.

    Uses SQLite's online backup API on a READ-ONLY connection, so committed pages that still live in the WAL file are
    included (a plain file copy of a WAL database silently drops them) and the source is never written. Refuses to
    overwrite `dst`. Verifies the copy (integrity_check + per-table row counts equal to the source) and returns
    {"path", "integrity", "tables": {name: rows}}; on any mismatch the partial copy is removed and SnapshotError raised."""
    import os
    import sqlite3
    from pathlib import Path

    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise SnapshotError(f"source database not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(dst, os.O_CREAT | os.O_EXCL | os.O_WRONLY))  # atomic "never overwrite"
    except FileExistsError:
        raise SnapshotError(f"refusing to overwrite existing file: {dst}") from None
    try:
        s = sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None)
        d = sqlite3.connect(dst)
        try:
            s.execute("BEGIN")  # one read transaction: the copy and the source counts see the same committed state,
            s.execute("SELECT count(*) FROM sqlite_master").fetchone()  # even while a crawl keeps writing
            s.backup(d)
            names = [n for (n,) in s.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            count = lambda c, n: c.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0]
            tables = {n: count(d, n) for n in names}
            integrity = d.execute("PRAGMA integrity_check").fetchone()[0]
            mismatch = [n for n in names if count(s, n) != tables[n]]
        finally:
            if s.in_transaction:
                s.execute("ROLLBACK")
            s.close()
            d.close()
        if integrity != "ok" or mismatch:
            raise SnapshotError(f"snapshot verification failed: integrity={integrity!r}, row-count mismatch in {mismatch}")
    except BaseException as ex:
        dst.unlink(missing_ok=True)  # only ever the file this call created
        if isinstance(ex, sqlite3.Error):
            raise SnapshotError(f"cannot read source database {src}: {ex}") from ex
        raise
    return {"path": str(dst), "integrity": integrity, "tables": tables}
