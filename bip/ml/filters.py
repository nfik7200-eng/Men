"""Training-data filters: an ordered, auditable pipeline applied before any model is trained.

Every trainer maps its examples to :class:`Candidate` and calls :func:`run_pipeline`. Each filter
reports what it dropped and why, so a training run can be explained and reproduced.

Rules:

* **Missing stays missing.** ``None`` features are missing, never 0. No filter imputes.
* **Machine labels never train.** Model predictions, AI-proposed labels and synthetic labels are
  dropped by :func:`human_verified`.
* The pipeline is deterministic: the input is ordered by ``key`` first, so the report does not
  depend on input order. The input is never mutated.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from bip.config.settings import FILTER_NAMES
from bip.ml.labels import TRAINABLE

if TYPE_CHECKING:
    from bip.config.settings import MLSettings

FILTERS_VERSION = "1"
MAX_SAMPLES_PER_REASON = 20

MACHINE_ORIGIN = frozenset(
    {
        # ``bip/ml/training.py``: ``source == "synthetic"`` is the built-in synthetic dataset
        # (``data_kind = "synthetic"``, model status ``experimental_synthetic`` in the same file).
        "synthetic",
        "experimental_synthetic",
        # ``bip/research/engine.py``: classification evidence kinds that are always ``inferred``
        # and never verified (a model prediction and a rule verdict).
        "model_prediction",
        "deterministic_rule",
        # ``bip/db/schema.py`` ``entity_label_source``: label sources are ``analyst``, ``ai`` or
        # ``signal``; ``ai`` is an AI-proposed label and ``signal`` is a machine-derived one.
        "ai",
        "ai_proposed",
        "signal",
    }
)
"""Label sources and statuses that mean the label came from a machine, never a human.

