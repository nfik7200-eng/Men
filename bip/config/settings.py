"""Typed, validated settings loaded from ``BIP_*`` environment variables.

Nested groups use ``__`` (e.g. ``BIP_ROUTER__CROSS_VERIFICATION_SAMPLE_RATE=0.05``). Settings do
**not** read a ``.env`` file implicitly; for local development run commands through
``uv run --env-file .env …`` so the source of configuration is always explicit.

Secrets (``postgres_dsn``, ``redis_url``) are :class:`pydantic.SecretStr`: they print as
``**********``, are registered for log redaction on load, and are never included in
:class:`~bip.core.exceptions.ConfigurationError` messages. Provider API keys are *not* settings;
they are referenced by environment-variable name from the provider registry (Stage 2) and read
with :func:`bip.security.secrets.read_secret`.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ByteSize,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from bip.contracts.blockchain import get_chain
from bip.core.exceptions import ConfigurationError
from bip.security.secrets import register_secret


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class RouterFlags(BaseModel):
    """Feature flags for the future Provider Intelligence Router (Stage 3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    live_requests_enabled: bool = False
    """Master switch for real provider requests. No code path uses it yet; it must stay False
    until the capability prober (Stage 4) exists."""
    circuit_breakers_enabled: bool = True
    cross_verification_sample_rate: Annotated[float, Field(ge=0.0, le=1.0)] = 0.01


_LOCAL_ORIGIN_PREFIXES = ("http://localhost:", "http://127.0.0.1:", "https://")


class ApiSettings(BaseModel):
    """HTTP API settings (``BIP_API__*``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = "127.0.0.1"
    """Bind address. Defaults to loopback; there is no authentication yet (see docs)."""
    port: Annotated[int, Field(ge=1, le=65535)] = 8000
    cors_origins: Annotated[tuple[str, ...], NoDecode] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    """Exact origins allowed by CORS. Comma-separated in the environment. ``*`` is rejected."""
    max_request_bytes: Annotated[int, Field(ge=1024, le=10 * 1024 * 1024)] = 64 * 1024

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("cors_origins")
    @classmethod
    def _exact_origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for origin in value:
            if "*" in origin:
                raise ValueError("wildcard CORS origins are not allowed")
            if not origin.startswith(_LOCAL_ORIGIN_PREFIXES) or origin.endswith("/"):
                raise ValueError(
                    "CORS origins must be exact, e.g. http://localhost:5173 or https://host"
                )
        return value


class DatabaseSettings(BaseModel):
    """Operational SQLite database settings (``BIP_DATABASE__*``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.\-]{1,64}\.sqlite3$")] = "bip.sqlite3"
    """File name inside ``<data_root>/operational/``. Never a path."""
    busy_timeout_ms: Annotated[int, Field(ge=0, le=60_000)] = 5_000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BIP_",
        env_nested_delimiter="__",
        env_file=None,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )

    env: Environment = Environment.DEVELOPMENT
    log_level: LogLevel = "INFO"
    log_format: Literal["json", "text"] = "json"

    data_root: Path = Path("var/data")
    """Root of all BIP data. Relative paths resolve against the working directory."""
    temp_dir: Path | None = None
    """Scratch space; defaults to ``<data_root>/tmp``."""
    max_data_bytes: ByteSize = ByteSize(5 * 1024**3)
    """Hard cap on bytes BIP may store under ``data_root`` (ADR-011 prototype cap: 5 GiB)."""
    min_free_bytes: ByteSize = ByteSize(5 * 1024**3)
    """Writes pause when free space on the data filesystem would drop below this."""
    min_free_ratio: Annotated[float, Field(ge=0.0, le=0.5)] = 0.05

    enabled_chains: Annotated[tuple[str, ...], NoDecode] = ("ethereum", "solana")
    """Comma-separated in the environment: ``BIP_ENABLED_CHAINS=ethereum,solana``."""

    router: RouterFlags = RouterFlags()
    api: ApiSettings = ApiSettings()
    database: DatabaseSettings = DatabaseSettings()

    postgres_dsn: SecretStr | None = None
    """Optional. Not needed for unit tests; no database code exists yet."""
    redis_url: SecretStr | None = None

    @field_validator("enabled_chains", mode="before")
    @classmethod
    def _split_chains(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("enabled_chains")
    @classmethod
    def _known_chains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("at least one chain must be enabled")
        if len(set(value)) != len(value):
            raise ValueError("enabled_chains contains duplicates")
        for chain in value:
            get_chain(chain)
        return value

    @field_validator("postgres_dsn")
    @classmethod
    def _postgres_scheme(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().startswith(
            ("postgresql://", "postgres://", "postgresql+psycopg://")
        ):
            raise ValueError("must start with postgresql:// (value not shown)")
        return value

    @field_validator("redis_url")
    @classmethod
    def _redis_scheme(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().startswith(("redis://", "rediss://")):
            raise ValueError("must start with redis:// or rediss:// (value not shown)")
        return value

    @model_validator(mode="after")
    def _environment_rules(self) -> Self:
        if self.env is Environment.PRODUCTION:
            if not self.data_root.is_absolute():
                raise ValueError("data_root must be an absolute path in production")
            if self.log_format != "json":
                raise ValueError("log_format must be 'json' in production")
        if (
            self.temp_dir is not None
            and not self.temp_dir.is_absolute()
            and self.env is Environment.PRODUCTION
        ):
            raise ValueError("temp_dir must be an absolute path in production")
        return self

    @property
    def resolved_data_root(self) -> Path:
        return self.data_root.expanduser().resolve()

    @property
    def resolved_temp_dir(self) -> Path:
        base = self.temp_dir if self.temp_dir is not None else self.data_root / "tmp"
        return base.expanduser().resolve()


def _format_errors(exc: ValidationError) -> str:
    lines: list[str] = []
    for err in exc.errors(include_input=False, include_url=False, include_context=False):
        loc = ".".join(str(part) for part in err["loc"]) or "settings"
        lines.append(f"  - BIP_{loc.upper().replace('.', '__')}: {err['msg']}")
    return "invalid configuration:\n" + "\n".join(lines)


def load_settings(**overrides: Any) -> Settings:
    """Load settings from the environment (plus explicit overrides) or raise a readable error.

    Error messages list variable names and reasons only, never the offending values.
    """
    try:
        settings = Settings(**overrides)
    except ValidationError as exc:
        raise ConfigurationError(_format_errors(exc)) from None
    register_secret(settings.postgres_dsn)
    register_secret(settings.redis_url)
    return settings
