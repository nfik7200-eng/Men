"""Setup from the UI (ADR-056). Admin only, reads included (``bip.api.access``).

Secrets are write-only: responses say whether a secret is set, where it comes from and when it
changed, never its value. Provider credentials apply at once (endpoints are resolved per call);
everything else applies after a restart, which ``POST /setup/restart`` performs in-process.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Path, Request, Response, status
from pydantic import BaseModel, Field

from bip.api.deps import AppContext, Context, WritableContext
from bip.api.errors import error_response
from bip.config.setup_catalog import BY_KEY, EDITABLE, EditableSetting
from bip.config.store import env_sets, flatten, resolve_overrides, validate_override
from bip.core.exceptions import ConfigurationError
from bip.discovery.telegram import TOKEN_PATTERN
from bip.providers.registry import resolve_endpoint

router = APIRouter(tags=["setup"])

TELEGRAM_TOKEN = "BIP_TELEGRAM_BOT_TOKEN"  # noqa: S105 - a variable name, not a secret
Applies = Literal["immediately", "after_restart"]
NameP = Annotated[str, Path(min_length=4, max_length=128, pattern=r"^BIP_[A-Z0-9_]+$")]
KeyP = Annotated[str, Path(min_length=2, max_length=128, pattern=r"^[a-z0-9_.]+$")]


class SecretIn(BaseModel):
    value: Annotated[str, Field(min_length=1, max_length=4096)]


class SettingIn(BaseModel):
    value: bool | int | str | list[str] | None


@dataclass(frozen=True, slots=True)
class SecretSpec:
    name: str
    group: str
    label: str
    applies: Applies
    help: str
    provider_id: str | None = None
    chains: tuple[str, ...] = ()
    access: str | None = None
    docs_url: str | None = None
    terms_url: str | None = None
    checkable: bool = False
    """A provider health check can run once it is set (the provider has an adapter)."""


def _actor(request: Request) -> str:
    user = request.scope.get("state", {}).get("user")
    return str(user.username) if user is not None else "local"


def secret_catalog(ctx: AppContext) -> dict[str, SecretSpec]:
    out: dict[str, SecretSpec] = {}
    for spec in ctx.registry.all():
        if spec.endpoint_env is None:
            continue
        is_url = spec.endpoint_env.endswith("_URL")
        out[spec.endpoint_env] = SecretSpec(
            name=spec.endpoint_env,
            group="Providers",
            label=spec.name,
            applies="immediately",
            help=(
                "The full endpoint URL from your provider dashboard (it usually contains the key)."
                if is_url
                else "The API key or token from your provider dashboard."
            ),
            provider_id=spec.id,
            chains=spec.chains,
            access=spec.access,
            docs_url=spec.docs_url,
            terms_url=spec.terms_url,
            checkable=spec.protocol != "none",
        )
    out[TELEGRAM_TOKEN] = SecretSpec(
        name=TELEGRAM_TOKEN,
        group="Alerts",
        label="Telegram bot token",
        applies="after_restart",
        help="From @BotFather: digits, a colon, then the token text.",
        docs_url="https://core.telegram.org/bots#how-do-i-create-a-bot",
    )
    ai_key = ctx.settings.ai.api_key_env
    out[ai_key] = SecretSpec(
        name=ai_key,
        group="AI research",
        label="OpenAI-compatible API key",
        applies="after_restart",
        help="Only needed for the openai_compatible provider.",
    )
    return out


def _check_secret(ctx: AppContext, spec: SecretSpec, value: str) -> str | None:
    """A value-free reason the value cannot be used, or ``None``."""
    if spec.name == TELEGRAM_TOKEN:
        return None if TOKEN_PATTERN.match(value) else "not a Telegram bot token"
    if any(c.isspace() for c in value):
        return "the value must not contain spaces"
    if spec.provider_id is not None and spec.name.endswith("_URL"):
        resolution = resolve_endpoint(ctx.registry.get(spec.provider_id), {spec.name: value})
        if resolution.configuration != "configured":
            return resolution.reason.replace(value, "[value]")
        return None
    if not 8 <= len(value) <= 512:
        return "the value must be 8 to 512 characters"
    return None


def _running_value(ctx: AppContext, key: str) -> Any:
    node: Any = ctx.settings
    for part in key.split("."):
        node = getattr(node, part)
    if isinstance(node, tuple):
        return [str(v) for v in cast(tuple[object, ...], node)]
    return node


def _changed_since_start(ctx: AppContext, updated_at: object, key: str) -> bool:
    if key in ctx.setup_cleared:
        return True
    return isinstance(updated_at, str) and updated_at > ctx.config_loaded_at


def _overview(ctx: AppContext) -> dict[str, Any]:
    store, env = ctx.config_store, ctx.environ
    pending: list[str] = []
    secrets: list[dict[str, Any]] = []
    for spec in secret_catalog(ctx).values():
        meta = store.secret_meta(spec.name)
        source = env.source(spec.name)
        if spec.applies == "after_restart" and _changed_since_start(
            ctx, meta and meta["updated_at"], spec.name
        ):
            pending.append(spec.name)
        secrets.append(
            {
                "name": spec.name,
                "group": spec.group,
                "label": spec.label,
                "help": spec.help,
                "applies": spec.applies,
                "provider_id": spec.provider_id,
                "chains": list(spec.chains),
                "access": spec.access,
                "docs_url": spec.docs_url,
                "terms_url": spec.terms_url,
                "checkable": spec.checkable,
                "set": source is not None,
                "source": source,
                "updated_at": meta["updated_at"] if meta else None,
                "updated_by": meta["updated_by"] if meta else None,
            }
        )
    stored = flatten(store.overrides())
    settings: list[dict[str, Any]] = []
    for item in EDITABLE:
        meta = store.setting_meta(item.key)
        if _changed_since_start(ctx, meta and meta["updated_at"], item.key):
            pending.append(item.key)
        in_env = env_sets(os.environ, item.key)
        settings.append(
            {
                "key": item.key,
                "group": item.group,
                "label": item.label,
                "help": item.help,
                "kind": item.kind,
                "choices": list(item.choices),
                "contacts_providers": item.contacts_providers,
                "value": _running_value(ctx, item.key),
                "stored": stored.get(item.key),
                "source": "environment"
                if in_env
                else ("setup" if item.key in stored else "default"),
                "updated_at": meta["updated_at"] if meta else None,
                "updated_by": meta["updated_by"] if meta else None,
            }
        )
    report = ctx.setup_report
    return {
        "secrets": secrets,
        "settings": settings,
        "restart": {
            "available": ctx.restart is not None and ctx.restart.available,
            "pending": pending,
        },
        "startup": {
            "overrides_error": report.error if report else None,
            "ignored_keys": report.ignored if report else [],
            "overridden_by_environment": report.overridden_by_environment if report else [],
        },
    }


@router.get("/setup")
def setup_overview(ctx: Context) -> dict[str, Any]:
    return _overview(ctx)


def _invalid(message: str) -> Response:
    return error_response(422, "invalid_setup_value", message)


@router.post("/setup/secrets/{name}", response_model=None)
def set_secret(ctx: WritableContext, request: Request, name: NameP, body: SecretIn) -> Any:
    spec = secret_catalog(ctx).get(name)
    if spec is None:
        return error_response(404, "not_found", f"{name} is not a setup secret")
    value = body.value.strip()
    reason = _check_secret(ctx, spec, value)
    actor = _actor(request)
    if reason is not None:
        ctx.auth.audit(actor, "setup.secret.set", name, "denied", detail=reason[:200])
        return _invalid(f"{spec.label}: {reason}")
    try:
        ctx.config_store.set_secret(name, value, actor=actor)
    except ConfigurationError as exc:
        return _invalid(str(exc))
    ctx.setup_cleared.discard(name)
    ctx.auth.audit(actor, "setup.secret.set", name, "ok")
    return {"name": name, "set": True, "applies": spec.applies}


def _telegram_enabled_from_setup(ctx: AppContext) -> bool:
    return flatten(ctx.config_store.overrides()).get("alerts.telegram_enabled") is True and not (
        env_sets(os.environ, "alerts.telegram_enabled")
    )


@router.delete("/setup/secrets/{name}", response_model=None)
def clear_secret(ctx: WritableContext, request: Request, name: NameP) -> Any:
    if name not in secret_catalog(ctx):
        return error_response(404, "not_found", f"{name} is not a setup secret")
    if name == TELEGRAM_TOKEN and _telegram_enabled_from_setup(ctx):
        return error_response(
            409, "in_use", "switch Telegram alerts off before removing the bot token"
        )
    actor = _actor(request)
    if not ctx.config_store.clear_secret(name, actor=actor):
        return error_response(404, "not_found", f"{name} is not stored by setup")
    ctx.setup_cleared.add(name)
    ctx.auth.audit(actor, "setup.secret.clear", name, "ok")
    return {"name": name, "set": ctx.environ.source(name) is not None}


def _check_kind(item: EditableSetting, value: object) -> tuple[object, str | None]:
    if item.kind == "bool":
        return value, None if isinstance(value, bool) else "expected true or false"
    if item.kind == "int":
        ok = isinstance(value, int) and not isinstance(value, bool)
        return value, None if ok else "expected a whole number"
    if item.kind == "list":
        items = cast(list[object], value) if isinstance(value, list) else None
        if items is None or not all(isinstance(v, str) for v in items):
            return None, "expected a list of text values"
        clean = [str(v).strip() for v in items if str(v).strip()]
        return clean, None if len(clean) <= 500 else "at most 500 entries"
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None  # an optional text setting cleared to "not set"
    if not isinstance(value, str):
        return value, "expected text"
    if item.kind == "choice" and value not in item.choices:
        return value, f"expected one of: {', '.join(item.choices)}"
    return value.strip(), None


@router.post("/setup/settings/{key}", response_model=None)
def set_setting(ctx: WritableContext, request: Request, key: KeyP, body: SettingIn) -> Any:
    item = BY_KEY.get(key)
    if item is None:
        return error_response(404, "not_found", f"{key} cannot be changed from the setup page")
    value, reason = _check_kind(item, body.value)
    if reason is None:
        # Validate against the configuration the next start would load: the running settings
        # plus every stored override, plus this change.
        nested, _report = resolve_overrides(ctx.config_store, os.environ)
        base = ctx.settings.model_copy(deep=True)
        for k, v in flatten(nested).items():
            if validate_override(base, k, v) is None:
                base = _apply(base, k, v)
        reason = validate_override(base, key, value)
        enabling_telegram = key == "alerts.telegram_enabled" and value is True
        if reason is None and enabling_telegram and TELEGRAM_TOKEN not in ctx.environ:
            reason = "set the Telegram bot token first"
    actor = _actor(request)
    if reason is not None:
        ctx.auth.audit(actor, "setup.setting.set", key, "denied", detail=reason[:200])
        return _invalid(f"{item.label}: {reason}")
    ctx.config_store.set_setting(key, value, actor=actor)
    ctx.setup_cleared.discard(key)
    ctx.auth.audit(actor, "setup.setting.set", key, "ok")
    return {"key": key, "stored": value, "applies": "after_restart"}


def _apply(base: Any, key: str, value: object) -> Any:
    from bip.config.settings import Settings

    data = base.model_dump()
    node = data
    *parents, leaf = key.split(".")
    for part in parents:
        node = node[part]
    node[leaf] = value
    return Settings.model_validate(data)


@router.delete("/setup/settings/{key}", response_model=None)
def clear_setting(ctx: WritableContext, request: Request, key: KeyP) -> Any:
    if key not in BY_KEY:
        return error_response(404, "not_found", f"{key} cannot be changed from the setup page")
    actor = _actor(request)
    if not ctx.config_store.clear_setting(key, actor=actor):
        return error_response(404, "not_found", f"{key} is not stored by setup")
    ctx.setup_cleared.add(key)
    ctx.auth.audit(actor, "setup.setting.clear", key, "ok")
    return {"key": key, "stored": None, "applies": "after_restart"}


@router.post("/setup/restart", response_model=None, status_code=status.HTTP_202_ACCEPTED)
def restart(ctx: Context, request: Request) -> Any:
    actor = _actor(request)
    if ctx.restart is None or not ctx.restart.request():
        return error_response(
            409,
            "restart_unavailable",
            "this API was not started with `python -m bip.api`; restart it the way it was started",
        )
    ctx.auth.audit(actor, "setup.restart", None, "ok")
    return {"restarting": True}