Matched case-insensitively against the label source (before any ``:`` suffix, as in
``analyst_curation:<reviewer>`` in ``bip/ml/curation.py``) and against the label status. The
human vocabulary is ``bip.ml.labels.TRAINABLE`` (``verified``, ``corroborated``,
``analyst_reviewed``). ``labels.py`` itself says model predictions are never stored as labels.
Anything else is dropped as ``unreviewed_status`` (conservative: drop when unsure)."""


@dataclass(frozen=True)
class Candidate:
    """One training example in the generic form every trainer maps to."""

    key: str
    """Unique per example."""
    entity: str
    """``chain:subject``."""
    as_of: datetime
    """When the features were taken."""
    label_time: datetime | None
    label: str | None
    value: bool | None
    label_source: str
    label_status: str
    features: Mapping[str, float | None]


@dataclass(frozen=True)
class StepResult:
    kept: tuple[Candidate, ...]
    dropped: dict[str, int]
    """Dropped count per reason."""
    samples: dict[str, tuple[str, ...]]
    """Up to 20 dropped keys per reason, in input order."""


class FilterStep(Protocol):
    @property
    def name(self) -> str: ...

    def apply(self, items: Sequence[Candidate]) -> StepResult: ...


def _result(items: Sequence[Candidate], reason_of: Callable[[Candidate], str | None]) -> StepResult:
    kept: list[Candidate] = []
    dropped: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    for c in items:
        reason = reason_of(c)
        if reason is None:
            kept.append(c)
            continue
        dropped[reason] = dropped.get(reason, 0) + 1
        bucket = samples.setdefault(reason, [])
        if len(bucket) < MAX_SAMPLES_PER_REASON:
            bucket.append(c.key)
    return StepResult(tuple(kept), dropped, {r: tuple(k) for r, k in samples.items()})


@dataclass(frozen=True)
class _Step:
    name: str
    _fn: Callable[[Sequence[Candidate]], StepResult]

    def apply(self, items: Sequence[Candidate]) -> StepResult:
        return self._fn(items)


def human_verified() -> FilterStep:
    def reason(c: Candidate) -> str | None:
        source = c.label_source.split(":", 1)[0].strip().lower()
        if source in MACHINE_ORIGIN or c.label_status.strip().lower() in MACHINE_ORIGIN:
            return "machine_origin"
        if c.label_status not in TRAINABLE:
            return "unreviewed_status"
        return None

    return _Step("human_verified", lambda items: _result(items, reason))


def point_in_time(now: datetime) -> FilterStep:
    def reason(c: Candidate) -> str | None:
        if c.label_time is not None and c.as_of > c.label_time:
            return "as_of_after_label_time"
        if c.as_of > now:
            return "as_of_in_future"
        return None

    return _Step("point_in_time", lambda items: _result(items, reason))


def finite_values() -> FilterStep:
    def reason(c: Candidate) -> str | None:
        if any(v is not None and not math.isfinite(v) for v in c.features.values()):
            return "non_finite_feature"
        return None

    return _Step("finite_values", lambda items: _result(items, reason))


def feature_completeness(max_missing_fraction: float) -> FilterStep:
    def reason(c: Candidate) -> str | None:
        n = len(c.features)
        missing = 1.0 if n == 0 else sum(v is None for v in c.features.values()) / n
        return "too_many_missing" if missing > max_missing_fraction else None

    return _Step("feature_completeness", lambda items: _result(items, reason))


def label_conflict() -> FilterStep:
    def apply(items: Sequence[Candidate]) -> StepResult:
        values: dict[tuple[str, str | None, datetime], set[bool | None]] = defaultdict(set)
        for c in items:
            values[(c.entity, c.label, c.as_of)].add(c.value)
        conflicted = {k for k, v in values.items() if len(v) > 1}
        return _result(
            items,
            lambda c: (
                "conflicting_label_value" if (c.entity, c.label, c.as_of) in conflicted else None
            ),
        )

    return _Step("label_conflict", apply)


def feature_digest(features: Mapping[str, float | None]) -> str:
    """sha256 over the sorted feature items; ``None`` is encoded as ``null``, distinct from 0."""
    payload = json.dumps(sorted(features.items()), separators=(",", ":"), allow_nan=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _newest_first(c: Candidate) -> tuple[float, str]:
    return (-c.as_of.timestamp(), c.key)


def deduplicate() -> FilterStep:
    def apply(items: Sequence[Candidate]) -> StepResult:
        winner: dict[tuple[str, str | None, bool | None, str], Candidate] = {}
        group = {c.key: (c.entity, c.label, c.value, feature_digest(c.features)) for c in items}
        for c in items:
            g = group[c.key]
            if g not in winner or _newest_first(c) < _newest_first(winner[g]):
                winner[g] = c
        return _result(items, lambda c: None if winner[group[c.key]].key == c.key else "duplicate")

    return _Step("deduplicate", apply)


def per_entity_cap(max_examples_per_entity: int) -> FilterStep:
    def apply(items: Sequence[Candidate]) -> StepResult:
        by_entity: dict[str, list[Candidate]] = defaultdict(list)
        for c in items:
            by_entity[c.entity].append(c)
        over: set[str] = set()
        for group in by_entity.values():
            group.sort(key=_newest_first)
            over.update(c.key for c in group[max_examples_per_entity:])
        return _result(items, lambda c: "over_entity_cap" if c.key in over else None)

    return _Step("per_entity_cap", apply)


def build_steps(settings: MLSettings, now: datetime) -> list[FilterStep]:
    """The enabled filters, always in pipeline order whatever order ``enabled`` lists them."""
    cfg = settings.filters
    available: dict[str, Callable[[], FilterStep]] = {
        "human_verified": human_verified,
        "point_in_time": lambda: point_in_time(now),
        "finite_values": finite_values,
        "feature_completeness": lambda: feature_completeness(cfg.max_missing_fraction),
        "label_conflict": label_conflict,
        "deduplicate": deduplicate,
        "per_entity_cap": lambda: per_entity_cap(cfg.max_examples_per_entity),
    }
    return [available[n]() for n in FILTER_NAMES if n in cfg.enabled]


def run_pipeline(
    items: Sequence[Candidate], settings: MLSettings, now: datetime
) -> tuple[list[Candidate], dict[str, Any]]:
    """Apply the enabled filters in fixed order; return the kept examples and a JSON report.

    The result is ordered by ``key`` so it does not depend on the input order.
    """
    current = tuple(sorted(items, key=lambda c: c.key))
    steps: list[dict[str, Any]] = []
    for step in build_steps(settings, now):
        res = step.apply(current)
        steps.append(
            {
                "name": step.name,
                "input": len(current),
                "dropped": sum(res.dropped.values()),
                "reasons": dict(sorted(res.dropped.items())),
                "samples": {r: list(k) for r, k in sorted(res.samples.items())},
            }
        )
        current = res.kept
    report: dict[str, Any] = {
        "filters_version": FILTERS_VERSION,
        "input": len(items),
        "output": len(current),
        "steps": steps,
    }
    return list(current), report
