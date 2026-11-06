"""Wallet classification service: features → rules + active model → validated contract.

Status vocabulary for every reported label:

* ``high_confidence`` / ``moderate_confidence``: a calibrated model score at or above a
  threshold whose validation precision met the configured target (≥ 0.95 / ≥ 0.80) with
  enough support;
* ``low_confidence``: score ≥ 0.5 but no validated threshold reached (or an uncalibrated
  model);
* ``rule_based``: a deterministic, evidence-backed rule fired (precision not measured, no
  probability);
* ``insufficient_evidence``: too little observed activity to classify.

A prediction is never stated as fact. Models trained on synthetic data are marked
``experimental_synthetic`` on every label they produce.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import insert, select

from bip.config.settings import Settings
from bip.db import schema
from bip.db.errors import RecordNotFoundError
from bip.db.repository import Repository, canonical_address
from bip.db.types import utcnow
from bip.intel.dataset import load_wallet
from bip.ml import rules, runtime, taxonomy
from bip.ml.features import FEATURE_NAMES, FEATURE_VERSION, FeatureVector, extract

J = dict[str, Any]
LabelStatus = Literal[
    "high_confidence",
    "moderate_confidence",
    "low_confidence",
    "rule_based",
    "insufficient_evidence",
]
SENSITIVE_CAVEAT = (
    "Behavioural resemblance only: no operator, owner or intent is established; "
    "independently attributable evidence would be required."
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LabelResult(_Strict):
    label: str
    status: LabelStatus
    confidence: Annotated[float, Field(ge=0, le=1)] | None
    """Calibrated model score; ``None`` for rule-based or insufficient-evidence labels."""
    source: str
    model_status: str | None = None
    calibrated: bool | None = None
    explanation: str
    evidence: tuple[J, ...] = ()
    supporting_features: tuple[J, ...] = ()
    caveat: str | None = None


class ModelRef(_Strict):
    name: str
    version: str
    feature_version: str
    status: str
    data_kind: str
    # training dataset hash; None for the rules baseline (no training data)
    dataset_version: str | None = None


class ClassificationResult(_Strict):
    classification_id: str
    wallet_address: str
    chain: Literal["ethereum", "solana"]
    classification_timestamp: str
    as_of: str
    status: Literal["classified", "classified_with_limitations", "insufficient_evidence"]
    taxonomy_version: str
    feature_version: str
    feature_digest: str
    rules: ModelRef
    model: ModelRef | None
    account_type: LabelResult
    behavioural_roles: tuple[LabelResult, ...]
    not_assigned: tuple[J, ...]
    contradictions: tuple[J, ...]
    anomaly: J
    coverage: J
    supporting_evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...]
    data_origin: str


class ClassificationService:
    def __init__(self, repo: Repository, settings: Settings) -> None:
        self._repo = repo
        self._settings = settings
        self._cache: dict[tuple[str, str], J] = {}

    # ------------------------------------------------------------------ model registry

    def active_model(self, chain: str) -> J | None:
        """Latest ``trained`` model for the chain, else the latest ``experimental_synthetic``."""
        m = schema.ml_models
        with self._repo.transaction() as conn:
            for status in ("trained", "experimental_synthetic"):
                row = (
                    conn.execute(
                        select(m)
                        .where(m.c.chain == chain, m.c.status == status)
                        .order_by(m.c.id.desc())
                    )
                    .mappings()
                    .first()
                )
                if row is not None:
                    return _model_row(row)
        return None

    def _artifact(self, model: J) -> J:
        key = (model["name"], model["version"])
        if key not in self._cache:
            root = self._settings.resolved_data_root
            self._cache[key] = runtime.backend().load(
                root / model["artifact_path"], model["artifact_sha256"], root / "ml" / "models"
            )
        return self._cache[key]

    def models(self) -> list[J]:
        m = schema.ml_models
        with self._repo.transaction() as conn:
            rows = conn.execute(select(m).order_by(m.c.id.desc())).mappings().all()
        return [_model_row(r) for r in rows]

    def model(self, name: str, version: str) -> J:
        m, e = schema.ml_models, schema.ml_evaluation_results
        with self._repo.transaction() as conn:
            row = (
                conn.execute(select(m).where(m.c.name == name, m.c.version == version))
                .mappings()
                .first()
            )
            if row is None:
                raise RecordNotFoundError(f"model {name} {version} not found")
            evals = (
                conn.execute(
                    select(e)
                    .where(e.c.model_name == name, e.c.model_version == version)
                    .order_by(e.c.id)
                )
                .mappings()
                .all()
            )
        return {
            **_model_row(row),
            "evaluation": [
                {
                    "split": r["split"],
                    "label": r["label"],
                    "data_kind": r["data_kind"],
                    "metrics": json.loads(r["payload_json"]),
                }
                for r in evals
            ],
        }

    def training_runs(self, limit: int) -> list[J]:
        t = schema.ml_training_runs
        with self._repo.transaction() as conn:
            rows = conn.execute(select(t).order_by(t.c.id.desc()).limit(limit)).mappings().all()
        return [
            {
                "training_run_id": r["id"],
                "model_name": r["model_name"],
                "chain": r["chain"],
                "started_at": _iso(r["started_at"]),
                "finished_at": _iso(r["finished_at"]),
                "status": r["status"],
                "data_kind": r["data_kind"],
                "dataset_version": r["dataset_version"],
                "dataset": json.loads(r["dataset_json"]) if r["dataset_json"] else None,
                "params": json.loads(r["params_json"]),
                "message": r["message"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ classification

    def classify(self, chain: str, address: str, as_of: datetime | None = None) -> J:
        ref = canonical_address(chain, address)
        ds = load_wallet(
            self._repo, ref.chain, ref.address, self._settings.intel.max_records_per_wallet
        )
        at = as_of or utcnow()
        fv = extract(ds, at)
        self._store_features(fv)
        acct = rules.account_type(self._repo, ds)
        rule_roles = rules.roles(self._repo, ds, fv, at)
        model = self.active_model(ref.chain)
        limitations = [
            "Classification is based only on activity BIP has retrieved (not lifetime history)."
        ]
        if model is not None and not runtime.ml_runtime().available:
            limitations.append(
                f"A trained model ({model['name']} {model['version']}) is registered, but the "
                "ML runtime (numpy, scikit-learn) is not installed in this environment: only "
                "the deterministic rules baseline ran. No model prediction is shown."
            )
            model = None
        if ref.chain == "ethereum":
            limitations.append(
                "Ethereum activity is discovered via Transfer logs; plain ETH "
                "transfers and internal transfers can be missing."
            )
        enough = (fv.values["tx_count"] or 0) >= self._settings.ml.min_tx_for_classification
        model_roles: list[J] = []
        not_assigned: list[J] = []
        anomaly: J = {"status": "not_available", "reason": "no trained anomaly model"}
        if model is None:
            limitations.append(
                "No trained model is registered for this chain: only "
                "deterministic rules were applied."
            )
        else:
            if model["status"] == "experimental_synthetic":
                limitations.append(
                    "The active model was trained on SYNTHETIC data. Its "
                    "predictions demonstrate the pipeline only and are not "
                    "evidence about this wallet."
                )
            if not enough:
                limitations.append(
                    f"Fewer than {self._settings.ml.min_tx_for_classification} "
                    "observed transactions: model roles abstained."
                )
            else:
                model_roles, not_assigned, anomaly = self._predict(model, fv)
        roles = _merge(rule_roles, model_roles)
        contradictions = _contradictions(rule_roles, model_roles, fv)
        for r in roles:
            if taxonomy.get(r["label"]).sensitive:
                r["caveat"] = SENSITIVE_CAVEAT
        if not ds.transactions and not ds.transfers:
            status = "insufficient_evidence"
            limitations.append("No stored observations for this address.")
        elif not enough or model is None or model["status"] != "trained" or contradictions:
            status = "classified_with_limitations"
        else:
            status = "classified"
        result = ClassificationResult(
            classification_id=str(uuid.uuid4()),
            wallet_address=ref.address,
            chain=ref.chain,  # type: ignore[arg-type]
            classification_timestamp=utcnow().isoformat(),
            as_of=at.isoformat(),
            status=status,
            taxonomy_version=taxonomy.TAXONOMY_VERSION,
            feature_version=FEATURE_VERSION,
            feature_digest=fv.digest(),
            rules=ModelRef(
                name=rules.RULES_NAME,
                version=rules.RULES_VERSION,
                feature_version=FEATURE_VERSION,
                status="rule_based",
                data_kind="stored_observations",
            ),
            model=ModelRef(
                name=model["name"],
                version=model["version"],
                feature_version=model["feature_version"],
                status=model["status"],
                data_kind=model["data_kind"],
                dataset_version=model.get("dataset_version"),
            )
            if model
            else None,
            account_type=LabelResult(**acct),
            behavioural_roles=tuple(LabelResult(**r) for r in roles),
            not_assigned=tuple(not_assigned),
            contradictions=tuple(contradictions),
            anomaly=anomaly,
            coverage=fv.coverage | {"window": fv.window},
            supporting_evidence_refs=tuple(fv.record_ids[:20]),
            limitations=tuple(limitations),
            data_origin=self._repo.get_meta("data_origin") or "stored_observations",
        )
        payload = result.model_dump(mode="json")
        with self._repo.transaction() as conn:
            conn.execute(
                insert(schema.wallet_classifications).values(
                    classification_key=result.classification_id,
                    chain=ref.chain,
                    address=ref.address,
                    classified_at=utcnow(),
                    as_of=at,
                    feature_version=FEATURE_VERSION,
                    feature_digest=fv.digest(),
                    models_json=json.dumps([payload["rules"], payload["model"]]),
                    status=status,
                    data_origin=result.data_origin,
                    payload_json=json.dumps(payload),
                )
            )
        return payload

    def _predict(self, model: J, fv: FeatureVector) -> tuple[list[J], list[J], J]:
        art = self._artifact(model)
        be = runtime.backend()
        x, _ = be.matrix([fv.values], FEATURE_NAMES, art["medians"])
        roles: list[J] = []
        not_assigned: list[J] = []
        for lab, spec in sorted(art["labels"].items()):
            est, thr, calibrated = spec["estimator"], spec["thresholds"], spec["calibrated"]
            score = float(est.predict_proba(x)[0, 1])
            status: str | None = None
            if calibrated and thr.get("high") is not None and score >= thr["high"]:
                status = "high_confidence"
            elif calibrated and thr.get("moderate") is not None and score >= thr["moderate"]:
                status = "moderate_confidence"
            elif score >= 0.5:
                status = "low_confidence"
            if status is None:
                not_assigned.append({"label": lab, "score": round(score, 4)})
                continue
            capped = model["status"] != "trained" and status != "low_confidence"
            if capped:  # thresholds measured on synthetic data say nothing about real wallets
                status = "low_confidence"
            roles.append(
                {
                    "label": lab,
                    "status": status,
                    "confidence": round(score, 4),
                    "source": f"{model['name']}@{model['version']}",
                    "model_status": model["status"],
                    "calibrated": calibrated,
                    "explanation": (
                        (
                            "Capped at low confidence: thresholds were validated on synthetic "
                            "data only. "
                            if capped
                            else ""
                        )
                        + f"Model score {score:.2f}; thresholds high="
                        f"{_fmt(thr.get('high'))}, moderate={_fmt(thr.get('moderate'))} "
                        f"({thr.get('basis')}). A prediction, not a fact."
                    ),
                    "evidence": [
                        {"type": "feature_vector", "ref": fv.digest(), "note": "model input"}
                    ],
                    "supporting_features": _top_features(est, fv),
                }
            )
        not_assigned.sort(key=lambda d: -d["score"])
        anomaly: J = {"status": "not_available", "reason": "model has no anomaly detector"}
        if art.get("anomaly") is not None:
            s = be.anomaly_scores(art["anomaly"], x)[0]
            ref = art["anomaly_reference"]
            pct = sum(1 for r in ref if r <= s) / len(ref) if ref else None
            anomaly = {
                "status": "scored",
                "score": round(s, 4),
                "training_percentile": round(pct, 4) if pct is not None else None,
                "outlier": bool(pct is not None and pct >= 0.95),
                "model": "IsolationForest",
                "note": "Unusual relative to the model's training wallets; not evidence "
                "of wrongdoing.",
            }
        return roles, not_assigned[:5], anomaly

    def _store_features(self, fv: FeatureVector) -> None:
        f = schema.wallet_features
        with self._repo.transaction() as conn:
            exists = conn.execute(
                select(f.c.id).where(
                    f.c.chain == fv.chain,
                    f.c.address == fv.address,
                    f.c.feature_version == fv.feature_version,
                    f.c.digest == fv.digest(),
                )
            ).first()
            if exists is None:
                conn.execute(
                    insert(f).values(
                        chain=fv.chain,
                        address=fv.address,
                        feature_version=fv.feature_version,
                        as_of=fv.as_of,
                        digest=fv.digest(),
                        data_kind=fv.data_kind,
                        payload_json=json.dumps(fv.public()),
                        created_at=utcnow(),
                    )
                )

    def history(self, chain: str, address: str, limit: int) -> list[J]:
        ref = canonical_address(chain, address)
        c = schema.wallet_classifications
        with self._repo.transaction() as conn:
            rows = conn.execute(
                select(c.c.payload_json)
                .where(c.c.chain == ref.chain, c.c.address == ref.address)
                .order_by(c.c.id.desc())
                .limit(limit)
            ).all()
        return [json.loads(r[0]) for r in rows]

    def get(self, classification_id: str) -> J:
        c = schema.wallet_classifications
        with self._repo.transaction() as conn:
            row = conn.execute(
                select(c.c.payload_json).where(c.c.classification_key == classification_id)
            ).first()
        if row is None:
            raise RecordNotFoundError(f"classification {classification_id} not found")
        return json.loads(row[0])

    def latest_features(self, chain: str, address: str) -> J | None:
        ref = canonical_address(chain, address)
        f = schema.wallet_features
        with self._repo.transaction() as conn:
            row = conn.execute(
                select(f.c.payload_json)
                .where(f.c.chain == ref.chain, f.c.address == ref.address)
                .order_by(f.c.id.desc())
            ).first()
        return json.loads(row[0]) if row else None


def _merge(rule_roles: list[J], model_roles: list[J]) -> list[J]:
    out = list(rule_roles)
    seen = {r["label"] for r in rule_roles}
    for r in model_roles:
        if r["label"] in seen:  # rule and model agree: keep both views, rule first
            r = {**r, "explanation": r["explanation"] + " A deterministic rule also matched."}
        out.append(r)
    return out


def _contradictions(rule_roles: list[J], model_roles: list[J], fv: FeatureVector) -> list[J]:
    out: list[J] = []
    rule_labels = {r["label"] for r in rule_roles}
    model_labels = {r["label"]: r for r in model_roles}
    if "low_activity_or_new" in rule_labels and "high_frequency_activity" in model_labels:
        out.append(
            {
                "between": [
                    "bip-rules:low_activity_or_new",
                    f"{model_labels['high_frequency_activity']['source']}:high_frequency_activity",
                ],
                "note": "The rule sees low/new activity while the model predicts high "
                "frequency; both are kept and neither is preferred automatically.",
            }
        )
    if "high_frequency_activity" in rule_labels and "long_term_holder" in model_labels:
        out.append(
            {
                "between": [
                    "bip-rules:high_frequency_activity",
                    f"{model_labels['long_term_holder']['source']}:long_term_holder",
                ],
                "note": "High observed frequency conflicts with a long-term-holder prediction.",
            }
        )
    _ = fv
    return out


def _top_features(est: Any, fv: FeatureVector) -> list[J]:
    forest = est
    if hasattr(est, "calibrated_classifiers_"):  # CalibratedClassifierCV(FrozenEstimator(rf))
        forest = getattr(est.estimator, "estimator", est.estimator)
    importances = getattr(forest, "feature_importances_", None)
    if importances is None:
        return []
    n = len(FEATURE_NAMES)
    combined = [float(importances[i]) + float(importances[i + n]) for i in range(n)]
    order = sorted(range(n), key=lambda i: -combined[i])[:5]
    return [
        {
            "feature": FEATURE_NAMES[i],
            "importance": round(combined[i], 4),
            "value": fv.values[FEATURE_NAMES[i]],
            "availability": fv.availability[FEATURE_NAMES[i]],
        }
        for i in order
    ]


def _fmt(v: float | None) -> str:
    return "none (not validated)" if v is None else f"{v:.2f}"


def _model_row(r: Any) -> J:
    return {
        "name": r["name"],
        "version": r["version"],
        "kind": r["kind"],
        "chain": r["chain"],
        "status": r["status"],
        "feature_version": r["feature_version"],
        "taxonomy_version": r["taxonomy_version"],
        "dataset_version": r["dataset_version"],
        "data_kind": r["data_kind"],
        "artifact_path": r["artifact_path"],
        "artifact_sha256": r["artifact_sha256"],
        "params": json.loads(r["params_json"]),
        "labels": json.loads(r["labels_json"]),
        "thresholds": json.loads(r["thresholds_json"]),
        "training_run_id": r["training_run_id"],
        "created_at": _iso(r["created_at"]),
        "note": r["note"],
    }


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None
