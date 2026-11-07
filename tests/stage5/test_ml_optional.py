"""The ML runtime (numpy, scikit-learn, joblib) is optional.

BIP must start, classify with the rules baseline, and say so honestly when the ML packages are
not installed (for example on Android/Termux, where scikit-learn cannot be installed). Rules
output must never be presented as a model prediction.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bip.ml import runtime
from tests.stage5.helpers import API, env_with

ML_MODULES = ("numpy", "sklearn", "joblib")


def test_runtime_reports_available_when_packages_are_installed() -> None:
    st = runtime.ml_runtime()
    assert st.available is True
    assert st.missing == ()


def test_classification_falls_back_to_rules_when_ml_runtime_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    e = env_with(tmp_path, allow_synthetic_training=True)
    with TestClient(e.app) as c:
        w = e.eth.wallet
        c.post(f"{API}/ingestion/wallet/ethereum/{w}?limit=20")
        trained = c.post(
            f"{API}/ml/training-runs", json={"chain": "ethereum", "source": "synthetic"}
        )
        assert trained.json()["status"] == "succeeded"  # a model exists

        monkeypatch.setattr(runtime, "ml_runtime", lambda: runtime.MLRuntime(False, ("sklearn",)))

        real_backend = runtime.backend
        calls: list[str] = []

        def counting_backend() -> object:
            mod = real_backend()  # raises MLUnavailableError: the runtime is reported missing
            calls.append("backend loaded")  # only reached if the backend was actually loaded
            return mod

        monkeypatch.setattr(runtime, "backend", counting_backend)

        r = c.post(f"{API}/ml/classify/ethereum/{w}").json()
        assert r["model"] is None  # rules output is never attributed to a model
        assert r["account_type"]["status"] == "rule_based"
        assert any("not installed" in lim and "rules baseline" in lim for lim in r["limitations"])
        assert calls == []  # the ML backend was never loaded or used

        t = c.post(f"{API}/ml/training-runs", json={"chain": "ethereum", "source": "synthetic"})
        assert t.status_code == 503
        assert t.json()["error"]["code"] == "ml_unavailable"

        models = c.get(f"{API}/ml/models").json()
        assert models["runtime"] == {"available": False, "missing": ["sklearn"]}
    e.engine.dispose()


def test_api_starts_without_ml_packages(tmp_path: Path) -> None:
    """Run in a fresh interpreter where numpy, sklearn and joblib cannot be imported."""
    script = textwrap.dedent(
        f"""
        import importlib.abc, sys
        BLOCKED = {ML_MODULES!r}
        class Block(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in BLOCKED:
                    raise ModuleNotFoundError(f"No module named {{name!r}}")
                return None
        sys.meta_path.insert(0, Block())

        from fastapi.testclient import TestClient
        from tests.stage5.helpers import API, env_with
        e = env_with(__import__("pathlib").Path({str(tmp_path)!r}))
        with TestClient(e.app) as c:
            assert c.get(f"{{API}}/health").json()["status"] == "ok"
            w = e.eth.wallet
            c.post(f"{{API}}/ingestion/wallet/ethereum/{{w}}?limit=20")
            r = c.post(f"{{API}}/ml/classify/ethereum/{{w}}").json()
            assert r["model"] is None, r
            assert r["account_type"]["status"] == "rule_based"
            assert c.get(f"{{API}}/ml/models").json()["runtime"]["available"] is False
            body = {{"chain": "ethereum", "source": "stored_labels"}}
            t = c.post(f"{{API}}/ml/training-runs", json=body)
            assert t.status_code == 503, t.text
        assert not any(m.split(".")[0] in BLOCKED for m in sys.modules), "an ML module was imported"
        print("OK")
        """
    )
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run(  # noqa: S603 - fixed interpreter and a script built in this test
        [sys.executable, "-c", script], cwd=root, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip().endswith("OK")


def test_missing_runtime_limitations_do_not_contradict_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    e = env_with(tmp_path, allow_synthetic_training=True)
    with TestClient(e.app) as c:
        w = e.eth.wallet
        c.post(f"{API}/ingestion/wallet/ethereum/{w}?limit=20")
        c.post(f"{API}/ml/training-runs", json={"chain": "ethereum", "source": "synthetic"})
        monkeypatch.setattr(runtime, "ml_runtime", lambda: runtime.MLRuntime(False, ("sklearn",)))
        lims = c.post(f"{API}/ml/classify/ethereum/{w}").json()["limitations"]
        assert not any("No trained model is registered" in x for x in lims), lims
        assert not any("A trained model" in x for x in lims), lims  # it may be synthetic
        assert any("is registered, but the ML runtime" in x for x in lims), lims
    e.engine.dispose()


def test_partly_installed_runtime_degrades_to_rules_instead_of_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """find_spec succeeds but importing the backend fails (e.g. a broken wheel on Termux)."""
    e = env_with(tmp_path, allow_synthetic_training=True)
    with TestClient(e.app) as c:
        w = e.eth.wallet
        c.post(f"{API}/ingestion/wallet/ethereum/{w}?limit=20")
        c.post(f"{API}/ml/training-runs", json={"chain": "ethereum", "source": "synthetic"})

        def broken() -> object:
            raise runtime.MLUnavailableError("The ML runtime failed to import (bad wheel).")

        monkeypatch.setattr(runtime, "backend", broken)
        r = c.post(f"{API}/ml/classify/ethereum/{w}")
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["model"] is None
        assert any("failed to import" in x for x in body["limitations"]), body["limitations"]
    e.engine.dispose()
