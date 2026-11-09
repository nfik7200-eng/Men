"""Database location, engine creation and schema migration.

The database file always lives at ``<data_root>/operational/<filename>``, resolved through
:class:`bip.storage.paths.StorageRoot`, so it cannot escape the data root. Opening is idempotent:
repeated startups apply only missing migrations. A database whose schema is newer than this code
is refused rather than modified.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import URL, Engine, create_engine, event, insert, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from bip.config.settings import Settings
from bip.db import schema
from bip.db.errors import DatabaseError
from bip.db.types import utcnow
from bip.storage.paths import Layer, StorageRoot


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    description: str
    apply: Callable[[Connection], None]


_SQL_DIR = Path(__file__).with_name("sql")


def _run_sql_file(conn: Connection, name: str) -> None:
    body = (_SQL_DIR / name).read_text(encoding="utf-8")
    lines = [ln for ln in body.splitlines() if not ln.lstrip().startswith("--")]
    for statement in "\n".join(lines).split(";"):
        if statement.strip():
            conn.exec_driver_sql(statement)


def _initial_schema(conn: Connection) -> None:
    """v1: frozen DDL from BIP 0.1 (never edited)."""
    _run_sql_file(conn, "v1.sql")


_V2_COLUMNS: tuple[tuple[str, str], ...] = (
    ("transactions", "network VARCHAR(32)"),
    ("transactions", "block_hash VARCHAR(100)"),
    ("transactions", "finality VARCHAR(16)"),
    ("transactions", "parse_status VARCHAR(16) NOT NULL DEFAULT 'complete'"),
    ("transactions", "value_raw VARCHAR(80)"),
    ("transactions", "fee_raw VARCHAR(80)"),
    ("transactions", "gas_used VARCHAR(80)"),
    ("transactions", "instruction_count INTEGER"),
    ("transactions", "unknown_instruction_count INTEGER"),
    ("transactions", "program_ids_json TEXT"),
    ("transactions", "raw_ref VARCHAR(100)"),
    ("transactions", "run_id INTEGER"),
    ("transfers", "observation_kind VARCHAR(40) NOT NULL DEFAULT 'unspecified'"),
    ("transfers", "parse_status VARCHAR(16) NOT NULL DEFAULT 'complete'"),
    ("transfers", "block_time VARCHAR(32)"),
    ("transfers", "raw_ref VARCHAR(100)"),
    ("provider_checks", "last_failure_at VARCHAR(32)"),
    ("provider_checks", "consecutive_failures INTEGER NOT NULL DEFAULT 0"),
    ("provider_checks", "last_latency_ms INTEGER"),
)


def _provider_registry_and_ingestion(conn: Connection) -> None:
    """v2: provider health, network observations, blocks, balance changes, raw payloads,
    ingestion runs/errors/cursors; new columns on transactions, transfers, provider_checks."""
    # Idempotent: SQLite auto-commits DDL under pysqlite, so a crash midway can leave some
    # columns/tables in place with the version still at 1. Each step checks before acting.
    for table, column_ddl in _V2_COLUMNS:
        column = column_ddl.split()[0]
        existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column_ddl}")
    for table in schema.V2_TABLES:
        table.create(conn, checkfirst=True)
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_transactions_chain_block_time "
        "ON transactions (chain, block_time)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_transfers_chain_tx_id ON transfers (chain, tx_id)"
    )


def _intelligence(conn: Connection) -> None:
    """v3: provider observations, evidence verifications, analysis runs, anomalies, leads.
    Additive only (new tables, one nullable column); idempotent like v2."""
    existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(transactions)")}
    if "created_contract" not in existing:
        conn.exec_driver_sql("ALTER TABLE transactions ADD COLUMN created_contract VARCHAR(64)")
    for table in schema.V3_TABLES:
        table.create(conn, checkfirst=True)


def _provider_probing(conn: Connection) -> None:
    """v4: probe runs, capability observations, reliability snapshots, consistency checks.
    Additive only (new tables); idempotent like v2/v3."""
    for table in schema.V4_TABLES:
        table.create(conn, checkfirst=True)


def _case_research_links(conn: Connection) -> None:
    """v6: investigation_research_runs (links only; runs and reports untouched). Additive."""
    for table in schema.V6_TABLES:
        table.create(conn, checkfirst=True)


def _ml_and_research(conn: Connection) -> None:
    """v5: features, labels, reviews, model registry, training/evaluation, classifications,
    research runs/plans/tool calls/claims/reports/model usage. Additive, idempotent."""
    for table in schema.V5_TABLES:
        table.create(conn, checkfirst=True)


def _signals_and_alerts(conn: Connection) -> None:
    """v7: signals, signal evidence, discovery runs, alert rules, alerts. Additive, idempotent."""
    for table in schema.V7_TABLES:
        table.create(conn, checkfirst=True)


def _auth_and_audit(conn: Connection) -> None:
    """v8: users, sessions, audit log. Additive, idempotent."""
    for table in schema.V8_TABLES:
        table.create(conn, checkfirst=True)


def _job_history(conn: Connection) -> None:
    """v9: scheduler job history. Additive, idempotent."""
    for table in schema.V9_TABLES:
        table.create(conn, checkfirst=True)


def _relationships_and_notes(conn: Connection) -> None:
    """v10: entity relationships, their evidence, investigation notes. Additive, idempotent."""
    for table in schema.V10_TABLES:
        table.create(conn, checkfirst=True)


def _pool_events(conn: Connection) -> None:
    """v11: pool-creation events. Additive, idempotent."""
    for table in schema.V11_TABLES:
        table.create(conn, checkfirst=True)


def _tokens_and_reserves(conn: Connection) -> None:
    """v12: token statistics and pool reserves. Additive, idempotent."""
    for table in schema.V12_TABLES:
        table.create(conn, checkfirst=True)


def _solana_discoveries(conn: Connection) -> None:
    """v13: Solana discoveries. Additive, idempotent."""
    for table in schema.V13_TABLES:
        table.create(conn, checkfirst=True)


def _entity_labels(conn: Connection) -> None:
    """v14: reviewed entity labels. Additive, idempotent."""
    for table in schema.V14_TABLES:
        table.create(conn, checkfirst=True)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "initial operational schema", _initial_schema),
    Migration(
        2, "provider registry state and read-only ingestion", _provider_registry_and_ingestion
    ),
    Migration(3, "verification and wallet intelligence", _intelligence),
    Migration(4, "provider capability probing and reliability", _provider_probing),
    Migration(5, "ML wallet classification and AI research", _ml_and_research),
    Migration(6, "research runs linked to cases", _case_research_links),
    Migration(7, "signals, discovery runs and alerts", _signals_and_alerts),
    Migration(8, "users, sessions and audit log", _auth_and_audit),
    Migration(9, "background job history", _job_history),
    Migration(10, "entity relationships and investigation notes", _relationships_and_notes),
    Migration(11, "liquidity pool creation events", _pool_events),
    Migration(12, "token statistics and pool reserves", _tokens_and_reserves),
    Migration(13, "Solana discoveries", _solana_discoveries),
    Migration(14, "reviewed entity labels", _entity_labels),
)
assert MIGRATIONS[-1].version == schema.SCHEMA_VERSION  # noqa: S101 - import-time invariant


def database_path(settings: Settings) -> Path:
    """Resolved path of the SQLite file (inside the data root)."""
    root = StorageRoot(settings.resolved_data_root)
    return root.layer_path(Layer.OPERATIONAL, settings.database.filename)


def _configure_sqlite(engine: Engine, busy_timeout_ms: int) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: sqlite3.Connection, _record: object) -> None:  # pyright: ignore[reportUnusedFunction]
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        cursor.close()


def create_db_engine(settings: Settings, *, create_root: bool = True) -> Engine:
    """Create the engine for the configured database file and apply migrations."""
    root = StorageRoot(settings.resolved_data_root)
    if create_root:
        root.create()
    path = database_path(settings)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DatabaseError(f"cannot create database directory: {exc.strerror}") from None
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(path)))
    _configure_sqlite(engine, settings.database.busy_timeout_ms)
    migrate(engine)
    return engine


def current_version(conn: Connection) -> int:
    has_table = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'")
    ).first()
    if has_table is None:
        return 0
    value = conn.execute(
        select(schema.schema_migrations.c.version).order_by(
            schema.schema_migrations.c.version.desc()
        )
    ).scalar()
    return int(value) if value is not None else 0


def migrate(engine: Engine) -> int:
    """Apply missing migrations in one transaction each; return the resulting version."""
    try:
        with engine.begin() as conn:
            version = current_version(conn)
        if version > schema.SCHEMA_VERSION:
            raise DatabaseError(
                f"database schema version {version} is newer than this code "
                f"({schema.SCHEMA_VERSION}); refusing to open"
            )
        for migration in MIGRATIONS:
            if migration.version <= version:
                continue
            with engine.begin() as conn:
                migration.apply(conn)
                conn.execute(
                    insert(schema.schema_migrations).values(
                        version=migration.version,
                        applied_at=utcnow(),
                        description=migration.description,
                    )
                )
            version = migration.version
        return version
    except SQLAlchemyError as exc:
        raise DatabaseError(f"database migration failed: {type(exc).__name__}") from exc


def check_connectivity(engine: Engine) -> bool:
    """True if a trivial query succeeds. Never raises."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False
