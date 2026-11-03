"""Request-scoped dependencies backed by ``app.state``."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from bip.config.settings import Settings
from bip.db.repository import Repository
from bip.ingest.wallet import WalletIngestor
from bip.providers.registry import Registry
from bip.providers.state import ProviderStateStore
from bip.router.router import ProviderRouter
from bip.storage.disk_guard import DiskGuard

WRITE_RESERVE_BYTES = 64 * 1024
"""Headroom each write request must have under the disk guard."""


INGEST_RESERVE_BYTES = 8 * 1024 * 1024
"""Headroom an ingestion run must have (raw payloads plus normalized rows)."""


@dataclass(frozen=True, slots=True)
class AppContext:
    settings: Settings
    repo: Repository
    disk_guard: DiskGuard
    registry: Registry
    provider_state: ProviderStateStore
    router: ProviderRouter
    ingestor: WalletIngestor
    ingest_lock: threading.Lock


def get_context(request: Request) -> AppContext:
    ctx: AppContext = request.app.state.bip
    return ctx


Context = Annotated[AppContext, Depends(get_context)]


def require_write_capacity(ctx: Context) -> AppContext:
    """Raise StorageLimitError (-> 507) if the disk guard refuses a small write."""
    ctx.disk_guard.require(WRITE_RESERVE_BYTES)
    return ctx


WritableContext = Annotated[AppContext, Depends(require_write_capacity)]
