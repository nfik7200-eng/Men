"""Stage 5 ML: features, rules, labels, training gates, calibration/thresholds, abstention.

All data is offline: synthetic wallets (labelled as such) or seeded test rows.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from bip.db import schema
from bip.intel.dataset import WalletDataset
from bip.ml import backend, synthetic, taxonomy
from bip.ml.features import FEATURE_NAMES, extract
from bip.ml.service import _contradictions  # pyright: ignore[reportPrivateUsage]
from tests.stage3.builders import T0, Builder, a
from tests.stage3.test_intel_api import Env
from tests.stage5.helpers import API, J, env_with


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    e = env_with(tmp_path, allow_synthetic_training=True)
    yield e
    e.engine.dispose()


@pytest.fixture
def client(env: Env) -> Iterator[TestClient]:
    with TestClient(env.app) as c:
        yield c


# ---------------------------------------------------------------- taxonomy and features


def test_taxonomy_is_hierarchical_and_flags_sensitive_labels() -> None:
    dims = {x.dimension for x in taxonomy.ALL}
    assert dims == {"account_type", "behavioural_role"}
    assert taxonomy.get("exchange_hot_wallet").sensitive
    assert taxonomy.get("potentially_malicious_behaviour").sensitive
    assert "solana_program" in {x.id for x in taxonomy.account_types_for("solana")}
    assert "mev_participant" not in {x.id for x in taxonomy.roles_for("solana")}


def test_features_distinguish_missing_from_zero_and_respect_as_of() -> None:
    empty = extract(WalletDataset(chain="ethereum", address=a(1), owned_accounts=frozenset()), T0)
    assert all(v is None for v in empty.values.values())  # no window: unavailable, never 0
    assert set(empty.availability.values()) == {"unavailable"}
    b = Builder()
    for i in range(6):
        b.transfer(T0 + timedelta(hours=i), direction="out", party=a(2), amount=10**18)
    later = T0 + timedelta(days=30)
    b.tx(later, to=a(3))  # after as_of: must be excluded (no leakage)
    fv = extract(b.ds, T0 + timedelta(days=1))
    assert fv.values["tx_count"] == 6
    assert fv.coverage["records_after_as_of_excluded"] == 1
    assert fv.values["deployments"] == 0  # observed zero, not missing
    assert fv.availability["deployments"] == "observed"
    assert fv.values["failed_tx_rate"] == 0.0
    assert fv.values["recent_activity_ratio"] is None  # nothing in the prior week: unavailable
    assert fv.values["repeated_amount_share"] == 1.0
    assert extract(b.ds, T0 + timedelta(days=1)).digest() == fv.digest()  # reproducible
    assert list(fv.values) == list(FEATURE_NAMES)


def test_solana_features() -> None:
    b = Builder(chain="solana", wallet="W" * 32)
    for i in range(4):
        b.tx(
            T0 + timedelta(minutes=i),
            programs=("11111111111111111111111111111111", "Prog111111111111111111111111111111"),
        )
    tx = b.ds.transactions[0]
    b.ds.transactions[0] = dataclasses.replace(tx, instruction_count=4, unknown_instruction_count=1)
    fv = extract(b.ds, T0 + timedelta(hours=1))
    assert fv.values["distinct_call_targets"] == 2
    assert fv.values["unknown_instruction_share"] == 0.25
    assert fv.values["burst_max_10min"] == 4


# ---------------------------------------------------------------- deterministic rules


def test_rules_classify_account_type_from_stored_evidence(env: Env, client: TestClient) -> None:
    w = env.eth.wallet
    client.post(f"{API}/ingestion/wallet/ethereum/{w}?limit=20")
    r = client.post(f"{API}/ml/classify/ethereum/{w}").json()
    assert r["account_type"]["label"] == "externally_owned_account"
    assert r["account_type"]["status"] == "rule_based"
    assert r["account_type"]["confidence"] is None  # rules carry no probability
    assert r["account_type"]["evidence"][0]["type"] == "transaction"
    token = client.post(f"{API}/ml/classify/ethereum/{env.eth.token}").json()
    assert token["account_type"]["label"] == "token_contract"
    unknown = client.post(f"{API}/ml/classify/ethereum/{a(777)}").json()
    assert unknown["account_type"]["label"] == "unknown_account_type"
    assert unknown["status"] == "insufficient_evidence"
    assert unknown["behavioural_roles"] == []
    sol = env.sol
    client.post(f"{API}/ingestion/wallet/solana/{sol.wallet}?limit=20")
    assert (
        client.post(f"{API}/ml/classify/solana/{sol.wallet}").json()["account_type"]["label"]
        == "wallet_account"
    )
    assert (
        client.post(f"{API}/ml/classify/solana/{sol.unknown_program}").json()["account_type"][
            "label"
        ]
        == "solana_program"
    )
    assert (
        client.post(f"{API}/ml/classify/solana/{sol.src_ata}").json()["account_type"]["label"]
        == "token_account"
    )


# ---------------------------------------------------------------- labels


def test_label_import_validation_and_reviews(client: TestClient) -> None:
    good = {
        "chain": "ethereum",
        "address": a(1),
        "label": "defi_trader",
        "status": "weak",
        "source": "test-list",
        "observed_at": "2026-01-01T00:00:00Z",
    }
    r = client.post(f"{API}/ml/labels", json={"labels": [good]})
    assert r.json() == {"added": 1, "duplicates_skipped": 0}
    assert (
        client.post(f"{API}/ml/labels", json={"labels": [good]}).json()["duplicates_skipped"] == 1
    )
    bad_label = client.post(f"{API}/ml/labels", json={"labels": [{**good, "label": "insider"}]})
    assert bad_label.status_code == 422
    wrong_chain = client.post(
        f"{API}/ml/labels",
        json={
            "labels": [{**good, "chain": "solana", "address": "W" * 32, "label": "mev_participant"}]
        },
    )
    assert wrong_chain.status_code == 422
    assert (
        client.post(f"{API}/ml/labels", json={"labels": [{**good, "status": "great"}]}).status_code
        == 422
    )
    assert (
        client.post(
            f"{API}/ml/labels", json={"labels": [{**good, "data_kind": "synthetic"}]}
        ).status_code
        == 422
    )  # data_kind cannot be set by importers
    listed = client.get(f"{API}/ml/labels").json()
    lid = listed["items"][0]["id"]
    assert listed["items"][0]["data_kind"] == "real"
    client.post(
        f"{API}/ml/labels/{lid}/reviews", json={"decision": "rejected", "reviewer": "analyst"}
    )
    again = client.get(f"{API}/ml/labels").json()["items"][0]
    assert again["latest_review"] == "rejected"
    assert again["status"] == "weak"  # original label row untouched


# ---------------------------------------------------------------- training


def test_real_label_training_refuses_insufficient_data(client: TestClient) -> None:
    r = client.post(
        f"{API}/ml/training-runs", json={"chain": "ethereum", "source": "stored_labels"}
    ).json()
    assert r["status"] == "insufficient_data"
    assert r["model"] is None
    assert "No model was trained" in r["message"]
    assert client.get(f"{API}/ml/models").json()["items"] == []


def test_synthetic_training_is_disabled_by_default(tmp_path: Path) -> None:
    e = env_with(tmp_path)
    try:
        with TestClient(e.app) as c:
            r = c.post(f"{API}/ml/training-runs", json={"chain": "ethereum", "source": "synthetic"})
            assert r.status_code == 422
    finally:
        e.engine.dispose()


def test_synthetic_training_is_reproducible_versioned_and_labelled(
    env: Env, client: TestClient
) -> None:
    r1 = client.post(
        f"{API}/ml/training-runs", json={"chain": "ethereum", "source": "synthetic"}
    ).json()
    r2 = client.post(
        f"{API}/ml/training-runs", json={"chain": "ethereum", "source": "synthetic"}
    ).json()
    assert r1["status"] == r2["status"] == "succeeded"
    assert r1["dataset_version"] == r2["dataset_version"]  # same seed → same dataset
    assert r1["model"]["version"] == "1.0.0"
    assert r2["model"]["version"] == "1.0.1"
    splits = r1["dataset"]["splits"]
    assert splits["train"]["as_of_to"] <= splits["test"]["as_of_from"]  # out-of-time test
    m1 = client.get(f"{API}/ml/models/wallet-classifier-ethereum/1.0.0").json()
    m2 = client.get(f"{API}/ml/models/wallet-classifier-ethereum/1.0.1").json()
    assert m1["status"] == "experimental_synthetic"
    assert m1["data_kind"] == "synthetic"
    assert "SYNTHETIC" in m1["note"]
    assert m1["artifact_sha256"] == m2["artifact_sha256"]  # deterministic artifact
    tests = [e for e in m1["evaluation"] if e["split"] == "test"]
    assert {e["data_kind"] for e in tests} == {"synthetic"}
    for e in tests:
        m = e["metrics"]
        assert {
            "precision",
            "recall",
            "f1",
            "confusion",
            "brier",
            "reliability_bins",
            "at_high_threshold",
            "pr_auc",
            "precision_at_10",
        } <= set(m)
        # every test evaluation says how the model compares with the deterministic rules
        baseline = m["rule_baseline"]
        assert "available" in baseline
        if baseline["available"]:
            assert {"precision", "recall", "f1", "pr_auc", "false_positives"} <= set(baseline)
        else:
            assert baseline["reason"]
    assert any(e["metrics"]["rule_baseline"]["available"] for e in tests)
    for thr in m1["thresholds"].values():
        for key in ("high", "moderate"):
            assert thr[key] is None or thr[key] >= 0.5  # never a positive claim below 0.5
    runs = client.get(f"{API}/ml/training-runs").json()["items"]
    assert runs[0]["data_kind"] == "synthetic"


def test_real_labels_train_with_explicit_examples_only(tmp_path: Path) -> None:
    """Seeded test rows + real-kind labels: the pipeline trains when the gate is met."""
    from tests.stage3.test_findings_opportunities import Seeder

    e = env_with(tmp_path, min_labelled_wallets=10, min_positives_per_label=3)
    try:
        with TestClient(e.app) as c:
            labels: list[J] = []
            base = datetime(2026, 1, 1, tzinfo=UTC)
            with e.engine.begin() as conn:
                s = Seeder(conn)
                for i in range(24):
                    w = a(5000 + i)
                    busy = i % 2 == 0
                    at = base + timedelta(days=i)
                    for k in range(30 if busy else 3):
                        s.tx(at - timedelta(minutes=5 * k if busy else 3000 * k), w, a(9))
                    labels.append(
                        {
                            "chain": "ethereum",
                            "address": w,
                            "label": "high_frequency_activity",
                            "value": busy,
                            "status": "analyst_reviewed",
                            "source": "test-review",
                            "observed_at": (at + timedelta(minutes=1)).isoformat(),
                        }
                    )
            assert c.post(f"{API}/ml/labels", json={"labels": labels}).status_code == 201
            r = c.post(
                f"{API}/ml/training-runs", json={"chain": "ethereum", "source": "stored_labels"}
            ).json()
            assert r["status"] == "succeeded", r
            model = c.get(f"{API}/ml/models").json()["active"]["ethereum"]
            assert model["status"] == "trained"
            assert model["data_kind"] == "real"
            assert model["labels"]["modelled"] == ["high_frequency_activity"]
            # validation split is too small to calibrate: no high/moderate thresholds
            thr = model["thresholds"]["high_frequency_activity"]
            assert thr["high"] is None
            assert thr["moderate"] is None
            out = c.post(f"{API}/ml/classify/ethereum/{a(5000)}").json()
            model_roles = [
                x for x in out["behavioural_roles"] if x["source"].startswith("wallet-classifier")
            ]
            assert model_roles
            assert all(x["status"] == "low_confidence" for x in model_roles)
            assert all(x["calibrated"] is False for x in model_roles)
    finally:
        e.engine.dispose()


def test_threshold_selection_requires_measured_precision() -> None:
    import numpy as np

    scores = np.array([0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55, 0.52, 0.3, 0.2])
    y = np.array([1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0])
    thr = backend._threshold(scores, y, 0.95, 5)  # pyright: ignore[reportPrivateUsage]
    assert thr == 0.55  # lowest cut keeping precision ≥ 0.95 with ≥ 5 predictions
    assert backend._threshold(scores, y, 0.95, 50) is None  # pyright: ignore[reportPrivateUsage]
    noisy = np.array([1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0])
    assert backend._threshold(scores, noisy, 0.95, 3) is None  # pyright: ignore[reportPrivateUsage]


def test_tampered_artifact_is_refused(env: Env, client: TestClient) -> None:
    client.post(f"{API}/ml/training-runs", json={"chain": "ethereum", "source": "synthetic"})
    model = client.get(f"{API}/ml/models").json()["active"]["ethereum"]
    path = env.settings.resolved_data_root / model["artifact_path"]
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ValueError, match="hash does not match"):
        backend.load(path, model["artifact_sha256"], env.settings.resolved_data_root / "ml")
    with pytest.raises(ValueError, match="outside"):
        backend.load(Path("/etc/hosts"), "0" * 64, env.settings.resolved_data_root / "ml")


# ---------------------------------------------------------------- classification


def test_classification_abstains_and_labels_synthetic_models(env: Env, client: TestClient) -> None:
    client.post(f"{API}/ml/training-runs", json={"chain": "ethereum", "source": "synthetic"})
    w = env.eth.wallet
    client.post(f"{API}/ingestion/wallet/ethereum/{w}?limit=20")
    r = client.post(f"{API}/ml/classify/ethereum/{w}").json()  # 2 observed transactions
    assert r["model"]["status"] == "experimental_synthetic"
    assert any("SYNTHETIC" in x for x in r["limitations"])
    assert any("abstained" in x for x in r["limitations"])
    assert all(x["source"].startswith("bip-rules") for x in r["behavioural_roles"])
    assert r["status"] == "classified_with_limitations"
    history = client.get(f"{API}/ml/classifications/ethereum/{w}").json()
    assert history["total"] == 1
    one = client.get(f"{API}/ml/classifications/{r['classification_id']}").json()
    assert one["feature_digest"] == r["feature_digest"]
    feats = client.get(f"{API}/ml/features/ethereum/{w}").json()
    assert feats["feature_version"] == "1.0.0"
    with env.engine.connect() as conn:  # predictions never become training labels
        assert (
            conn.execute(select(func.count()).select_from(schema.wallet_labels)).scalar_one() == 0
        )


def test_multiple_model_roles_and_statuses_on_synthetic_wallet(env: Env) -> None:
    """A synthetic bot-like wallet gets several roles; every one carries its status."""
    ctx_settings = env.settings
    with TestClient(env.app):
        ctx = env.app.state.bip
        ctx.trainer.train("ethereum", "synthetic")
        svc = ctx.classifier
        model = svc.active_model("ethereum")
        assert model is not None
        art = svc._artifact(model)  # pyright: ignore[reportPrivateUsage]
        bot = next(w for w in synthetic.generate(seed=99) if w.labels["automated_bot"])
        fv = extract(bot.dataset, bot.as_of, data_kind="synthetic")
        roles, not_assigned, anomaly = svc._predict(model, fv)  # pyright: ignore[reportPrivateUsage]
        statuses = {x["status"] for x in roles}
        assert roles
        assert statuses <= {"high_confidence", "moderate_confidence", "low_confidence"}
        assert all(x["model_status"] == "experimental_synthetic" for x in roles)
        assert anomaly["status"] == "scored"
        assert set(art["labels"]) == set(synthetic.SYNTHETIC_ROLES)
        _ = (not_assigned, ctx_settings)


def test_conflicting_predictions_are_kept() -> None:
    fv = extract(Builder().ds, T0)
    out = _contradictions(
        [{"label": "low_activity_or_new"}],
        [{"label": "high_frequency_activity", "source": "m@1"}],
        fv,
    )
    assert out
    assert "neither is preferred" in out[0]["note"]


def test_api_validation(client: TestClient) -> None:
    assert client.post(f"{API}/ml/classify/base/{a(1)}").status_code == 422
    assert client.post(f"{API}/ml/classify/ethereum/nope").status_code == 422
    assert client.post(f"{API}/ml/training-runs", json={"chain": "bitcoin"}).status_code == 422
    assert client.get(f"{API}/ml/models/x/1").status_code == 422
    assert client.get(f"{API}/ml/classifications/{'0' * 36}").status_code == 404
    tax = client.get(f"{API}/ml/taxonomy").json()
    assert tax["features"]["version"] == "1.0.0"
    assert json.dumps(tax).count("sensitive") >= 1


# ---------------------------------------------------------------- review regressions


def test_training_gate_counts_distinct_wallets_and_splits_by_wallet(tmp_path: Path) -> None:
    from tests.stage3.test_findings_opportunities import Seeder

    e = env_with(tmp_path, min_labelled_wallets=10, min_positives_per_label=3)
    try:
        with TestClient(e.app) as c:
            base = datetime(2026, 1, 1, tzinfo=UTC)
            w = a(6000)
            with e.engine.begin() as conn:
                s = Seeder(conn)
                for k in range(5):
                    s.tx(base - timedelta(minutes=k), w, a(9))
            snapshots = [
                {
                    "chain": "ethereum",
                    "address": w,
                    "label": "automated_bot",
                    "value": i % 2 == 0,
                    "status": "analyst_reviewed",
                    "source": "review",
                    "observed_at": (base + timedelta(days=i)).isoformat(),
                }
                for i in range(30)
            ]
            c.post(f"{API}/ml/labels", json={"labels": snapshots})
            r = c.post(
                f"{API}/ml/training-runs", json={"chain": "ethereum", "source": "stored_labels"}
            ).json()
            assert r["status"] == "insufficient_data"  # 30 snapshots of ONE wallet
            assert "1 distinct labelled wallet" in r["message"]
    finally:
        e.engine.dispose()


def test_conflicting_labels_are_dropped(tmp_path: Path) -> None:
    e = env_with(tmp_path)
    try:
        with TestClient(e.app) as c:
            at = "2026-01-01T00:00:00Z"
            c.post(
                f"{API}/ml/labels",
                json={
                    "labels": [
                        {
                            "chain": "ethereum",
                            "address": a(1),
                            "label": "defi_trader",
                            "value": True,
                            "status": "verified",
                            "source": "s1",
                            "observed_at": at,
                        },
                        {
                            "chain": "ethereum",
                            "address": a(1),
                            "label": "defi_trader",
                            "value": False,
                            "status": "verified",
                            "source": "s2",
                            "observed_at": at,
                        },
                    ]
                },
            )
            examples = e.app.state.bip.trainer._stored("ethereum")  # pyright: ignore[reportPrivateUsage]
            assert examples == []  # the only label conflicted, so no example remains
    finally:
        e.engine.dispose()


def test_synthetic_models_never_report_high_or_moderate_confidence(env: Env) -> None:
    with TestClient(env.app):
        ctx = env.app.state.bip
        ctx.trainer.train("ethereum", "synthetic")
        model = ctx.classifier.active_model("ethereum")
        assert model is not None
        for w in synthetic.generate(n=60, seed=5):
            fv = extract(w.dataset, w.as_of, data_kind="synthetic")
            roles, _, _ = ctx.classifier._predict(model, fv)  # pyright: ignore[reportPrivateUsage]
            assert {r["status"] for r in roles} <= {"low_confidence"}
            assert all(r["supporting_features"] for r in roles)  # importances are shown


def test_label_counts_report_trainable_wallets_against_the_gate(client: TestClient) -> None:
    def lab(i: int, status: str, label: str = "defi_trader") -> J:
        return {
            "chain": "ethereum",
            "address": a(i),
            "label": label,
            "status": status,
            "source": "analyst",
            "observed_at": "2026-01-01T00:00:00Z",
        }

    labels = [lab(1, "analyst_reviewed"), lab(1, "verified", "long_term_holder"), lab(2, "weak")]
    labels.append(lab(3, "corroborated"))
    assert client.post(f"{API}/ml/labels", json={"labels": labels}).json()["added"] == 4
    counts = client.get(f"{API}/ml/labels").json()["counts"]
    # wallet 1 (two trainable labels) and wallet 3 count once each; a weak label is not trainable
    assert counts["trainable_wallets"] == {"ethereum": 2}
    assert counts["min_labelled_wallets"] == 30
    weak_id = next(
        x["id"] for x in client.get(f"{API}/ml/labels").json()["items"] if x["status"] == "weak"
    )
    client.post(
        f"{API}/ml/labels/{weak_id}/reviews", json={"decision": "accepted", "reviewer": "me"}
    )
    assert client.get(f"{API}/ml/labels").json()["counts"]["trainable_wallets"] == {"ethereum": 3}


def test_rule_roles_never_read_data_after_as_of(tmp_path: Path) -> None:
    """The rule baseline is computed at each example's as_of; a deployed contract's Transfer
    logs that happen later must not make its deployer a token deployer at that time."""
    from datetime import UTC, datetime, timedelta

    from bip.config import load_settings
    from bip.db.engine import create_db_engine
    from bip.db.repository import Repository
    from bip.intel.dataset import TxRow, WalletDataset
    from bip.ml import rules
    from bip.ml.features import extract
    from tests.discovery.seed import E18, Seed, eth_addr, eth_tx

    engine = create_db_engine(load_settings(data_root=tmp_path / "d", env="test"))
    seed = Seed(engine)
    t0 = datetime(2026, 6, 1, tzinfo=UTC)
    deployer, contract = eth_addr(0xD1), eth_addr(0xC1)
    seed.tx(eth_tx(1), deployer, t0, created=contract)
    later = t0 + timedelta(days=30)
    seed.tx(eth_tx(2), eth_addr(0xA1), later, to=contract)
    seed.transfer(eth_tx(2), eth_addr(0xA1), eth_addr(0xB1), E18, later, token=contract)
    tx = TxRow(
        eth_tx(1),
        1,
        None,
        None,
        t0,
        deployer,
        None,
        "success",
        "complete",
        "finalized",
        (),
        provider="SYNTHETIC",
        verification="unverified",
        created_contract=contract,
    )
    ds = WalletDataset("ethereum", deployer, frozenset(), transactions=[tx])
    repo = Repository(engine)
    before = t0 + timedelta(days=1)
    labels = {r["label"] for r in rules.roles(repo, ds, extract(ds, before), before)}
    assert "contract_deployer" in labels
    assert "token_or_nft_deployer" not in labels  # its first Transfer is 29 days later
    after = later + timedelta(days=1)
    labels = {r["label"] for r in rules.roles(repo, ds, extract(ds, after), after)}
    assert "token_or_nft_deployer" in labels
    engine.dispose()
