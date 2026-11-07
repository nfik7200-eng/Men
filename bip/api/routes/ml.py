"""ML wallet classification: classify, history, model registry, training, labels, features.

Classification runs locally on stored observations (no network, no LLM). Training happens
only on explicit request: on reviewed real labels (with an honest data gate), or on the
synthetic dataset when ``BIP_ML__ALLOW_SYNTHETIC_TRAINING`` is enabled. Synthetic models are
always marked ``experimental_synthetic``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query
from pydantic import Field

from bip.api.deps import Context, WritableContext
from bip.api.errors import error_response
from bip.api.routes.chains import _require_chain  # pyright: ignore[reportPrivateUsage]
from bip.api.schemas import Body
from bip.ml import runtime, taxonomy
from bip.ml.features import DESCRIPTIONS, FEATURE_NAMES, FEATURE_VERSION
from bip.ml.labels import LabelIn, ReviewIn

router = APIRouter(prefix="/ml", tags=["ml"])
J = dict[str, Any]
ChainP = Annotated[str, Path(min_length=1, max_length=32)]
AddressP = Annotated[str, Path(min_length=1, max_length=100)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]


@router.get("/taxonomy")
def get_taxonomy(ctx: Context) -> J:
    return {
        "version": taxonomy.TAXONOMY_VERSION,
        "labels": [
            {
                "id": x.id,
                "dimension": x.dimension,
                "chains": list(x.chains),
                "description": x.description,
                "rule_detectable": x.rule_detectable,
                "sensitive": x.sensitive,
            }
            for x in taxonomy.ALL
        ],
        "features": {
            "version": FEATURE_VERSION,
            "names": [{"name": n, "description": DESCRIPTIONS[n]} for n in FEATURE_NAMES],
        },
        "statuses": {
            "high_confidence": "Calibrated score ≥ a threshold validated at ≥ the high "
            "precision target on held-out data.",
            "moderate_confidence": "Calibrated score ≥ a threshold validated at the moderate "
            "precision target.",
            "low_confidence": "Score ≥ 0.5 without a validated threshold.",
            "rule_based": "Deterministic rule over stored evidence (precision not measured).",
            "insufficient_evidence": "Too little observed activity to classify.",
        },
    }


@router.post("/classify/{chain}/{address}", status_code=201)
def classify(ctx: WritableContext, chain: ChainP, address: AddressP) -> J:
    _require_chain(ctx, chain)
    return ctx.classifier.classify(chain, address)


@router.get("/classifications/{chain}/{address}")
def classification_history(ctx: Context, chain: ChainP, address: AddressP, limit: Limit = 20) -> J:
    _require_chain(ctx, chain)
    items = ctx.classifier.history(chain, address, limit)
    return {"items": items, "total": len(items), "limit": limit}


@router.get("/classifications/{classification_id}")
def get_classification(
    ctx: Context, classification_id: Annotated[str, Path(pattern=r"^[0-9a-f\-]{36}$")]
) -> J:
    return ctx.classifier.get(classification_id)


@router.get("/features/{chain}/{address}")
def wallet_features(ctx: Context, chain: ChainP, address: AddressP) -> Any:
    _require_chain(ctx, chain)
    fv = ctx.classifier.latest_features(chain, address)
    if fv is None:
        return error_response(404, "not_found", "no feature snapshot yet; classify the wallet")
    return fv


@router.get("/models")
def list_models(ctx: Context) -> J:
    rt = runtime.ml_runtime()
    return {
        "items": ctx.classifier.models(),
        "active": {c: ctx.classifier.active_model(c) for c in ("ethereum", "solana")},
        "runtime": {"available": rt.available, "missing": list(rt.missing)},
    }


@router.get("/models/{name}/{version}")
def get_model(
    ctx: Context,
    name: Annotated[str, Path(pattern=r"^[a-z0-9\-]{3,64}$")],
    version: Annotated[str, Path(pattern=r"^[0-9][0-9.]{0,31}$")],
) -> J:
    return ctx.classifier.model(name, version)


@router.get("/training-runs")
def training_runs(ctx: Context, limit: Limit = 20) -> J:
    return {"items": ctx.classifier.training_runs(limit)}


class TrainBody(Body):
    chain: Literal["ethereum", "solana"]
    source: Literal["stored_labels", "synthetic"] = "stored_labels"
    seed: Annotated[int, Field(ge=0, le=2**31)] = 7


@router.post("/training-runs", status_code=201)
def start_training(ctx: WritableContext, body: TrainBody) -> Any:
    """Train synchronously (small CPU models). One training run at a time."""
    if not ctx.train_lock.acquire(blocking=False):
        return error_response(409, "training_busy", "A training run is in progress.")
    try:
        return ctx.trainer.train(body.chain, body.source, seed=body.seed)
    finally:
        ctx.train_lock.release()


class LabelImport(Body):
    labels: Annotated[list[LabelIn], Field(min_length=1, max_length=5000)]


@router.post("/labels", status_code=201)
def import_labels(ctx: WritableContext, body: LabelImport) -> Any:
    if len(body.labels) > ctx.settings.ml.max_label_import:
        return error_response(422, "invalid_input", "too many labels in one import")
    return ctx.labels.add(body.labels)


@router.get("/labels")
def list_labels(
    ctx: Context,
    limit: Limit = 50,
    offset: Offset = 0,
    chain: Annotated[str | None, Query(max_length=32)] = None,
) -> J:
    items, total = ctx.labels.list(chain=chain, data_kind=None, limit=limit, offset=offset)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "counts": ctx.labels.counts(),
    }


@router.post("/labels/{label_id}/reviews", status_code=201)
def review_label(
    ctx: WritableContext, label_id: Annotated[int, Path(ge=1, le=2**53)], body: ReviewIn
) -> J:
    return ctx.labels.review(label_id, body)
