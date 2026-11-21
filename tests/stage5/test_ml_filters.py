"""Training-data filters: each filter's keep and drop cases, determinism and fixed order."""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from bip.config.settings import FILTER_NAMES, MLSettings, TrainingFilterSettings
from bip.ml import filters
from bip.ml.filters import Candidate, run_pipeline

NOW = datetime(2026, 6, 1, tzinfo=UTC)
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def cand(
    key: str = "k1",
    *,
    entity: str = "ethereum:0xa",
    as_of: datetime = T0,
    label_time: datetime | None = None,
    label: str | None = "exchange",
    value: bool | None = True,
    source: str = "analyst_curation:alice",
    status: str = "analyst_reviewed",
    features: dict[str, float | None] | None = None,
) -> Candidate:
    return Candidate(
        key=key,
        entity=entity,
        as_of=as_of,
        label_time=label_time,
        label=label,
        value=value,
        label_source=source,
        label_status=status,
        features={"a": 1.0, "b": 2.0} if features is None else features,
    )


def only(*names: str) -> MLSettings:
    return MLSettings(filters=TrainingFilterSettings(enabled=names))  # type: ignore[arg-type]


def keys(items: Sequence[Candidate]) -> list[str]:
    return [c.key for c in items]


# ---------------------------------------------------------------- settings


def test_default_settings() -> None:
    s = MLSettings()
    assert s.filters.enabled == FILTER_NAMES
    assert s.filters.max_missing_fraction == 0.5
    assert s.filters.max_examples_per_entity == 5
    assert s.continuous_training_enabled is False
    assert s.continuous_interval_hours == 6
    assert s.continuous_min_new_examples == 20
    assert s.promotion_brier_tolerance == 0.005


