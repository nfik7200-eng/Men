"""Explainable scoring of candidate signals.

Every signal carries the components below, so a reader can see exactly why it scored as it did.

* ``magnitude`` (weight 0.5): how far the observation is from what is normal. For median/MAD
  detectors it is ``z / (3 * threshold)`` clamped to 0..1, so a robust z equal to the threshold
  gives 0.33 and three times the threshold gives 1.0. Count-based detectors define their own and
  say how.
* ``baseline_reliability`` (weight 0.3): how much history the comparison rests on,
  ``n / 50`` clamped to 0..1. A baseline of ten observations is weak evidence of "normal".
* ``evidence_finality`` (weight 0.2): the share of the supporting transactions that are finalized.
  Provisional evidence lowers both the score and the confidence.

``score = 100 * sum(weight * value)``. Severity is ``high`` from 65, ``medium`` from 40, otherwise
``low``. Confidence is ``baseline_reliability * (0.5 + 0.5 * finality)`` limited by a per-detector
cap. The cap is below 1 for every detector, because stored history is partial and a signal is a
statistical or observational finding, never a verified fact about intent or identity.
"""

from __future__ import annotations

from dataclasses import dataclass

WEIGHTS: dict[str, float] = {
    "magnitude": 0.5,
    "baseline_reliability": 0.3,
    "evidence_finality": 0.2,
}
HIGH_SCORE = 65.0
MEDIUM_SCORE = 40.0


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def magnitude_from_z(z: float, threshold: float) -> float:
    """Magnitude of a robust z-score relative to the detector's threshold (see module docs)."""
    return clamp(z / (3.0 * threshold))


def reliability_from_count(n: int) -> float:
    """Baseline reliability from the number of baseline observations."""
    return clamp(n / 50.0)


def severity_for(score: float) -> str:
    if score >= HIGH_SCORE:
        return "high"
    if score >= MEDIUM_SCORE:
        return "medium"
    return "low"


@dataclass(frozen=True, slots=True)
class Score:
    score: float
    confidence: float
    severity: str
    components: list[dict[str, object]]


def score_signal(
    *,
    magnitude: float,
    baseline_reliability: float,
    evidence_finality: float,
    confidence_cap: float,
    notes: dict[str, str] | None = None,
) -> Score:
    values = {
        "magnitude": clamp(magnitude),
        "baseline_reliability": clamp(baseline_reliability),
        "evidence_finality": clamp(evidence_finality),
    }
    notes = notes or {}
    components: list[dict[str, object]] = []
    total = 0.0
    for name, weight in WEIGHTS.items():
        contribution = 100.0 * weight * values[name]
        total += contribution
        components.append(
            {
                "name": name,
                "value": round(values[name], 4),
                "weight": weight,
                "contribution": round(contribution, 2),
                "note": notes.get(name, ""),
            }
        )
    confidence = values["baseline_reliability"] * (0.5 + 0.5 * values["evidence_finality"])
    confidence = round(min(confidence, clamp(confidence_cap)), 4)
    total = round(total, 2)
    return Score(
        score=total, confidence=confidence, severity=severity_for(total), components=components
    )
