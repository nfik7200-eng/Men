from __future__ import annotations

from fastapi import APIRouter

from bip import __version__
from bip.api.deps import Context
from bip.api.schemas import Health
from bip.core.exceptions import StorageLimitError
from bip.db.engine import check_connectivity
from bip.db.types import utcnow

router = APIRouter(tags=["system"])


@router.get("/health", response_model=Health)
def get_health(ctx: Context) -> Health:
    db_ok = check_connectivity(ctx.repo.engine)
    try:
        writes = "allowed" if ctx.disk_guard.check().allowed else "refused"
    except StorageLimitError:
        writes = "unknown"
    return Health(
        status="ok" if db_ok and writes == "allowed" else "degraded",
        version=__version__,
        environment=ctx.settings.env.value,
        database="ok" if db_ok else "unavailable",
        storage_writes=writes,
        timestamp=utcnow(),
    )
