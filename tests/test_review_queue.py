"""Loop C, iteration 2: one question per listing pair in the human review queue.

Found on the real DB (mission 004): 22 of the 23 pending candidates were 11 mirrored pairs (A -> B's product and
B -> A's product). That doubled the review work and allowed contradictory decisions: rejecting one side left the
mirror pending, so the same pair could still be accepted afterwards."""
from datetime import datetime

from sqlalchemy import select

from watchtower.collector import ensure_sources
from watchtower.ingest import ingest_listing
from watchtower.matching import prune_candidates, rematch_all, resolve_candidate
from watchtower.models import Listing, MatchCandidate, Source
from watchtower.sources import RawListing

T0 = datetime(2026, 11, 6, 12, 0)
A_TITLE, B_TITLE = "Raspberry Pi 3 A+", "Raspberry Pi Model 3 A+"  # a real ambiguous pair from the two merchants


def _raw(ext, title):
    return RawListing(external_id=ext, url=f"https://x.example/{ext}", title=title, price_minor=2500, currency="USD",
                      brand="Raspberry Pi", category="maker-electronics", availability="in_stock")


def _world(db):
    ensure_sources(db)
    s = db.Session()
    srcs = []
    for key in ("shopa", "shopb"):
        src = Source(key=key, name=key, base_url=f"https://{key}.example", currency="USD", kind="live")
        s.add(src)
        s.flush()
        srcs.append(src)
    a, *_ = ingest_listing(s, srcs[0], None, _raw("a1", A_TITLE), T0)
    b, *_ = ingest_listing(s, srcs[1], None, _raw("b1", B_TITLE), T0)
    s.flush()
    rematch_all(s)
    s.flush()
    return s, a, b


def _add_legacy_mirror(s, a, b):
    """The reverse of the queued candidate, as a queue written before this fix would also hold."""
    (c,) = _pending(s)
    l, other = (a, b) if c.listing_id == b.id else (b, a)
    m = MatchCandidate(listing_id=l.id, product_id=other.product_id, score=0.6, reasons=["legacy mirror"])
    s.add(m)
    s.flush()
    return c, m


def _pending(s):
    return s.scalars(select(MatchCandidate).where(MatchCandidate.status == "pending")).all()


def test_an_ambiguous_pair_is_asked_once_not_in_both_directions(db):
    s, a, b = _world(db)
    assert a.product_id != b.product_id
    assert len(_pending(s)) == 1
    assert a.match_status == b.match_status == "review"  # both listings show they are awaiting a decision


def test_rejecting_a_pair_closes_its_mirror_so_it_can_never_be_accepted_later(db):
    s, a, b = _world(db)
    first, mirror = _add_legacy_mirror(s, a, b)  # a queue written before this fix still holds both directions
    resolve_candidate(s, first.id, accept=False)
    s.flush()
    assert _pending(s) == [] and mirror.status == "rejected"
    assert a.match_status == b.match_status == "self" and a.product_id != b.product_id
    resolve_candidate(s, mirror.id, accept=True)  # no longer pending: ignored
    rematch_all(s)
    s.flush()
    assert a.product_id != b.product_id and _pending(s) == []  # never re-queued


def test_accepting_a_pair_confirms_both_listings_and_leaves_no_mirror(db):
    s, a, b = _world(db)
    first, mirror = _add_legacy_mirror(s, a, b)
    resolve_candidate(s, first.id, accept=True)
    s.flush()
    assert a.product_id == b.product_id and _pending(s) == []
    assert a.match_status == b.match_status == "confirmed"


def test_prune_collapses_existing_mirrored_pairs_and_keeps_both_listings_in_review(db):
    s, a, b = _world(db)
    _add_legacy_mirror(s, a, b)
    prune_candidates(s)
    s.flush()
    assert len(_pending(s)) == 1
    assert a.match_status == b.match_status == "review"


def test_prune_keeps_distinct_questions_about_the_same_target_product(db):
    """Two different listings proposed for the same product are two decisions, not a mirror."""
    s, a, b = _world(db)
    (c,) = _pending(s)
    target = s.get(Listing, c.listing_id)
    other_side = a if target is b else b
    src = Source(key="shopc", name="shopc", base_url="https://shopc.example", currency="USD", kind="live")
    s.add(src)
    s.flush()
    raw = _raw("c1", "Raspberry Pi 3 Model A+")
    raw.category = "laptop"  # enter unmatched (ingest would otherwise auto-merge it), then place it by hand
    x, *_ = ingest_listing(s, src, None, raw, T0)
    s.flush()
    x.category, x.product_id, x.match_status = "maker-electronics", target.product_id, "auto"  # same product as the queued listing, another merchant
    s.add(MatchCandidate(listing_id=x.id, product_id=c.product_id, score=0.6, reasons=["second listing, same question target"]))
    s.flush()
    prune_candidates(s)
    s.flush()
    assert {(m.listing_id, m.product_id) for m in _pending(s)} == {(target.id, other_side.product_id), (x.id, other_side.product_id)}


# --- independent review of iteration 2: with ONE candidate (the normal state now) the partner listing was never updated
def test_rejecting_the_single_candidate_returns_both_listings_to_self(db):
    s, a, b = _world(db)
    (c,) = _pending(s)
    resolve_candidate(s, c.id, accept=False)
    s.flush()
    assert _pending(s) == [] and a.match_status == b.match_status == "self"


def test_accepting_the_single_candidate_confirms_both_listings(db):
    s, a, b = _world(db)
    (c,) = _pending(s)
    resolve_candidate(s, c.id, accept=True)
    s.flush()
    assert a.product_id == b.product_id and a.match_status == b.match_status == "confirmed"
    prune_candidates(s)  # later maintenance must not undo the human decision
    s.flush()
    assert a.match_status == b.match_status == "confirmed"


