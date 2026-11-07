"""Reproducible training pipeline for wallet classifiers.

``train(chain, source)``:

1. **Examples.** ``stored_labels`` uses real labels with provenance. Features are extracted
   from stored observations *as of each label's* ``observed_at``, so there is no leakage.
   ``synthetic`` uses :mod:`bip.ml.synthetic` and is allowed only when
   ``BIP_ML__ALLOW_SYNTHETIC_TRAINING`` is true.
2. **Data gate.** Real data needs ≥ ``min_labelled_wallets`` labelled wallets. Each label
   needs ≥ ``min_positives_per_label`` positive *and* negative training examples. Otherwise
   the run is recorded as ``insufficient_data`` and no model is produced.
3. **Explicit labels only.** A wallet is an example for a label only if that label was stated
   for it (true or false). The absence of a label is never treated as a negative.
4. **Dataset version.** sha256 over every (wallet, as_of, label, value, status, source) and
   every feature digest.
5. **Out-of-time split.** Examples are ordered by ``as_of``: the oldest 60 % train, the next
   20 % validate (calibration and thresholds), the newest 20 % test.
6. **Training.** Per-label random forests (:mod:`bip.ml.backend`), an Isolation Forest for
   behavioural outliers, and an HDBSCAN cluster summary (informational).
7. **Registration.** The model goes into the registry with its artifact hash, thresholds,
   provenance and per-split evaluation. A model trained on synthetic data is registered as
   ``experimental_synthetic``.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, insert, select, update

from bip.config.settings import Settings
from bip.db import schema
from bip.db.errors import InvalidRecordError
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.intel.dataset import load_wallet
from bip.ml import runtime, synthetic, taxonomy
from bip.ml.features import FEATURE_NAMES, FEATURE_VERSION, FeatureVector, extract
from bip.ml.labels import LabelStore

if TYPE_CHECKING:
    from bip.ml.backend import LabelModel

J = dict[str, Any]
MODEL_PREFIX = "wallet-classifier"
SYNTHETIC_NOTE = (
    "Trained on SYNTHETIC data: demonstrates the pipeline only; metrics are not "
    "real-world performance."
)


@dataclass
class Example:
    fv: FeatureVector
    labels: dict[str, bool]
    statuses: dict[str, str]
    sources: dict[str, str]
    conflicts: set[str] = field(default_factory=set[str])


def model_root(settings: Settings) -> Path:
    return settings.resolved_data_root / "ml" / "models"


class Trainer:
    def __init__(self, repo: Repository, settings: Settings) -> None:
        self._repo = repo
        self._settings = settings
        self._cfg = settings.ml

    # ------------------------------------------------------------------ examples

    def _synthetic(self, chain: str, seed: int) -> list[Example]:
        out: list[Example] = []
        for w in synthetic.generate(seed=seed, chain=chain):
            out.append(
                Example(
                    extract(w.dataset, w.as_of, data_kind="synthetic"),
                    w.labels,
                    dict.fromkeys(w.labels, "synthetic"),
                    dict.fromkeys(w.labels, "bip.ml.synthetic"),
                )
            )
        return out

    def _stored(self, chain: str) -> list[Example]:
        groups: dict[tuple[str, str], Example] = {}
        rows = LabelStore(self._repo).training_rows(chain, "real")
        for r in rows:
            key = (r["address"], r["observed_at"])
            if key not in groups:
                from datetime import datetime

                as_of = datetime.fromisoformat(r["observed_at"])
                ds = load_wallet(
                    self._repo, chain, r["address"], self._settings.intel.max_records_per_wallet
                )
                groups[key] = Example(extract(ds, as_of), {}, {}, {})
            ex = groups[key]
            lab, value = r["label"], bool(r["value"])
            if lab in ex.labels and ex.labels[lab] != value:
                ex.conflicts.add(lab)  # sources disagree: the label is dropped, not guessed
            ex.labels[lab] = value
            ex.statuses[lab] = r["status"]
            ex.sources[lab] = r["source"]
        out = list(groups.values())
        for ex in out:
            for lab in ex.conflicts:
                for d in (ex.labels, ex.statuses, ex.sources):
                    d.pop(lab, None)
        return [ex for ex in out if ex.labels]

    # ------------------------------------------------------------------ training

    def train(self, chain: str, source: str, *, seed: int = 7) -> J:
        if source == "synthetic" and not self._cfg.allow_synthetic_training:
            raise InvalidRecordError(
                "synthetic training is disabled (BIP_ML__ALLOW_SYNTHETIC_TRAINING=false)"
            )
        if source not in {"synthetic", "stored_labels"}:
            raise InvalidRecordError("source must be 'stored_labels' or 'synthetic'")
        backend = runtime.backend()  # raises MLUnavailableError before any run is recorded
        data_kind = "synthetic" if source == "synthetic" else "real"
        name = f"{MODEL_PREFIX}-{chain}"
        params: J = {
            "seed": seed,
            "source": source,
            "split": [0.6, 0.2, 0.2],
            "model": "RandomForestClassifier(n_estimators=150, max_depth=8, "
            "min_samples_leaf=2, class_weight=balanced_subsample)",
            "calibration": "sigmoid on validation split when >=10 pos and >=10 neg",
            "feature_version": FEATURE_VERSION,
            "taxonomy_version": taxonomy.TAXONOMY_VERSION,
            "high_precision_target": self._cfg.high_precision_target,
            "moderate_precision_target": self._cfg.moderate_precision_target,
            "min_threshold_support": self._cfg.min_threshold_support,
        }
        run_id = self._start(name, chain, data_kind, params)
        random.seed(seed)
        examples = self._synthetic(chain, seed) if source == "synthetic" else self._stored(chain)
        wallets = len({e.fv.address for e in examples})
        if data_kind == "real" and wallets < self._cfg.min_labelled_wallets:
            return self._finish(
                run_id,
                "insufficient_data",
                None,
                None,
                f"{wallets} distinct labelled wallet(s) with trainable labels "
                f"({len(examples)} snapshots); at least {self._cfg.min_labelled_wallets} "
                "distinct wallets are needed. "
                "No model was trained.",
            )
        # Split by wallet, ordered by each wallet's first labelled time (out-of-time): every
        # snapshot of one wallet lands in the same split, so no wallet is in train and test.
        first: dict[str, Any] = {}
        for e in examples:
            first[e.fv.address] = min(first.get(e.fv.address, e.fv.as_of), e.fv.as_of)
        order = sorted(first, key=lambda addr: (first[addr], addr))
        na = len(order)
        cut_a, cut_b = int(na * 0.6), int(na * 0.8)
        split_of = {
            addr: "train" if i < cut_a else "validation" if i < cut_b else "test"
            for i, addr in enumerate(order)
        }
        examples.sort(key=lambda e: (e.fv.as_of, e.fv.address))
        n = len(examples)
        a, b = int(n * 0.6), int(n * 0.8)
        splits = {
            k: [e for e in examples if split_of[e.fv.address] == k]
            for k in ("train", "validation", "test")
        }
        _ = (a, b)
        version_src = hashlib.sha256()
        for e in examples:
            version_src.update(e.fv.digest().encode())
            for lab in sorted(e.labels):
                version_src.update(
                    f"|{e.fv.address}|{lab}|{e.labels[lab]}|"
                    f"{e.statuses[lab]}|{e.sources[lab]}".encode()
                )
        dataset_version = version_src.hexdigest()
        dataset: J = {
            "examples": n,
            "wallets": wallets,
            "split_unit": "wallet (no wallet appears in two splits)",
            "conflicting_labels_dropped": sum(len(e.conflicts) for e in examples),
            "data_kind": data_kind,
            "splits": {
                k: {
                    "n": len(v),
                    "as_of_from": v[0].fv.as_of.isoformat() if v else None,
                    "as_of_to": v[-1].fv.as_of.isoformat() if v else None,
                }
                for k, v in splits.items()
            },
            "label_statuses": _count(s for e in examples for s in e.statuses.values()),
        }
        x_tr, med = backend.matrix([e.fv.values for e in splits["train"]], FEATURE_NAMES)
        x_va, _ = backend.matrix([e.fv.values for e in splits["validation"]], FEATURE_NAMES, med)
        x_te, _ = backend.matrix([e.fv.values for e in splits["test"]], FEATURE_NAMES, med)
        candidate = sorted({lab for e in examples for lab in e.labels})
        modelled: dict[str, LabelModel] = {}
        skipped: dict[str, str] = {}
        evals: list[J] = []
        for lab in candidate:
            if taxonomy.BY_ID.get(lab) is None or chain not in taxonomy.get(lab).chains:
                skipped[lab] = "not in the taxonomy for this chain"
                continue
            idx = {k: [i for i, e in enumerate(v) if lab in e.labels] for k, v in splits.items()}
            ys = {k: [int(splits[k][i].labels[lab]) for i in idx[k]] for k in splits}
            pos, neg = sum(ys["train"]), len(ys["train"]) - sum(ys["train"])
            if pos < self._cfg.min_positives_per_label or neg < self._cfg.min_positives_per_label:
                skipped[lab] = (
                    f"{pos} positive / {neg} negative training examples; "
                    f"{self._cfg.min_positives_per_label} of each needed"
                )
                continue
            if not ys["validation"] or not ys["test"]:
                skipped[lab] = "no validation or test examples (cannot evaluate)"
                continue
            import numpy as np

            m = backend.fit_label(
                lab,
                x_tr[idx["train"]],
                np.array(ys["train"]),
                x_va[idx["validation"]],
                np.array(ys["validation"]),
                seed=seed,
                high_target=self._cfg.high_precision_target,
                moderate_target=self._cfg.moderate_precision_target,
                min_support=self._cfg.min_threshold_support,
            )
            modelled[lab] = m
            for split, x in (("train", x_tr), ("validation", x_va), ("test", x_te)):
                p: list[float] = backend.scores(m, x[idx[split]]) if idx[split] else []
                evals.append(
                    {
                        "split": split,
                        "label": lab,
                        "metrics": backend.metrics(ys[split], p, m.thresholds),
                    }
                )
        if not modelled:
            return self._finish(
                run_id,
                "insufficient_data",
                dataset_version,
                dataset,
                "No label had enough positive and negative examples. "
                f"Skipped: {json.dumps(skipped)[:300]}",
            )
        import numpy as np

        x_all = np.vstack([x_tr, x_va, x_te])
        anomaly = backend.fit_anomaly(x_tr, seed) if len(x_tr) >= 20 else None
        anomaly_ref = sorted(backend.anomaly_scores(anomaly, x_tr)) if anomaly is not None else []
        clusters = backend.cluster_summary(x_all)
        version = self._next_version(name)
        status = "experimental_synthetic" if data_kind == "synthetic" else "trained"
        path = model_root(self._settings) / name / f"{version}.joblib"
        artifact = {
            "feature_names": list(FEATURE_NAMES),
            "medians": med,
            "labels": {
                k: {
                    "estimator": v.estimator,
                    "thresholds": v.thresholds,
                    "calibrated": v.calibrated,
                }
                for k, v in modelled.items()
            },
            "anomaly": anomaly,
            "anomaly_reference": anomaly_ref,
        }
        sha, size = backend.save(artifact, path, self._cfg.max_artifact_bytes)
        summary = {
            split: backend.summarize(
                {e["label"]: e["metrics"] for e in evals if e["split"] == split}
            )
            for split in ("train", "validation", "test")
        }
        dataset["clusters"] = clusters
        with self._repo.transaction() as conn:
            conn.execute(
                insert(schema.ml_models).values(
                    name=name,
                    version=version,
                    kind="multilabel_random_forest",
                    chain=chain,
                    status=status,
                    feature_version=FEATURE_VERSION,
                    taxonomy_version=taxonomy.TAXONOMY_VERSION,
                    dataset_version=dataset_version,
                    data_kind=data_kind,
                    artifact_path=str(path.relative_to(self._settings.resolved_data_root)),
                    artifact_sha256=sha,
                    params_json=json.dumps({**params, "artifact_bytes": size}),
                    labels_json=json.dumps(
                        {
                            "modelled": sorted(modelled),
                            "skipped": skipped,
                            "calibration": {k: v.calibration for k, v in modelled.items()},
                            "train_counts": {k: v.train_counts for k, v in modelled.items()},
                            "anomaly_model": "IsolationForest" if anomaly is not None else None,
                            "summary": summary,
                        }
                    ),
                    thresholds_json=json.dumps({k: v.thresholds for k, v in modelled.items()}),
                    training_run_id=run_id,
                    created_at=utcnow(),
                    note=SYNTHETIC_NOTE if data_kind == "synthetic" else None,
                )
            )
            for e in evals:
                conn.execute(
                    insert(schema.ml_evaluation_results).values(
                        training_run_id=run_id,
                        model_name=name,
                        model_version=version,
                        split=e["split"],
                        label=e["label"],
                        data_kind=data_kind,
                        payload_json=json.dumps(e["metrics"]),
                    )
                )
        return self._finish(
            run_id,
            "succeeded",
            dataset_version,
            dataset,
            f"Registered {name} {version} ({status}); modelled "
            f"{len(modelled)} label(s), skipped {len(skipped)}.",
            model=(name, version),
        )

    # ------------------------------------------------------------------ bookkeeping

    def _start(self, name: str, chain: str, data_kind: str, params: J) -> int:
        with self._repo.transaction() as conn:
            pk = conn.execute(
                insert(schema.ml_training_runs).values(
                    model_name=name,
                    chain=chain,
                    started_at=utcnow(),
                    status="running",
                    data_kind=data_kind,
                    params_json=json.dumps(params),
                )
            ).inserted_primary_key
        assert pk is not None  # noqa: S101
        return int(pk[0])

    def _finish(
        self,
        run_id: int,
        status: str,
        dataset_version: str | None,
        dataset: J | None,
        message: str,
        model: tuple[str, str] | None = None,
    ) -> J:
        with self._repo.transaction() as conn:
            conn.execute(
                update(schema.ml_training_runs)
                .where(schema.ml_training_runs.c.id == run_id)
                .values(
                    finished_at=utcnow(),
                    status=status,
                    dataset_version=dataset_version,
                    dataset_json=json.dumps(dataset) if dataset else None,
                    message=message[:500],
                )
            )
        return {
            "training_run_id": run_id,
            "status": status,
            "message": message,
            "dataset_version": dataset_version,
            "dataset": dataset,
            "model": {"name": model[0], "version": model[1]} if model else None,
        }

    def _next_version(self, name: str) -> str:
        with self._repo.transaction() as conn:
            n = int(
                conn.execute(
                    select(func.count())
                    .select_from(schema.ml_models)
                    .where(schema.ml_models.c.name == name)
                ).scalar_one()
            )
        return f"1.0.{n}"


def _count(values: Any) -> J:
    out: J = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out
