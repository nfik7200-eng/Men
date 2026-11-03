"""Error envelope and exception handlers.

Every error response has the shape ``{"error": {"code": ..., "message": ...}}``. Messages are
written for users. Stack traces, SQL, file paths and input values are never included; unexpected
exceptions are logged (through the redacting logger) and reported as ``internal_error``.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from bip.chains.common import ParseError
from bip.core.exceptions import StorageLimitError
from bip.core.logging import get_logger
from bip.db.errors import (
    DatabaseError,
    DuplicateRecordError,
    InvalidRecordError,
    RecordNotFoundError,
)
from bip.providers.registry import ProviderNotFoundError
from bip.router.router import NoEligibleProviderError, RoutingFailedError
from bip.security.secrets import redact_text

log = get_logger("bip.api")


def error_response(status: int, code: str, message: str, details: object = None) -> JSONResponse:
    body: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return JSONResponse(status_code=status, content={"error": body})


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RecordNotFoundError)
    async def _not_found(_: Request, exc: RecordNotFoundError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return error_response(404, "not_found", str(exc))

    @app.exception_handler(DuplicateRecordError)
    async def _duplicate(_: Request, exc: DuplicateRecordError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return error_response(409, "conflict", str(exc))

    @app.exception_handler(InvalidRecordError)
    async def _invalid(_: Request, exc: InvalidRecordError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return error_response(422, "invalid_input", str(exc))

    @app.exception_handler(DatabaseError)
    async def _database(_: Request, exc: DatabaseError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        log.error("database error: %s", exc)
        return error_response(
            503, "database_unavailable", "The database is unavailable. Try again."
        )

    @app.exception_handler(StorageLimitError)
    async def _storage(_: Request, exc: StorageLimitError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        log.warning("write refused by disk guard: %s", exc)
        return error_response(
            507,
            "storage_limit",
            "Writes are paused: the BIP storage limit or free-space floor has been reached.",
        )

    @app.exception_handler(NoEligibleProviderError)
    async def _no_provider(_: Request, exc: NoEligibleProviderError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return error_response(
            503,
            "no_eligible_provider",
            f"No provider can serve {exc.chain} {exc.method} right now.",
            [{"provider": k, "reason": redact_text(v)} for k, v in exc.reasons.items()],
        )

    @app.exception_handler(RoutingFailedError)
    async def _routing(_: Request, exc: RoutingFailedError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return error_response(
            502,
            "upstream_failed",
            "Every provider attempt failed.",
            [redact_text(a)[:300] for a in exc.attempts],
        )

    @app.exception_handler(ParseError)
    async def _upstream_invalid(_: Request, exc: ParseError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        log.warning("provider returned malformed data: %s", exc)
        return error_response(
            502,
            "upstream_invalid",
            "A provider returned data that failed validation; nothing was stored.",
        )

    @app.exception_handler(ProviderNotFoundError)
    async def _provider_missing(_: Request, exc: ProviderNotFoundError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return error_response(404, "not_found", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        details = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"),
                "message": str(err.get("msg", "invalid value")).removeprefix("Value error, "),
            }
            for err in exc.errors()
        ]
        return error_response(422, "validation_error", "The request is not valid.", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        code = {404: "not_found", 405: "method_not_allowed", 413: "payload_too_large"}.get(
            exc.status_code, "http_error"
        )
        message = str(exc.detail) if exc.detail else "Request failed."
        return error_response(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        log.exception("unhandled error: %s", type(exc).__name__)
        return error_response(500, "internal_error", "An unexpected error occurred.")
