"""Operational persistence (SQLite in BIP 0.1) built on SQLAlchemy Core.

SQLAlchemy Core (not the ORM) keeps SQL explicit and parameterized and keeps the schema portable
to PostgreSQL later (ADR-002, ADR-015). Bulk chain data does not belong here: it goes to the
Parquet lake from Stage 5 onward. These tables hold entities of interest and application state.
"""
