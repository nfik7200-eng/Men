"""Controlled continuous retraining.

A scheduled cycle never replaces the served model blindly:

1. Count the filtered, eligible real-label examples (the Task 2 pipeline). Fewer than
   ``continuous_min_new_examples`` beyond the count at the last decision means *skipped*.
2. Otherwise fit a challenger without registering it (:meth:`Trainer.fit`).
3. **Promotion gate.** No champion: promote (the data gate already passed). A champion built on
   another ``feature_version`` is incomparable: promote, noting ``feature version changed``.
   Otherwise re-score the champion on the challenger's test split and promote only if
   challenger macro PR-AUC >= champion macro PR-AUC *and* challenger Brier <= champion Brier +
   ``promotion_brier_tolerance`` (both over the labels both models score).
4. Promotion registers through :meth:`Trainer.register`. A rejected challenger is never inserted
   in ``ml_models`` (``ClassificationService`` serves the newest ``trained`` row, so it cannot be
   served); its run row is closed as ``succeeded`` / ``challenger not promoted`` with metrics.

Bookkeeping lives in ``app_metadata`` (no schema change), per chain and kind
(``wallet_classifier`` or ``signal_ranking``)::

    ml:continuous:<kind>:<chain>:examples        count at the last decision (integer text)
    ml:continuous:<kind>:<chain>:last_decision   JSON decision (see below)

A decision is ``{kind, chain, time, decision, reason, examples, new_examples, filters,
challenger, champion, model}`` where ``decision`` is ``promoted``, ``rejected`` or ``skipped``
(``evaluated`` for signal ranking, which has no champion), ``filters`` is the filter report
(input, output, filters_version, steps), ``challenger``/``champion`` carry
``{macro_pr_auc, brier, labels, n}`` metrics and ``model`` is the registered ``{name, version}``.

Local data only: nothing here reads a provider. The caller holds the locks (``train_lock`` for
:meth:`run_wallet`, ``analysis_lock`` for :meth:`run_ranking`). Model output stays a statistical
signal; nothing is confirmed, rejected or triggered.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Protocol

from bip.config.settings import Settings
from bip.db.repository import Repository
from bip.db.types import utcnow
from bip.ml import runtime
from bip.ml.features import FEATURE_VERSION
from bip.ml.service import ClassificationService
from bip.ml.training import MODEL_PREFIX, Challenger, Trainer

J = dict[str, Any]
WALLET = "wallet_classifier"
RANKING = "signal_ranking"
RANKING_ACTOR = "continuous-training"


class RankingEvaluator(Protocol):
    def label_counts(self) -> dict[str, dict[str, int]]: ...

    def evaluate(self, actor: str) -> dict[str, Any]: ...


def examples_key(chain: str, kind: str = WALLET) -> str:
    return f"ml:continuous:{kind}:{chain}:examples"


def decision_key(chain: str, kind: str = WALLET) -> str:
    return f"ml:continuous:{kind}:{chain}:last_decision"


def _macro(per_label: dict[str, J], labels: Sequence[str]) -> J:
    used = [k for k in labels if _usable(per_label.get(k))]
    n = len(used)
    return {
        "macro_pr_auc": sum(per_label[k]["pr_auc"] for k in used) / n if n else None,
        "brier": sum(per_label[k]["brier"] for k in used) / n if n else None,
        "labels": list(used),
        "n": n,
    }


def _usable(m: J | None) -> bool:
    return m is not None and m.get("pr_auc") is not None and m.get("brier") is not None


def _common(a: dict[str, J], b: dict[str, J]) -> list[str]:
    return sorted(k for k in set(a) & set(b) if _usable(a[k]) and _usable(b[k]))


def gate(
    challenger: dict[str, J], champion: dict[str, J], brier_tolerance: float
) -> tuple[bool, str]:
    """Promotion decision from per-label test metrics (``pr_auc``, ``brier``) of both models,
    compared as macro means over the labels both models have a usable metric for."""
    common = _common(challenger, champion)
    if not common:
        return False, "no comparable labels between challenger and champion"
    ch, cp = _macro(challenger, common), _macro(champion, common)
    if ch["macro_pr_auc"] < cp["macro_pr_auc"]:
        return False, (
            f"challenger macro PR-AUC {ch['macro_pr_auc']:.4f} is below the champion's "
            f"{cp['macro_pr_auc']:.4f}"
        )
    if ch["brier"] > cp["brier"] + brier_tolerance:
        return False, (
            f"challenger Brier {ch['brier']:.4f} exceeds the champion's {cp['brier']:.4f} "
            f"by more than {brier_tolerance}"
        )
    return True, f"challenger is not worse than the champion on {len(common)} label(s)"


def _stored_count(repo: Repository, key: str) -> int:
    raw = repo.get_meta(key)
    try:
        return max(0, int(raw)) if raw is not None else 0
    except ValueError:
        return 0


class ContinuousTraining:
    def __init__(
        self,
        repo: Repository,
        settings: Settings,
        trainer: Trainer,
        classifier: ClassificationService,
        ranking: RankingEvaluator,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._repo = repo
        self._cfg = settings.ml
        self._trainer = trainer
        self._classifier = classifier
        self._ranking = ranking
        self._clock = clock

    # ------------------------------------------------------------------ recording

    def _record(self, kind: str, chain: str, decision: J, *, count: int | None) -> J:
        out: J = {"kind": kind, "chain": chain, "time": self._clock().isoformat(), **decision}
        self._repo.set_meta(decision_key(chain, kind), json.dumps(out))
        if count is not None:
            self._repo.set_meta(examples_key(chain, kind), str(count))
        return out

    def last_decision(self, chain: str, kind: str = WALLET) -> J | None:
        raw = self._repo.get_meta(decision_key(chain, kind))
        return None if raw is None else json.loads(raw)

    # ------------------------------------------------------------------ wallet classifier

    def run_wallet(self, chain: str) -> J:
        """One controlled retraining decision for a chain. The caller holds ``train_lock``."""
        examples, report = self._trainer.eligible_examples(chain)
        n = len(examples)
        new = n - _stored_count(self._repo, examples_key(chain))
        base: J = {"examples": n, "new_examples": new, "filters": report}
        if new < self._cfg.continuous_min_new_examples:
            return self._record(
                WALLET,
                chain,
                {
                    **base,
                    "decision": "skipped",
                    "reason": f"{new} new filtered example(s) since the last decision; "
                    f"{self._cfg.continuous_min_new_examples} needed",
                },
                count=None,
            )
        try:
            fitted = self._trainer.fit(chain, "stored_labels", prepared=(examples, report))
        except runtime.MLUnavailableError as exc:
            return self._record(
                WALLET, chain, {**base, "decision": "skipped", "reason": str(exc)}, count=None
            )
        if isinstance(fitted, dict):  # the data gate refused: no challenger exists
            return self._record(
                WALLET,
                chain,
                {**base, "decision": "skipped", "reason": str(fitted["message"])},
                count=n,
            )
        return self._decide(chain, fitted, base, n)

    def _champion(self, chain: str) -> J | None:
        model = self._classifier.active_model(chain)
        if (
            model is None
            or model["status"] != "trained"
            or model["name"] != (f"{MODEL_PREFIX}-{chain}")
        ):
            return None
        return model

    def score_champion(self, champion: J, challenger: Challenger) -> dict[str, J]:
        """The champion's per-label metrics on the *challenger's* test split."""
        art = self._classifier.load_artifact(champion)
        be = runtime.backend()
        out: dict[str, J] = {}
        for lab, spec in sorted(art["labels"].items()):
            if lab not in challenger.modelled:
                continue
            rows = [e for e in challenger.test_examples if lab in e.labels]
            if not rows:
                continue
            x, _ = be.matrix([e.fv.values for e in rows], art["feature_names"], art["medians"])
            p = [float(v) for v in spec["estimator"].predict_proba(x)[:, 1]]
            out[lab] = be.metrics([int(e.labels[lab]) for e in rows], p, spec["thresholds"])
        return out

    def _decide(self, chain: str, ch: Challenger, base: J, n: int) -> J:
        champion = self._champion(chain)
        own = ch.test_metrics()
        champion_info: J | None = None
        challenger_info: J = {"training_run_id": ch.run_id, "metrics": _macro(own, sorted(own))}
        if champion is None:
            promote, reason = True, "no champion"
        elif champion["feature_version"] != FEATURE_VERSION:
            promote, reason = True, "feature version changed"
            champion_info = {"name": champion["name"], "version": champion["version"]}
        else:
            try:
                theirs = self.score_champion(champion, ch)
            except Exception as exc:  # noqa: BLE001 - an unscorable champion must not be replaced blindly
                promote, reason = False, f"champion could not be scored ({type(exc).__name__})"
                theirs = {}
            else:
                promote, reason = gate(own, theirs, self._cfg.promotion_brier_tolerance)
            common = _common(own, theirs)
            challenger_info["metrics"] = _macro(own, common)
            champion_info = {
                "name": champion["name"],
                "version": champion["version"],
                "metrics": _macro(theirs, common),
            }
        decision: J = {
            **base,
            "reason": reason,
            "challenger": challenger_info,
            "champion": champion_info,
            "model": None,
        }
        if promote:
            result = self._trainer.register(ch)
            decision["model"] = result["model"]
            decision["decision"] = "promoted"
        else:
            self._trainer.reject(ch, reason)
            decision["decision"] = "rejected"
        return self._record(WALLET, chain, decision, count=n)

    # ------------------------------------------------------------------ signal ranking

    def run_ranking(self, chains: Sequence[str]) -> list[J]:
        """Run the existing ranking evaluation once when any chain has enough new human review
        outcomes; record a decision per chain. The caller holds ``analysis_lock``."""
        counts = self._ranking.label_counts()
        need = self._cfg.continuous_min_new_examples
        totals = {c: sum(counts.get(c, {}).values()) for c in chains}
        news = {c: totals[c] - _stored_count(self._repo, examples_key(c, RANKING)) for c in chains}
        due = [c for c in chains if news[c] >= need]
        result = self._ranking.evaluate(RANKING_ACTOR) if due else None
        out: list[J] = []
        for c in chains:
            base: J = {"examples": totals[c], "new_examples": news[c]}
            if result is not None and c in due:
                out.append(
                    self._record(
                        RANKING,
                        c,
                        {
                            **base,
                            "decision": "evaluated",
                            "reason": f"{news[c]} new human review outcome(s)",
                            "run_id": result.get("run_id"),
                            "status": result.get("status"),
                        },
                        count=totals[c],
                    )
                )
            else:
                out.append(
                    self._record(
                        RANKING,
                        c,
                        {
                            **base,
                            "decision": "skipped",
                            "reason": f"{news[c]} new human review outcome(s) since the last "
                            f"decision; {need} needed",
                        },
                        count=None,
                    )
                )
        return out
