"""Run the API: ``uv run python -m bip.api`` (binds to ``BIP_API__HOST``:``BIP_API__PORT``)."""

from __future__ import annotations

import uvicorn

from bip.api.app import create_app
from bip.config.settings import load_settings
from bip.core.logging import configure_logging


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level, settings.log_format)
    uvicorn.run(
        create_app(settings),
        host=settings.api.host,
        port=settings.api.port,
        log_config=None,
        access_log=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
