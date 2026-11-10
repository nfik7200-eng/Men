"""Reviewed entity labels. All chains, addresses and evidence here are SYNTHETIC."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bip.db.errors import DuplicateRecordError, InvalidRecordError
from bip.discovery.service import DiscoveryService
from bip.entities.labels import EntityLabelService
from tests.discovery.seed import ETH, NOW, Seed, eth_addr, eth_tx
from tests.security.helpers import API, PASSWORD, Rig

CONTRACT = eth_addr(0xC1)
URL = {"kind": "url", "ref": "https://example.org/synthetic-source", "description": "SYNTHETIC"}


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path, mode="disabled")
    yield r
    r.dispose()


def seed_signal(rig: Rig) -> int:
    Seed(rig.engine).tx(eth_tx(1), eth_addr(0xD1), NOW - timedelta(hours=2), created=CONTRACT)
    service = DiscoveryService(rig.repo, rig.settings, clock=lambda: NOW)
    service.run(ETH)
    return int(service.list_signals(chain=ETH, limit=5, offset=0)["items"][0]["id"])


def propose(c: TestClient, **over: Any) -> Any:
    body: dict[str, Any] = {
        "chain": ETH,
        "ref": CONTRACT,
        "entity_kind": "contract",
        "category": "protocol",
        "name": "Synthetic Protocol",
        "evidence": [],
        **over,
    }
    return c.post(f"{API}/entities/labels", json=body)


def test_a_proposal_is_not_attribution_until_a_person_approves_with_evidence(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        made = propose(c, evidence=[{"kind": "note", "ref": "looks like a router"}])
        assert made.status_code == 201
        label = made.json()
        assert (label["status"], label["proposed_by"]) == ("proposed", "local")
        assert c.get(f"{API}/entities/{ETH}/{CONTRACT}/labels").json()["approved"] == []
        no_evidence = c.post(
            f"{API}/entities/labels/{label['id']}/review",
            json={"decision": "approve", "note": "I checked, trust me"},
        )
        assert no_evidence.status_code == 422  # a note is not traceable evidence
        short = c.post(
            f"{API}/entities/labels/{label['id']}/review",
            json={"decision": "approve", "note": "ok", "evidence": [URL]},
        )
        assert short.status_code == 422
        approved = c.post(
            f"{API}/entities/labels/{label['id']}/review",
            json={
                "decision": "approve",
                "note": "Matches the published address list",
                "evidence": [URL],
            },
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
        shown = c.get(f"{API}/entities/{ETH}/{CONTRACT}/labels").json()
        assert [x["name"] for x in shown["approved"]] == ["Synthetic Protocol"]
        assert "never to a person" in shown["scope"]
        again = c.post(
            f"{API}/entities/labels/{label['id']}/review",
            json={"decision": "reject", "note": "changed my mind entirely"},
        )
        assert again.status_code == 409  # a decision is final


def test_stored_evidence_must_exist_and_duplicates_conflict(rig: Rig) -> None:
    seed_signal(rig)
    with TestClient(rig.app) as c:
        missing = propose(c, evidence=[{"kind": "transaction", "ref": eth_tx(999)}])
        assert missing.status_code == 422
        ok = propose(c, evidence=[{"kind": "transaction", "ref": eth_tx(1)}])
        assert ok.status_code == 201
        assert ok.json()["evidence"][0]["verified_stored"] is True
        assert propose(c, evidence=[]).status_code == 409
        assert propose(c, category="person").status_code == 422  # organisations only
        assert propose(c, ref="not-an-address", name="x").status_code == 422


def test_attribution_appears_on_signals_only_after_approval(rig: Rig) -> None:
    signal_id = seed_signal(rig)
    service = DiscoveryService(rig.repo, rig.settings, clock=lambda: NOW)
    before = service.get_signal(signal_id)["attribution"]
    assert before["labels"] == {}
    assert "unattributed" in before["note"]
    with TestClient(rig.app) as c:
        label = propose(c, evidence=[{"kind": "signal", "ref": str(signal_id)}]).json()
        mid = service.get_signal(signal_id)["attribution"]["labels"]
        assert mid == {}  # still only a proposal
        c.post(
            f"{API}/entities/labels/{label['id']}/review",
            json={
                "decision": "approve",
                "note": "Confirmed against the project's docs",
                "evidence": [URL],
            },
        )
    after = service.get_signal(signal_id)["attribution"]["labels"]
    assert after[CONTRACT][0]["name"] == "Synthetic Protocol"
    assert after[CONTRACT][0]["reviewed_by"] == "local"


class FakeLLM:
    name = "fake"
    model = "fake-1"

    def __init__(self, reply: object, available: bool = True) -> None:
        self._reply, self._available = reply, available

    def status(self) -> Any:
        return SimpleNamespace(available=self._available, reason="synthetic provider")

    def generate(self, system: str, user: str, *, timeout_s: float, max_bytes: int) -> str:
        assert "stored_signals" in user
        assert "Never name a person" in system
        return json.dumps(self._reply)


def test_ai_suggestions_are_grounded_dropped_when_not_and_never_self_approved(rig: Rig) -> None:
    signal_id = seed_signal(rig)
    svc = EntityLabelService(rig.repo)
    reply: dict[str, Any] = {
        "suggestions": [
            {
                "category": "protocol",
                "name": "Grounded",
                "rationale": "see signal",
                "evidence": [{"kind": "signal", "ref": str(signal_id)}],
            },
            {
                "category": "exchange",
                "name": "Invented",
                "rationale": "trust me",
                "evidence": [{"kind": "transaction", "ref": eth_tx(424242)}],
            },
            {"category": "exchange", "name": "No evidence", "evidence": []},
            {
                "category": "person",
                "name": "Someone",
                "evidence": [{"kind": "signal", "ref": str(signal_id)}],
            },
        ]
    }
    out = svc.suggest(FakeLLM(reply), chain=ETH, ref=CONTRACT, entity_kind="contract", actor="ana")
    assert [x["name"] for x in out["created"]] == ["Grounded"]
    assert len(out["dropped"]) == 3
    label = out["created"][0]
    assert (label["status"], label["source"], label["proposed_by"]) == ("proposed", "ai", "ai:fake")
    with pytest.raises(InvalidRecordError, match="only a human"):
        svc.review(
            label["id"], decision="approve", reviewer="ai:fake", note="self approval attempt"
        )
    with pytest.raises(InvalidRecordError, match="added by a person"):
        svc.review(label["id"], decision="approve", reviewer="ana", note="the model cited a signal")
    done = svc.review(
        label["id"],
        decision="approve",
        reviewer="ana",
        note="Verified against public docs",
        evidence=[URL],
    )
    assert done["status"] == "approved"
    assert (
        svc.suggest(
            FakeLLM({}, available=False),
            chain=ETH,
            ref=CONTRACT,
            entity_kind="contract",
            actor="ana",
        )["available"]
        is False
    )
    with pytest.raises(DuplicateRecordError):
        svc.review(label["id"], decision="reject", reviewer="ana", note="already decided here")


def test_unconfigured_model_is_reported_not_faked(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        r = c.post(
            f"{API}/entities/labels/suggest",
            json={"chain": ETH, "ref": CONTRACT, "entity_kind": "contract"},
        )
    assert r.status_code == 201
    body = r.json()
    assert body["available"] is False
    assert body["created"] == []
    assert "No AI provider is configured" in body["reason"]


def test_roles_and_a_separate_reviewer_when_authentication_is_required(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    for name, role in (("ana", "analyst"), ("bob", "analyst"), ("vic", "viewer")):
        rig.user(name, role)
    try:
        with TestClient(rig.app) as c:

            def login(name: str) -> dict[str, str]:
                r = c.post(f"{API}/auth/login", json={"username": name, "password": PASSWORD})
                return {"X-CSRF-Token": r.json()["csrf_token"]}

            viewer = login("vic")
            body: dict[str, Any] = {
                "chain": ETH,
                "ref": CONTRACT,
                "entity_kind": "contract",
                "category": "protocol",
                "name": "P",
                "evidence": [],
            }
            assert c.post(f"{API}/entities/labels", json=body, headers=viewer).status_code == 403
            assert c.get(f"{API}/entities/labels").status_code == 200
            ana = login("ana")
            made = c.post(f"{API}/entities/labels", json=body, headers=ana).json()
            assert made["proposed_by"] == "ana"
            review = {"decision": "approve", "note": "Checked the public docs", "evidence": [URL]}
            own = c.post(f"{API}/entities/labels/{made['id']}/review", json=review, headers=ana)
            assert own.status_code == 422  # cannot approve one's own proposal
            bob = login("bob")
            ok = c.post(f"{API}/entities/labels/{made['id']}/review", json=review, headers=bob)
            assert ok.status_code == 200
            assert ok.json()["reviewed_by"] == "bob"
    finally:
        rig.dispose()


def test_the_person_who_asked_the_model_cannot_approve_its_proposal(rig: Rig) -> None:
    signal_id = seed_signal(rig)
    svc = EntityLabelService(rig.repo, separate_reviewer=True)
    reply: dict[str, Any] = {
        "suggestions": [
            {
                "category": "protocol",
                "name": "Asked For",
                "evidence": [{"kind": "signal", "ref": str(signal_id)}],
            }
        ]
    }
    out = svc.suggest(FakeLLM(reply), chain=ETH, ref=CONTRACT, entity_kind="contract", actor="ana")
    label = out["created"][0]
    assert label["requested_by"] == "ana"
    with pytest.raises(InvalidRecordError, match="asked the model"):
        svc.review(
            label["id"],
            decision="approve",
            reviewer="ana",
            note="approving my own request",
            evidence=[URL],
        )
    done = svc.review(
        label["id"],
        decision="approve",
        reviewer="bob",
        note="checked by a second person",
        evidence=[URL],
    )
    assert done["status"] == "approved"


def test_a_model_may_only_cite_signals_it_was_shown_and_must_name_things_as_text(rig: Rig) -> None:
    signal_id = seed_signal(rig)
    svc = EntityLabelService(rig.repo)
    reply: dict[str, Any] = {
        "suggestions": [
            {
                "category": "protocol",
                "name": "Unseen",
                "evidence": [{"kind": "signal", "ref": "999999"}],
            },
            {
                "category": "protocol",
                "name": None,
                "evidence": [{"kind": "signal", "ref": str(signal_id)}],
            },
            {
                "category": "protocol",
                "name": "Shown",
                "evidence": [{"kind": "signal", "ref": str(signal_id)}],
            },
        ]
    }
    out = svc.suggest(FakeLLM(reply), chain=ETH, ref=CONTRACT, entity_kind="contract", actor="ana")
    assert [x["name"] for x in out["created"]] == ["Shown"]
    assert len(out["dropped"]) == 2


def test_evidence_that_was_not_checked_is_not_reported_as_verified(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        made = propose(c, evidence=[URL, {"kind": "note", "ref": "free text"}]).json()
    assert [e["verified_stored"] for e in made["evidence"]] == [None, None]


def test_a_rejected_label_can_be_proposed_again_and_odd_input_is_a_clean_error(rig: Rig) -> None:
    with TestClient(rig.app) as c:
        first = propose(c).json()
        c.post(
            f"{API}/entities/labels/{first['id']}/review",
            json={"decision": "reject", "note": "no evidence was given"},
        )
        again = propose(c, evidence=[URL])
        assert again.status_code == 201
        assert again.json()["id"] == first["id"]
        assert again.json()["status"] == "proposed"
        assert again.json()["reviewed_by"] is None
        bad_ref = propose(c, evidence=[{"kind": "signal", "ref": "²"}])
        assert bad_ref.status_code == 422  # not a 500
        hidden = propose(c, name="Exam​ple")  # zero-width space
        assert hidden.status_code == 422


def test_only_one_label_suggestion_runs_at_a_time(rig: Rig) -> None:
    svc = EntityLabelService(rig.repo, ai_concurrency=1)

    class Blocking(FakeLLM):
        def generate(self, system: str, user: str, *, timeout_s: float, max_bytes: int) -> str:
            with pytest.raises(DuplicateRecordError, match="already running"):
                svc.suggest(
                    FakeLLM({}), chain=ETH, ref=CONTRACT, entity_kind="contract", actor="bob"
                )
            return json.dumps({"suggestions": []})

    out = svc.suggest(Blocking({}), chain=ETH, ref=CONTRACT, entity_kind="contract", actor="ana")
    assert out["created"] == []
