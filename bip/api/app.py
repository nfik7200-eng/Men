"""FastAPI application factory.

``create_app()`` builds the app from settings. On startup it opens (and migrates) the SQLite
database under the configured data root. Tests inject a temporary engine and disk guard.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

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
    investigations,
    providers,
    settings_route,
    wallets,
    watchlists,
)
from bip.config.settings import Settings, load_settings
from bip.db.engine import create_db_engine
from bip.db.repository import Repository
from bip.storage.disk_guard import DiskGuard, guard_from_settings

API_PREFIX = "/api/v1"


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    disk_guard: DiskGuard | None = None,
) -> FastAPI:
    cfg = settings if settings is not None else load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        db = engine if engine is not None else create_db_engine(cfg)
        guard = disk_guard if disk_guard is not None else guard_from_settings(cfg)
        app.state.bip = AppContext(settings=cfg, repo=Repository(db), disk_guard=guard)
        try:
            yield
        finally:
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
        health,
        chains,
        dashboard,
        providers,
        wallets,
        watchlists,
        investigations,
        settings_route,
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
