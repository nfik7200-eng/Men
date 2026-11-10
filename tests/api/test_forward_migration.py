"""A database frozen at an older schema version keeps its data when upgraded to the current one."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, insert, text
from sqlalchemy.engine import URL

from bip.config import load_settings
from bip.db import schema
from bip.db.engine import MIGRATIONS, _configure_sqlite, database_path, migrate  # pyright: ignore[reportPrivateUsage]
from bip.db.errors import DatabaseError
from bip.storage.paths import StorageRoot

NEW_TABLES = {
    "signals",
    "signal_evidence",
    "discovery_runs",
    "alert_rules",
    "alerts",
    "users",
    "sessions",
    "audit_log",
    "job_runs",
    "entity_relationships",
    "relationship_evidence",
    "investigation_notes",
    "pool_events",
    "token_stats",
    "pool_reserves",
    "solana_discoveries",
    "entity_labels",
}


def frozen_at(tmp_path: Path, version: int) -> Engine:
    settings = load_settings(data_root=tmp_path / "data", env="test")
    StorageRoot(settings.resolved_data_root).create()
    path = database_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(path)))
    _configure_sqlite(engine, settings.database.busy_timeout_ms)
    for migration in MIGRATIONS:
        if migration.version > version:
            break
        with engine.begin() as conn:
            migration.apply(conn)
            conn.execute(
                insert(schema.schema_migrations).values(
                    version=migration.version,
                    applied_at=datetime.now(UTC),
                    description=migration.description,
                )
            )
    return engine


def test_version_six_data_survives_the_upgrade(tmp_path: Path) -> None:
    engine = frozen_at(tmp_path, 6)
    try:
        with engine.begin() as conn:
            tables = set(
                conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars()
            )
            assert not NEW_TABLES & tables, "the v6 fixture must not already have the new tables"
            conn.execute(
                insert(schema.watchlists).values(
                    name="kept",
                    description="from v6",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            conn.execute(
                insert(schema.investigations).values(
                    title="old case",
                    status="open",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
        assert migrate(engine) == schema.SCHEMA_VERSION == 15
        with engine.connect() as conn:
            tables = set(
                conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars()
            )
            assert tables >= NEW_TABLES
            assert conn.execute(text("SELECT name FROM watchlists")).scalars().all() == ["kept"]
            assert conn.execute(text("SELECT title FROM investigations")).scalars().all() == [
                "old case"
            ]
            assert conn.exec_driver_sql("PRAGMA integrity_check").all() == [("ok",)]
            assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            versions = (
                conn.execute(text("SELECT version FROM schema_migrations ORDER BY version"))
                .scalars()
                .all()
            )
        assert versions == list(range(1, 16))
        assert migrate(engine) == 15  # idempotent
    finally:
        engine.dispose()


def test_an_interrupted_upgrade_resumes(tmp_path: Path) -> None:
    """Tables are created idempotently, so a crash between 'create' and 'record' is recoverable."""
    engine = frozen_at(tmp_path, 7)
    try:
        with engine.begin() as conn:  # v8's tables exist but the version row was never written
            MIGRATIONS[7].apply(conn)
        assert migrate(engine) == schema.SCHEMA_VERSION
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar_one() == 0
    finally:
        engine.dispose()


def test_a_newer_database_is_refused(tmp_path: Path) -> None:
    engine = frozen_at(tmp_path, schema.SCHEMA_VERSION)
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(schema.schema_migrations).values(
                    version=schema.SCHEMA_VERSION + 1,
                    applied_at=datetime.now(UTC),
                    description="future",
                )
            )
        with pytest.raises(DatabaseError, match="newer than this code"):
            migrate(engine)
    finally:
        engine.dispose()
