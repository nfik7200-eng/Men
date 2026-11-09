"""FastAPI application factory.

``create_app()`` builds the app from settings. On startup it opens (and migrates) the SQLite
database under the configured data root. Tests inject a temporary engine and disk guard.
"""

from __future__ import annotations

import threading
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine

from bip import __version__
from bip.api.deps import AppContext
from bip.api.errors import install_error_handlers
from bip.api.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from bip.api.protection import ProtectionMiddleware
from bip.api.routes import (
    auth,
    chains,
    dashboard,
    discovery,
    entities,
    health,
    ingestion,
    intelligence,
    investigations,
    ml,
    provider_intel,
    providers,
    research,
    settings_route,
    system,
    wallets,
    watchlists,
)
from bip.config.settings import Settings, load_settings
from bip.core.exceptions import ConfigurationError
from bip.core.logging import get_logger
from bip.db.engine import create_db_engine
from bip.db.errors import DatabaseError
from bip.db.repository import Repository
from bip.discovery.service import DiscoveryService
from bip.entities.cases import CaseService
from bip.entities.relationships import RelationshipService
from bip.ingest.collector import ChainCollector
from bip.ingest.solana_collector import SolanaCollector
from bip.ingest.wallet import WalletIngestor
from bip.intel.service import IntelligenceService
from bip.intel.verify import Corroborator
from bip.ml.labels import LabelStore
from bip.ml.service import ClassificationService
from bip.ml.training import Trainer
from bip.ops.backup import BackupService
from bip.ops.jobs import build_jobs
from bip.ops.metrics import ApiMetrics, MetricsMiddleware
from bip.ops.scheduler import Scheduler
from bip.providers.intelligence import ProbeStore
from bip.providers.prober import Prober
from bip.providers.registry import Registry, default_registry
from bip.providers.state import ProviderStateStore
from bip.research.engine import ResearchManager
from bip.research.llm import LLMProvider, build_provider
from bip.router.router import ProviderRouter
from bip.rpc.transport import HttpsTransport, Transport
from bip.security.auth import AuthService
from bip.storage.disk_guard import DiskGuard, guard_from_settings

API_PREFIX = "/api/v1"
log = get_logger("bip.api")

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def require_safe_exposure(cfg: Settings) -> None:
    """Refuse to start an unauthenticated API on a non-loopback address.

    ``auth.mode = disabled`` is the single-user local prototype. Binding it to a reachable
    address would publish every endpoint, including the ones that contact providers, to anyone
    who can connect.
    """
    if (
        cfg.api.host not in _LOOPBACK_HOSTS
        and cfg.auth.mode != "required"
        and not cfg.auth.allow_insecure_bind
    ):
        raise ConfigurationError(
            f"BIP_API__HOST={cfg.api.host!r} is reachable from other machines but authentication "
            "is off. Set BIP_AUTH__MODE=required (and create a user with python -m "
            "bip.tools.users), or bind to 127.0.0.1."
        )


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    disk_guard: DiskGuard | None = None,
    transport: Transport | None = None,
    registry: Registry | None = None,
    environ: Mapping[str, str] | None = None,
    rpc_sleep: Callable[[float], None] | None = None,
    data_origin: str | None = None,
    llm_provider: LLMProvider | None = None,
) -> FastAPI:
    """Build the app. Tests inject ``engine``, ``disk_guard``, ``transport`` and ``environ``;
    in normal use the transport is the real hardened HTTPS client."""
    cfg = settings if settings is not None else load_settings()
    require_safe_exposure(cfg)
    metrics = ApiMetrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        db = engine if engine is not None else create_db_engine(cfg)
        guard = disk_guard if disk_guard is not None else guard_from_settings(cfg)
        repo = Repository(db)
        if data_origin is not None:
            # e.g. "offline_fixture": stored with the data so every view can label it
            repo.set_meta("data_origin", data_origin)
        reg = registry if registry is not None else default_registry()
        state = ProviderStateStore(repo)
        router = ProviderRouter(
            reg,
            cfg,
            transport
            if transport is not None
            else HttpsTransport(use_env_proxy=cfg.rpc.use_env_proxy),
            state,
            environ=environ,
            sleep=rpc_sleep if rpc_sleep is not None else time.sleep,
        )
        app.state.bip = AppContext(
            settings=cfg,
            repo=repo,
            disk_guard=guard,
            registry=reg,
            provider_state=state,
            router=router,
            ingestor=WalletIngestor(router, repo, cfg),
            ingest_lock=threading.Lock(),
            intel=IntelligenceService(repo, cfg),
            corroborator=Corroborator(repo, reg, router),
            analysis_lock=threading.Lock(),
            verify_lock=threading.Lock(),
            prober=Prober(reg, router, cfg.probe, states=state.states),
            probe_store=ProbeStore(repo, reg),
            probe_lock=threading.Lock(),
            classifier=ClassificationService(repo, cfg),
            labels=LabelStore(repo, min_labelled_wallets=cfg.ml.min_labelled_wallets),
            trainer=Trainer(repo, cfg),
            research=cast(ResearchManager, None),  # set below (needs the context)
            train_lock=threading.Lock(),
            discovery=DiscoveryService(repo, cfg),
            discovery_lock=threading.Lock(),
            auth=AuthService(repo, cfg.auth),
            metrics=metrics,
            backups=BackupService(cfg),
            started_monotonic=time.monotonic(),
            relationships=RelationshipService(repo, cfg),
            collector=ChainCollector(router, repo, cfg),
            solana_collector=SolanaCollector(router, repo, cfg),
            cases=CaseService(repo),
            scheduler=None,
        )
        ctx = app.state.bip
        ctx.research = ResearchManager(ctx, llm_provider or build_provider(cfg.ai, environ))
        if cfg.workers.enabled:
            ctx.scheduler = Scheduler(
                repo,
                build_jobs(ctx, ctx.backups),
                poll_seconds=cfg.workers.poll_seconds,
                max_backoff=timedelta(hours=cfg.workers.max_backoff_hours),
            )
            ctx.scheduler.start()
        try:  # runs interrupted by a restart are marked failed, never resumed silently
            ctx.research.recover()
        except DatabaseError as exc:  # the API must still start and report the database state
            log.warning("could not recover research runs at startup: %s", exc)
        try:
            yield
        finally:
            if ctx.scheduler is not None:
                ctx.scheduler.stop()
            ctx.research.shutdown()
            if engine is None:
                db.dispose()

    app = FastAPI(
        title="BIP API",
        version=__version__,
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
    )
    install_error_handlers(app)
    for module in (
        auth,
        provider_intel,  # before `providers`: its static /providers/... paths must match first
        health,
        system,
        chains,
        dashboard,
        providers,
        wallets,
        watchlists,
        investigations,
        settings_route,
        ingestion,
        intelligence,
        discovery,
        entities,
        ml,
        research,
    ):
        app.include_router(module.router, prefix=API_PREFIX)

    required = cfg.auth.mode == "required"
    # Innermost first (each call wraps the previous): protection, CORS, body limit, headers.
    app.add_middleware(ProtectionMiddleware, settings=cfg, prefix=API_PREFIX)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.api.cors_origins),
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token"] if required else ["Content-Type"],
        allow_credentials=required,
    )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=cfg.api.max_request_bytes)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(MetricsMiddleware, metrics=metrics)
    return app
