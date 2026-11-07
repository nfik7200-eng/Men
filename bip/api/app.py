"""FastAPI application factory.

``create_app()`` builds the app from settings. On startup it opens (and migrates) the SQLite
database under the configured data root. Tests inject a temporary engine and disk guard.
"""

from __future__ import annotations

import threading
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine

from bip import __version__
from bip.api.deps import AppContext
from bip.api.errors import install_error_handlers
from bip.api.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from bip.api.routes import (
    chains,
    dashboard,
    health,
    ingestion,
    intelligence,
    investigations,
    ml,
    provider_intel,
    providers,
    research,
    settings_route,
    wallets,
    watchlists,
)
from bip.config.settings import Settings, load_settings
from bip.core.logging import get_logger
from bip.db.engine import create_db_engine
from bip.db.errors import DatabaseError
from bip.db.repository import Repository
from bip.ingest.wallet import WalletIngestor
from bip.intel.service import IntelligenceService
from bip.intel.verify import Corroborator
from bip.ml.labels import LabelStore
from bip.ml.service import ClassificationService
from bip.ml.training import Trainer
from bip.providers.intelligence import ProbeStore
from bip.providers.prober import Prober
from bip.providers.registry import Registry, default_registry
from bip.providers.state import ProviderStateStore
from bip.research.engine import ResearchManager
from bip.research.llm import LLMProvider, build_provider
from bip.router.router import ProviderRouter
from bip.rpc.transport import HttpsTransport, Transport
from bip.storage.disk_guard import DiskGuard, guard_from_settings

API_PREFIX = "/api/v1"
log = get_logger("bip.api")


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
        )
        ctx = app.state.bip
        ctx.research = ResearchManager(ctx, llm_provider or build_provider(cfg.ai, environ))
        try:  # runs interrupted by a restart are marked failed, never resumed silently
            ctx.research.recover()
        except DatabaseError as exc:  # the API must still start and report the database state
            log.warning("could not recover research runs at startup: %s", exc)
        try:
            yield
        finally:
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
        provider_intel,  # before `providers`: its static /providers/... paths must match first
        health,
        chains,
        dashboard,
        providers,
        wallets,
        watchlists,
        investigations,
        settings_route,
        ingestion,
        intelligence,
        ml,
        research,
    ):
        app.include_router(module.router, prefix=API_PREFIX)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.api.cors_origins),
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
        allow_credentials=False,
    )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=cfg.api.max_request_bytes)
    app.add_middleware(SecurityHeadersMiddleware)
    return app
