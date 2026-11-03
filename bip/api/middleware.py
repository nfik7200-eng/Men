"""ASGI middleware: request body size limit and defensive response headers."""

from __future__ import annotations

import json

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _BodyTooLargeError(HTTPException):
    """Raised mid-stream. It is an HTTPException so FastAPI's body parser re-raises it as-is
    (it converts any other exception into a 400), and the app's handler renders a 413."""

    def __init__(self) -> None:
        super().__init__(status_code=413, detail="Request body is too large.")


class BodySizeLimitMiddleware:
    """Reject requests whose body exceeds ``max_bytes`` with 413.

    Checks ``Content-Length`` up front and also counts streamed (chunked) bodies.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    await _reject(send, 400, "bad_request", "Invalid Content-Length header.")
                    return
                if declared > self.max_bytes:
                    await _reject(send, 413, "payload_too_large", "Request body is too large.")
                    return
        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLargeError:
            if not started:
                await _reject(send, 413, "payload_too_large", "Request body is too large.")


class SecurityHeadersMiddleware:
    """Adds conservative headers to every HTTP response."""

    HEADERS = (
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"cache-control", b"no-store"),
    )

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def add_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                headers.extend(h for h in self.HEADERS if h[0] not in present)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, add_headers)


async def _reject(send: Send, status: int, code: str, message: str) -> None:
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
