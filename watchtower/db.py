from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from . import config


class Base(DeclarativeBase):
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
