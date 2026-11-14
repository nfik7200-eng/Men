"""Availability of the optional ML runtime (numpy, scikit-learn, joblib).

The ML packages are the default ``ml`` dependency group; ``uv sync --no-group ml`` omits them.
Without them BIP still starts, classifies with the deterministic rules baseline (and says
so), and refuses training with ``ml_unavailable``.
``bip.ml.backend`` is only imported through :func:`backend`.
"""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from types import ModuleType

from bip.core.exceptions import BIPError

REQUIRED = ("numpy", "sklearn", "joblib")
INSTALL_HINT = (
    "Install it with `uv sync` (the default `ml` dependency group) on a supported "
    "Linux, macOS or Windows host."
)


class MLUnavailableError(BIPError):
    """The ML runtime is not installed in this environment."""


@dataclass(frozen=True, slots=True)
class MLRuntime:
    available: bool
    missing: tuple[str, ...]


def ml_runtime() -> MLRuntime:
    missing: list[str] = []
    for name in REQUIRED:
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(name)
    return MLRuntime(not missing, tuple(missing))


def backend() -> ModuleType:
    """Import and return ``bip.ml.backend``; raise :class:`MLUnavailableError` if it cannot run."""
    st = ml_runtime()
    if not st.available:
        raise MLUnavailableError(
            f"The ML runtime is not installed (missing: {', '.join(st.missing)}). {INSTALL_HINT}"
        )
    try:
        return importlib.import_module("bip.ml.backend")
    except ImportError as exc:
        raise MLUnavailableError(
            f"The ML runtime failed to import ({exc}). {INSTALL_HINT}"
        ) from None
