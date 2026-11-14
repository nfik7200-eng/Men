"""Exclusion of reorganised-away (orphaned) chain data from derived intelligence.

A transaction is *orphaned* when its block is no longer on the canonical chain. Orphaned rows stay
in the database for audit and are still listed with their ``orphaned`` label, but every query that
feeds analysis, scoring, graphs or classification must leave them out. Transfers and balance
changes carry no finality of their own, so they are excluded through their transaction.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, Select, or_, select

from bip.contracts.provenance import Finality
from bip.db import schema

ORPHANED = Finality.ORPHANED.value


def orphaned_tx_ids(chain: str) -> Select[tuple[str]]:
    """Subquery: ids of ``chain`` transactions whose block is no longer canonical."""
    t = schema.transactions
    return select(t.c.tx_id).where(t.c.chain == chain, t.c.finality == ORPHANED)


def canonical_transaction() -> ColumnElement[bool]:
    """Condition on ``transactions`` rows: not orphaned (a NULL finality counts as not orphaned)."""
    t = schema.transactions
    return or_(t.c.finality.is_(None), t.c.finality != ORPHANED)
