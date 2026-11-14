"""Request-scoped dependencies backed by ``app.state``."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from bip.config.settings import Settings
from bip.db.repository import Repository
from bip.discovery.expansion import ExpansionEngine
from bip.discovery.review import ReviewService
from bip.discovery.service import DiscoveryService
from bip.entities.cases import CaseService
from bip.entities.labels import EntityLabelService
from bip.entities.relationships import RelationshipService
from bip.ingest.backfill import Backfill, GapRecovery
from bip.ingest.collector import ChainCollector
from bip.ingest.solana_collector import SolanaCollector
from bip.ingest.wallet import WalletIngestor
from bip.intel.memory import MemoryService
from bip.intel.service import IntelligenceService
from bip.intel.verify import Corroborator
from bip.ml.labels import LabelStore
from bip.ml.service import ClassificationService
from bip.ml.signal_ranking import SignalRanking
from bip.ml.training import Trainer
from bip.ops.backup import BackupService
from bip.ops.metrics import ApiMetrics
from bip.ops.scheduler import Scheduler
from bip.providers.intelligence import ProbeStore
from bip.providers.prober import Prober
from bip.providers.registry import Registry
from bip.providers.state import ProviderStateStore
from bip.research.engine import ResearchManager
from bip.router.router import ProviderRouter
from bip.security.auth import AuthService
from bip.storage.disk_guard import DiskGuard

WRITE_RESERVE_BYTES = 64 * 1024
"""Headroom each write request must have under the disk guard."""


INGEST_RESERVE_BYTES = 8 * 1024 * 1024
"""Headroom an ingestion run must have (raw payloads plus normalized rows)."""


@dataclass(slots=True)
class AppContext:
    settings: Settings
    repo: Repository
    disk_guard: DiskGuard
    registry: Registry
    provider_state: ProviderStateStore
    router: ProviderRouter
    ingestor: WalletIngestor
    ingest_lock: threading.Lock
    intel: IntelligenceService
    corroborator: Corroborator
    analysis_lock: threading.Lock
    verify_lock: threading.Lock
    prober: Prober
    probe_store: ProbeStore
    probe_lock: threading.Lock
    classifier: ClassificationService
    labels: LabelStore
    trainer: Trainer
    research: ResearchManager
    train_lock: threading.Lock
    discovery: DiscoveryService
    discovery_lock: threading.Lock
    auth: AuthService
    metrics: ApiMetrics
    backups: BackupService
    started_monotonic: float
    relationships: RelationshipService
    collector: ChainCollector
    solana_collector: SolanaCollector
    backfill: Backfill
    gap_recovery: GapRecovery
    memory: MemoryService
    review: ReviewService
    ranking: SignalRanking
    expansion: ExpansionEngine
    entity_labels: EntityLabelService
    cases: CaseService
    scheduler: Scheduler | None


def get_context(request: Request) -> AppContext:
    ctx: AppContext = request.app.state.bip
    return ctx


Context = Annotated[AppContext, Depends(get_context)]


def require_write_capacity(ctx: Context) -> AppContext:
    """Raise StorageLimitError (-> 507) if the disk guard refuses a small write."""
    ctx.disk_guard.require(WRITE_RESERVE_BYTES)
    return ctx


WritableContext = Annotated[AppContext, Depends(require_write_capacity)]
