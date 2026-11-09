"""Acquisition gate, isolated fixtures: a live source runs only with a real contact AND a reviewed, approved policy.
No network is used: the fake fetcher is never asked for anything the gate refuses."""
import pytest

from conftest import FakeFetcher
from watchtower import config
from watchtower.policy import SourceNotApproved, gate
from watchtower.sources import ADAPTERS

REAL_CONTACT = "ops@watchtower-research.org"


@pytest.fixture
def real_contact(monkeypatch):
    monkeypatch.setattr(config, "CONTACT", REAL_CONTACT)
    return REAL_CONTACT


def test_a_live_adapter_without_a_reviewed_policy_is_refused(db, real_contact):
    with pytest.raises(SourceNotApproved, match="policy status is missing"):
        gate(db, ADAPTERS["adafruit"], FakeFetcher())


def test_ebay_in_production_without_a_policy_is_refused(db, real_contact, monkeypatch):
    monkeypatch.setenv("EBAY_ENV", "production")
    adapter = ADAPTERS["ebay_browse"]
    assert adapter.kind == "live"                       # production eBay is a live source, so it is gated
    with pytest.raises(SourceNotApproved):
        gate(db, adapter, FakeFetcher())


def test_ebay_sandbox_is_labelled_and_not_gated(db, monkeypatch):
    monkeypatch.delenv("EBAY_ENV", raising=False)
    adapter = ADAPTERS["ebay_browse"]
    assert adapter.kind == "sandbox"
    gate(db, adapter, FakeFetcher())                    # returns without raising: test data, no commercial collection


def test_a_placeholder_contact_is_refused_even_for_a_live_source(db, monkeypatch):
    monkeypatch.setattr(config, "CONTACT", "contact@example.com")
    with pytest.raises(SourceNotApproved, match="WATCHTOWER_CONTACT"):
        gate(db, ADAPTERS["sparkfun"], FakeFetcher())