@pytest.mark.parametrize(
    "bad",
    [
        {"max_missing_fraction": -0.1},
        {"max_missing_fraction": 1.1},
        {"max_examples_per_entity": 0},
        {"enabled": ("nonsense",)},
    ],
)
def test_settings_validation(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TrainingFilterSettings(**bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------- human_verified


@pytest.mark.parametrize(
    ("source", "status"),
    [
        ("analyst_curation:bob", "analyst_reviewed"),
        ("etherscan_labels", "verified"),
        ("some_dataset", "corroborated"),
    ],
)
def test_human_verified_keeps_human_labels(source: str, status: str) -> None:
    r = filters.human_verified().apply([cand(source=source, status=status)])
    assert keys(r.kept) == ["k1"]
    assert r.dropped == {}


@pytest.mark.parametrize(
    ("source", "status", "reason"),
    [
        ("synthetic", "verified", "machine_origin"),
        ("model_prediction", "verified", "machine_origin"),
        ("AI", "verified", "machine_origin"),
        ("ai:ollama", "verified", "machine_origin"),
        ("deterministic_rule", "verified", "machine_origin"),
        ("analyst_curation:bob", "experimental_synthetic", "machine_origin"),
        ("analyst_curation:bob", "inferred", "unreviewed_status"),
        ("analyst_curation:bob", "weak", "unreviewed_status"),
        ("analyst_curation:bob", "unknown", "unreviewed_status"),
    ],
)
def test_human_verified_drops_machine_labels(source: str, status: str, reason: str) -> None:
    r = filters.human_verified().apply([cand(source=source, status=status)])
    assert r.kept == ()
    assert r.dropped == {reason: 1}
    assert r.samples == {reason: ("k1",)}


# ---------------------------------------------------------------- point_in_time


def test_point_in_time_label_time() -> None:
    items = [
        cand("equal", as_of=T0, label_time=T0),
        cand("before", as_of=T0, label_time=T0 + timedelta(days=1)),
        cand("after", as_of=T0 + timedelta(days=1), label_time=T0),
        cand("nolabeltime", label_time=None),
    ]
    r = filters.point_in_time(NOW).apply(items)
    assert keys(list(r.kept)) == ["equal", "before", "nolabeltime"]
    assert r.dropped == {"as_of_after_label_time": 1}


def test_point_in_time_future_as_of_uses_injected_clock() -> None:
    items = [cand("ok", as_of=NOW), cand("future", as_of=NOW + timedelta(seconds=1))]
    r = filters.point_in_time(NOW).apply(items)
    assert keys(list(r.kept)) == ["ok"]
    assert r.dropped == {"as_of_in_future": 1}
    later = filters.point_in_time(NOW + timedelta(days=1)).apply(items)
    assert keys(list(later.kept)) == ["ok", "future"]


# ---------------------------------------------------------------- finite_values


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_finite_values_drops_non_finite(bad: float) -> None:
    r = filters.finite_values().apply([cand("bad", features={"a": 1.0, "b": bad})])
    assert r.kept == ()
    assert r.dropped == {"non_finite_feature": 1}


def test_finite_values_allows_none_and_zero() -> None:
    items = [cand("none", features={"a": None, "b": 1.0}), cand("zero", features={"a": 0.0})]
    r = filters.finite_values().apply(items)
    assert keys(list(r.kept)) == ["none", "zero"]


# ---------------------------------------------------------------- feature_completeness


def test_feature_completeness_threshold_is_strictly_greater() -> None:
    items = [
        cand("half", features={"a": None, "b": 1.0}),  # 0.5 missing: kept at 0.5
        cand("most", features={"a": None, "b": None, "c": 1.0}),  # 0.67: dropped
        cand("full", features={"a": 1.0, "b": 2.0}),
    ]
    r = filters.feature_completeness(0.5).apply(items)
    assert keys(list(r.kept)) == ["half", "full"]
    assert r.dropped == {"too_many_missing": 1}


def test_feature_completeness_none_is_missing_but_zero_is_present() -> None:
    zeros = cand("zeros", features={"a": 0.0, "b": 0.0})
    nones = cand("nones", features={"a": None, "b": None})
    r = filters.feature_completeness(0.0).apply([zeros, nones])
    assert keys(list(r.kept)) == ["zeros"]


def test_feature_completeness_empty_map_is_fully_missing() -> None:
    r = filters.feature_completeness(0.99).apply([cand("empty", features={})])
    assert r.kept == ()
    r_all = filters.feature_completeness(1.0).apply([cand("empty", features={})])
    assert keys(list(r_all.kept)) == ["empty"]


# ---------------------------------------------------------------- label_conflict


def test_label_conflict_drops_all_disagreeing_examples() -> None:
    items = [
        cand("a", value=True),
        cand("b", value=False),
        cand("c", value=True),
        cand("other_time", as_of=T0 + timedelta(days=1), value=False),
        cand("other_label", label="mixer", value=False),
        cand("other_entity", entity="ethereum:0xb", value=False),
    ]
    r = filters.label_conflict().apply(items)
    assert keys(list(r.kept)) == ["other_time", "other_label", "other_entity"]
    assert r.dropped == {"conflicting_label_value": 3}


def test_label_conflict_agreeing_duplicates_kept() -> None:
    r = filters.label_conflict().apply([cand("a"), cand("b")])
    assert keys(list(r.kept)) == ["a", "b"]


def test_label_conflict_none_value_differs_from_false() -> None:
    r = filters.label_conflict().apply([cand("a", value=None), cand("b", value=False)])
    assert r.kept == ()


# ---------------------------------------------------------------- deduplicate


def test_deduplicate_keeps_newest_as_of_then_lowest_key() -> None:
    same = {"a": 1.0, "b": None}
    items = [
        cand("z_old", as_of=T0, features=same),
        cand("z_new", as_of=T0 + timedelta(days=1), features=same),
        cand("y_new", as_of=T0 + timedelta(days=1), features=same),
    ]
    r = filters.deduplicate().apply(items)
    assert keys(list(r.kept)) == ["y_new"]
    assert r.dropped == {"duplicate": 2}
    assert r.samples == {"duplicate": ("z_old", "z_new")}


def test_deduplicate_distinguishes_none_from_zero() -> None:
    items = [cand("none", features={"a": None}), cand("zero", features={"a": 0.0})]
    r = filters.deduplicate().apply(items)
    assert keys(list(r.kept)) == ["none", "zero"]


def test_deduplicate_digest_ignores_feature_insertion_order() -> None:
    items = [cand("a", features={"x": 1.0, "y": 2.0}), cand("b", features={"y": 2.0, "x": 1.0})]
    assert keys(list(filters.deduplicate().apply(items).kept)) == ["a"]


def test_deduplicate_different_value_label_or_entity_kept() -> None:
    items = [
        cand("a"),
        cand("b", value=False),
        cand("c", label="mixer"),
        cand("d", entity="ethereum:0xb"),
    ]
    assert len(filters.deduplicate().apply(items).kept) == 4


# ---------------------------------------------------------------- per_entity_cap


def test_per_entity_cap_keeps_newest_per_entity() -> None:
    items = [
        cand(f"a{i}", as_of=T0 + timedelta(days=i), features={"a": float(i)}) for i in range(4)
    ] + [cand("b", entity="ethereum:0xb")]
    r = filters.per_entity_cap(2).apply(items)
    assert keys(list(r.kept)) == ["a2", "a3", "b"]
    assert r.dropped == {"over_entity_cap": 2}
    assert r.samples == {"over_entity_cap": ("a0", "a1")}


def test_per_entity_cap_tie_goes_to_lowest_key() -> None:
    items = [cand("b"), cand("a"), cand("c")]
    r = filters.per_entity_cap(1).apply(items)
    assert keys(list(r.kept)) == ["a"]
    assert r.samples == {"over_entity_cap": ("b", "c")}


# ---------------------------------------------------------------- samples


def test_samples_capped_at_20_in_input_order() -> None:
    items = [cand(f"k{i:02d}", features={"a": float("nan")}) for i in range(30)]
    r = filters.finite_values().apply(items)
    assert r.dropped == {"non_finite_feature": 30}
    assert r.samples["non_finite_feature"] == tuple(f"k{i:02d}" for i in range(20))


# ---------------------------------------------------------------- pipeline


def mixed() -> list[Candidate]:
    return [
        cand("k1", entity="ethereum:0xa", as_of=T0),
        cand("k2", entity="ethereum:0xa", as_of=T0 + timedelta(days=1), features={"a": 3.0}),
        cand("k3", entity="ethereum:0xb", source="synthetic"),
        cand("k4", entity="ethereum:0xc", features={"a": float("nan")}),
        cand("k5", entity="ethereum:0xd", features={"a": None, "b": None}),
        cand("k6", entity="ethereum:0xe", value=True),
        cand("k7", entity="ethereum:0xe", value=False),
        cand("k8", entity="ethereum:0xf", as_of=NOW + timedelta(days=1)),
        cand("k9", entity="ethereum:0xg", features={"a": 5.0}),
        cand("k10", entity="ethereum:0xg", features={"a": 5.0}),
    ]


def test_pipeline_report_shape_and_counts() -> None:
    kept, report = run_pipeline(mixed(), MLSettings(), NOW)
    assert report["filters_version"] == "1"
    assert report["input"] == 10
    assert report["output"] == len(kept) == 3
    assert [s["name"] for s in report["steps"]] == list(FILTER_NAMES)
    by = {s["name"]: s for s in report["steps"]}
    assert by["human_verified"]["input"] == 10
    assert by["human_verified"]["dropped"] == 1
    assert by["human_verified"]["reasons"] == {"machine_origin": 1}
    assert by["human_verified"]["samples"] == {"machine_origin": ["k3"]}
    assert by["point_in_time"]["reasons"] == {"as_of_in_future": 1}
    assert by["finite_values"]["dropped"] == 1
    assert by["feature_completeness"]["dropped"] == 1
    assert by["label_conflict"]["dropped"] == 2
    assert by["deduplicate"]["dropped"] == 1
    assert by["per_entity_cap"]["dropped"] == 0
    assert keys(kept) == ["k1", "k10", "k2"]
    json.dumps(report)  # JSON-serialisable


def test_pipeline_is_deterministic_under_shuffle() -> None:
    items = mixed()
    base_kept, base_report = run_pipeline(items, MLSettings(), NOW)
    for seed in range(5):
        shuffled = items[:]
        random.Random(seed).shuffle(shuffled)  # noqa: S311
        kept, report = run_pipeline(shuffled, MLSettings(), NOW)
        assert sorted(keys(kept)) == sorted(keys(base_kept))
        assert report == base_report


def test_pipeline_does_not_mutate_input() -> None:
    items = mixed()
    before = items[:]
    run_pipeline(items, MLSettings(), NOW)
    assert items == before


def test_pipeline_order_fixed_when_enabled_is_reordered() -> None:
    reordered = tuple(reversed(FILTER_NAMES))
    s = MLSettings(filters=TrainingFilterSettings(enabled=reordered))
    _, report = run_pipeline(mixed(), s, NOW)
    assert [x["name"] for x in report["steps"]] == list(FILTER_NAMES)
    _, default = run_pipeline(mixed(), MLSettings(), NOW)
    assert report == default


def test_pipeline_skips_disabled_filters() -> None:
    _, report = run_pipeline(mixed(), only("finite_values", "human_verified"), NOW)
    assert [x["name"] for x in report["steps"]] == ["human_verified", "finite_values"]
    kept, _ = run_pipeline(mixed(), only(), NOW)
    assert len(kept) == 10


def test_pipeline_uses_settings_thresholds() -> None:
    s = MLSettings(filters=TrainingFilterSettings(max_examples_per_entity=1))
    items = [cand("a", as_of=T0, features={"a": 1.0}), cand("b", as_of=T0 + timedelta(days=1))]
    kept, _ = run_pipeline(items, s, NOW)
    assert keys(kept) == ["b"]
