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
    "ingestion_checkpoints",
    "coverage_ranges",
    "provider_usage_daily",
    "entities",
    "entity_facts",
    "signal_decisions",
    "discovery_examined",
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
        assert migrate(engine) == schema.SCHEMA_VERSION == 18
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
        assert versions == list(range(1, schema.SCHEMA_VERSION + 1))
        assert migrate(engine) == schema.SCHEMA_VERSION  # idempotent
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


def test_version_fifteen_collector_progress_is_seeded_into_checkpoints(tmp_path: Path) -> None:
    """v16 copies the collectors' app_metadata cursors and gaps; it never loses or fakes data."""
    import json

    engine = frozen_at(tmp_path, 15)
    now = datetime.now(UTC)
    legacy = {
        "collector:ethereum:last_block": "119",
        "collector:ethereum:last_block_hash": "0xabc",
        "collector:ethereum:gaps": json.dumps(
            [
                {"from_block": 100, "to_block": 100, "reason": "given up after 3 failed reads"},
                {
                    "from_block": 105,
                    "to_block": 105,
                    "reason": "2 qualifying transaction(s) not stored (receipt cap or call budget)",
                },
                {"from_block": "bad", "to_block": 1, "reason": "malformed entry is ignored"},
            ]
        ),
        "collector:solana:last_slot": "5000",
        "collector:solana:last_blockhash": "5000:HashSol",
        "collector:solana:gaps": json.dumps(
            [{"from_slot": 10, "to_slot": 20, "reason": "lag exceeded max_lag_slots"}]
        ),
    }
    try:
        with engine.begin() as conn:
            for key, value in legacy.items():
                conn.execute(
                    insert(schema.app_metadata).values(key=key, value=value, updated_at=now)
                )
        assert migrate(engine) == schema.SCHEMA_VERSION
        assert migrate(engine) == schema.SCHEMA_VERSION  # idempotent: a second pass adds nothing
        with engine.connect() as conn:
            cps = conn.execute(
                text(
                    "SELECT chain, network, stream, direction, position_kind, cursor, "
                    "cursor_hash, decoder_version FROM ingestion_checkpoints ORDER BY chain"
                )
            ).all()
            assert [tuple(r) for r in cps] == [
                ("ethereum", "mainnet", "collector", "live", "block", 119, None, "pre-v16"),
                ("solana", "mainnet-beta", "collector", "live", "slot", 5000, "HashSol", "pre-v16"),
            ]
            cov = conn.execute(
                text(
                    "SELECT chain, from_pos, to_pos, status FROM coverage_ranges "
                    "ORDER BY chain, from_pos"
                )
            ).all()
            assert [tuple(r) for r in cov] == [
                ("ethereum", 100, 100, "failed"),
                ("ethereum", 105, 105, "incomplete"),
                ("solana", 10, 20, "skipped"),
            ]
            # the legacy keys stay where the collectors read them
            assert (
                conn.execute(
                    text("SELECT value FROM app_metadata WHERE key='collector:ethereum:last_block'")
                ).scalar()
                == "119"
            )
    finally:
        engine.dispose()


def test_version_fifteen_without_collector_history_seeds_nothing(tmp_path: Path) -> None:
    engine = frozen_at(tmp_path, 15)
    try:
        assert migrate(engine) == schema.SCHEMA_VERSION
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM ingestion_checkpoints")).scalar() == 0
            assert conn.execute(text("SELECT COUNT(*) FROM coverage_ranges")).scalar() == 0
    finally:
        engine.dispose()


def test_v16_seeding_rejects_malformed_legacy_values_and_reruns_cleanly(tmp_path: Path) -> None:
    import json

    from bip.db.engine import _checkpoints_and_coverage  # pyright: ignore[reportPrivateUsage]

    engine = frozen_at(tmp_path, 15)
    now = datetime.now(UTC)
    legacy = {
        "collector:ethereum:last_block": "abc",  # malformed: no checkpoint is invented
        "collector:solana:last_slot": "700",
        "collector:solana:last_blockhash": "699:OlderHash",  # hash of another slot: not copied
        "collector:solana:gaps": json.dumps(
            [{"from_slot": 5, "to_slot": 6, "reason": "given up after 3 failed reads"}]
        ),
        "collector:ethereum:gaps": "not json",
    }
    try:
        with engine.begin() as conn:
            for key, value in legacy.items():
                conn.execute(
                    insert(schema.app_metadata).values(key=key, value=value, updated_at=now)
                )
        assert migrate(engine) == schema.SCHEMA_VERSION
        with engine.begin() as conn:  # the seeding step itself runs again (an interrupted retry)
            _checkpoints_and_coverage(conn)
        with engine.connect() as conn:
            cps = conn.execute(
                text("SELECT chain, cursor, cursor_hash FROM ingestion_checkpoints")
            ).all()
            assert [tuple(r) for r in cps] == [("solana", 700, None)]
            assert conn.execute(text("SELECT COUNT(*) FROM coverage_ranges")).scalar() == 1
    finally:
        engine.dispose()
