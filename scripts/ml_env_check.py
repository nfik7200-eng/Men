"""Report the ML environment and run a tiny fit/predict smoke test. Exit code 1 if anything is broken.

Run inside the ML venv:  .venv-ml/bin/python scripts/ml_env_check.py
Synthetic data here is a plumbing check only (is the numeric stack importable and functional?); it is not evidence of anything.
"""
import platform
import sys


def main() -> int:
    print(f"python   {platform.python_version()} ({platform.system()} {platform.machine()})")
    try:
        import numpy as np
        import scipy
        import sklearn
        import joblib
        from sklearn.ensemble import IsolationForest
        from sklearn.linear_model import LogisticRegression
    except Exception as e:  # noqa: BLE001 - report any import failure
        print(f"FAIL: cannot import the ML stack: {e!r}")
        return 1
    print(f"sklearn  {sklearn.__version__}\nnumpy    {np.__version__}\nscipy    {scipy.__version__}\njoblib   {joblib.__version__}")
    try:
        rng = np.random.RandomState(0)
        X = np.vstack([rng.normal(-2, 1, (40, 3)), rng.normal(2, 1, (40, 3))])
        y = np.array([0] * 40 + [1] * 40)
        clf = LogisticRegression(max_iter=200).fit(X, y)
        acc = float((clf.predict(X) == y).mean())
        iso = IsolationForest(n_estimators=20, random_state=0).fit(X)
        pred = iso.predict(X)
        assert acc > 0.9, f"smoke accuracy too low: {acc}"
        assert set(np.unique(pred)) <= {-1, 1}
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: smoke test: {e!r}")
        return 1
    print(f"smoke    ok (LogisticRegression train acc {acc:.2f}, IsolationForest predict ok)")
    try:  # the project itself must be importable from the same env
        sys.path.insert(0, ".")
        import watchtower.matching  # noqa: F401
        print("watchtower import ok")
    except Exception as e:  # noqa: BLE001
        print(f"WARN: watchtower not importable from cwd ({e!r}); install requirements-core.txt / run from repo root")
    return 0


if __name__ == "__main__":
    sys.exit(main())