def test_rejecting_another_question_never_downgrades_a_confirmed_listing(db):
    s, a, b = _world(db)
    (c,) = _pending(s)
    resolve_candidate(s, c.id, accept=True)
    src = Source(key="shopc", name="shopc", base_url="https://shopc.example", currency="USD", kind="live")
    s.add(src)
    s.flush()
    raw = _raw("c1", "Raspberry Pi 3 Model A Plus")
    raw.category = "laptop"
    x, *_ = ingest_listing(s, src, None, raw, T0)
    s.flush()
    x.category = "maker-electronics"
    q = MatchCandidate(listing_id=a.id, product_id=x.product_id, score=0.6, reasons=["another question about a"])
    s.add(q)
    s.flush()
    resolve_candidate(s, q.id, accept=False)
    s.flush()
    assert a.match_status == b.match_status == "confirmed" and a.product_id == b.product_id


def test_accept_resets_listings_whose_question_pointed_at_the_vanished_product(db):
    """Review: y proposed for l's (singleton) product; accepting l elsewhere deletes that product and y's question."""
    s, a, b = _world(db)
    (c,) = _pending(s)
    l = s.get(Listing, c.listing_id)
    src = Source(key="shopc", name="shopc", base_url="https://shopc.example", currency="USD", kind="live")
    s.add(src)
    s.flush()
    raw = _raw("c1", "Raspberry Pi 3 Model A Plus")
    raw.category = "laptop"
    y, *_ = ingest_listing(s, src, None, raw, T0)
    s.flush()
    y.category, y.match_status = "maker-electronics", "review"
    s.add(MatchCandidate(listing_id=y.id, product_id=l.product_id, score=0.6, reasons=["y -> l's product"]))
    s.flush()
    resolve_candidate(s, c.id, accept=True)
    s.flush()
    assert y.match_status == "self"


def test_accept_confirms_a_lone_self_member_of_the_target(db):
    s, a, b = _world(db)
    (c,) = _pending(s)
    l = s.get(Listing, c.listing_id)
    m = b if l is a else a
    m.match_status = "self"  # the common case: a lone listing that l was proposed against
    s.flush()
    resolve_candidate(s, c.id, accept=True)
    s.flush()
    assert l.product_id == m.product_id and m.match_status == "confirmed"


def test_a_listing_sharing_a_product_with_another_shop_is_never_labelled_self(db):
    """Product page showed 'match self 0.60' beside an Adafruit listing of the same product (6 such rows on the real snapshot)."""
    from watchtower.matching import _members
    ensure_sources(db)
    s = db.Session()
    srcs = []
    for key in ("shopa", "shopb"):
        src = Source(key=key, name=key, base_url=f"https://{key}.example", currency="USD", kind="live")
        s.add(src)
        s.flush()
        srcs.append(src)
    a, *_ = ingest_listing(s, srcs[0], None, _raw("a1", "Raspberry Pi Zero 2 W"), T0)
    b, *_ = ingest_listing(s, srcs[1], None, _raw("b1", "Raspberry Pi Zero 2 W"), T0)
    s.flush()
    assert a.product_id == b.product_id  # unambiguous: merged automatically at ingest
    b.match_status, b.match_confidence = "self", 0.6  # the state legacy runs left behind
    s.flush()
    out = prune_candidates(s)
    s.flush()
    assert b.match_status == "auto" and a.match_status == "auto" and (b.match_confidence or 0) > 0.6 and out["self_to_auto"] == 1
    # an AMBIGUOUS shared product is not healed to 'auto' (the matcher would not have merged it): left for verify to report
    b.match_status = "self"
    b.title = "Raspberry Pi Zero 2 W Kit"
    s.flush()
    assert prune_candidates(s)["self_to_auto"] == 0 and b.match_status == "self"


# --- auditable decisions (completion programme, milestone 3) -----------------------------------------------------
def test_every_review_decision_records_who_when_and_the_evidence_it_was_based_on(db):
    from datetime import timedelta
    s, a, b = _world(db)
    (c,) = _pending(s)
    reasons_then = list(c.reasons)
    when = T0 + timedelta(days=1)
    resolve_candidate(s, c.id, accept=False, by="admin", now=when, note="different revision")
    s.flush()
    assert c.status == "rejected" and c.decided_at == when and c.decided_by == "admin" and c.decision_note == "different revision"
    assert c.evidence["reasons"] == reasons_then and c.evidence["score"] == c.score
    assert {t["title"] for t in c.evidence["titles"]} == {A_TITLE, B_TITLE}  # what the reviewer actually saw
    assert c.evidence["decision"] == "rejected"


def test_a_mirror_closed_by_a_decision_carries_the_same_audit_trail(db):
    s, a, b = _world(db)
    first, mirror = _add_legacy_mirror(s, a, b)
    resolve_candidate(s, first.id, accept=True, by="admin", note="same board")
    s.flush()
    assert mirror.status == "accepted" and mirror.decided_by == "admin" and mirror.decided_at is not None
    assert "closed with" in (mirror.decision_note or "")


def test_an_already_decided_candidate_keeps_its_original_audit_record(db):
    from datetime import timedelta
    s, a, b = _world(db)
    (c,) = _pending(s)
    resolve_candidate(s, c.id, accept=False, by="alice", now=T0)
    resolve_candidate(s, c.id, accept=True, by="bob", now=T0 + timedelta(days=1))  # ignored: no longer pending
    s.flush()
    assert c.status == "rejected" and c.decided_by == "alice" and c.decided_at == T0
