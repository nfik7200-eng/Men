"""Sign-in, sign-out, the current user, user management and the audit trail.

Who may call what is decided by :mod:`bip.api.access` and enforced by the protection middleware;
these handlers assume the middleware already ran. With ``auth.mode = disabled`` the sign-in
endpoints say so and user creation is refused. The first administrator is created either with
``python -m bip.tools.users`` or once, from the sign-in page, with the one-time code the server
writes to an owner-only file (``bip.security.bootstrap``).
"""

from __future__ import annotations

import threading
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request, Response, status
from starlette.responses import JSONResponse

from bip.api.deps import AppContext, Context
from bip.api.errors import error_response
from bip.api.protection import COOKIE_NAME
from bip.api.schemas import (
    BootstrapBody,
    LoginBody,
    PasswordChange,
    PasswordSet,
    UserCreate,
    UserUpdate,
)
from bip.db.errors import InvalidRecordError
from bip.security.auth import AuthError

router = APIRouter(tags=["auth"])
Id = Annotated[int, Path(ge=1, le=2**53)]


def _ip(request: Request) -> str | None:
    value = request.scope.get("state", {}).get("client_ip")
    return str(value) if value else None


def _user(request: Request) -> Any:
    return request.scope.get("state", {}).get("user")


def _clear_cookie(response: Response, secure: bool) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", secure=secure, httponly=True, samesite="strict")


@router.get("/auth/status")
def auth_status(request: Request, ctx: Context) -> dict[str, Any]:
    """Public: whether sign-in is required, whether the first user still has to be created, and
    whether this caller is signed in."""
    cfg = ctx.settings.auth
    token = request.cookies.get(COOKIE_NAME)
    session = ctx.auth.resolve(token) if token else None
    return {
        "mode": cfg.mode,
        "setup_required": cfg.mode == "required" and not ctx.auth.has_users(),
        "authenticated": session is not None,
        "user": session.user.public() if session else None,
    }


@router.post("/auth/login")
def login(request: Request, ctx: Context, body: LoginBody) -> Any:
    cfg = ctx.settings.auth
    if cfg.mode != "required":
        return error_response(409, "auth_disabled", "Authentication is not enabled on this server.")
    ip = _ip(request)
    try:
        user = ctx.auth.authenticate(body.username, body.password, ip)
    except AuthError:
        return error_response(401, "invalid_credentials", "Invalid username or password.")
    return _signed_in(request, ctx, user, ip)


def _signed_in(
    request: Request, ctx: AppContext, user: Any, ip: str | None, status_code: int = 200
) -> JSONResponse:
    """Start a session for ``user`` and answer with its CSRF token and the session cookie."""
    cfg = ctx.settings.auth
    previous = request.cookies.get(COOKIE_NAME)
    if previous:
        ctx.auth.end_session(previous)  # a new session always replaces the old one
    token, session = ctx.auth.start_session(user, ip)
    response = JSONResponse(
        {
            "user": user.public(),
            "csrf_token": session.csrf_token,
            "expires_at": session.expires_at.isoformat(),
        },
        status_code=status_code,
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=cfg.session_ttl_hours * 3600,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="strict",
        path="/",
    )
    return response


_bootstrap_lock = threading.Lock()


@router.post("/auth/bootstrap", response_model=None)
def bootstrap(request: Request, ctx: Context, body: BootstrapBody) -> Any:
    """Public, once: create the first administrator with the one-time code the server wrote to
    ``<data_root>/config/first-admin-code``, and sign them in. Closed as soon as a user exists."""
    if ctx.settings.auth.mode != "required":
        return error_response(409, "auth_disabled", "Authentication is not enabled on this server.")
    ip = _ip(request)
    with _bootstrap_lock:
        if ctx.auth.has_users() or ctx.first_admin is None:
            return error_response(409, "already_set_up", "An administrator already exists.")
        if not ctx.first_admin.check(body.code):
            ctx.auth.audit(
                body.username[:64] or None, "user.bootstrap", None, "denied", ip, "wrong code"
            )
            return error_response(401, "invalid_setup_code", "The setup code is not correct.")
        try:
            user = ctx.auth.create_user(body.username, body.password, "admin", actor="bootstrap")
        except InvalidRecordError as exc:
            return error_response(422, "invalid_user", str(exc))
        ctx.first_admin.consume()
        ctx.first_admin = None
    ctx.auth.audit(user.username, "user.bootstrap", user.username, "ok", ip)
    return _signed_in(request, ctx, user, ip, status_code=201)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, ctx: Context) -> Response:
    token = request.cookies.get(COOKIE_NAME)
    user = _user(request)
    if token:
        ctx.auth.end_session(token)
    if user is not None:
        ctx.auth.audit(user.username, "logout", None, "ok", _ip(request))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_cookie(response, ctx.settings.auth.cookie_secure)
    return response


@router.get("/auth/me")
def me(request: Request, ctx: Context) -> dict[str, Any]:
    state = request.scope.get("state", {})
    session = state.get("session")
    if ctx.settings.auth.mode != "required" or session is None:
        return {"mode": ctx.settings.auth.mode, "user": None, "csrf_token": None}
    return {
        "mode": "required",
        "user": session.user.public(),
        "csrf_token": session.csrf_token,
        "expires_at": session.expires_at.isoformat(),
    }


@router.post("/auth/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(request: Request, ctx: Context, body: PasswordChange) -> Response:
    user = _user(request)
    if user is None:
        return error_response(409, "auth_disabled", "Authentication is not enabled on this server.")
    ctx.auth.change_own_password(user.id, body.current_password, body.new_password)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_cookie(response, ctx.settings.auth.cookie_secure)  # every session of the user ended
    return response


# ----------------------------------------------------------------------------- users (admin)


@router.get("/users")
def list_users(ctx: Context) -> dict[str, Any]:
    return {"items": [u.public() for u in ctx.auth.list_users()]}


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(request: Request, ctx: Context, body: UserCreate) -> Any:
    if ctx.settings.auth.mode != "required":
        return error_response(
            409,
            "auth_disabled",
            "Enable authentication first. Create the first user with python -m bip.tools.users.",
        )
    actor = _user(request)
    user = ctx.auth.create_user(
        body.username, body.password, body.role, actor=actor.username if actor else None
    )
    return user.public()


@router.patch("/users/{user_id}")
def update_user(request: Request, ctx: Context, user_id: Id, body: UserUpdate) -> dict[str, Any]:
    actor = _user(request)
    user = ctx.auth.update_user(
        user_id, role=body.role, enabled=body.enabled, actor=actor.username if actor else None
    )
    return user.public()


@router.post("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def set_password(request: Request, ctx: Context, user_id: Id, body: PasswordSet) -> Response:
    actor = _user(request)
    ctx.auth.set_password(user_id, body.new_password, actor=actor.username if actor else None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/audit")
def audit(
    ctx: Context,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    actor: Annotated[str | None, Query(max_length=64)] = None,
    action: Annotated[str | None, Query(max_length=80)] = None,
) -> dict[str, Any]:
    return ctx.auth.recent_audit(limit=limit, offset=offset, actor=actor, action=action)
