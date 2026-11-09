"""A listing without a product must never be treated as a member of 'product None' (which SQL reads as every unmatched
listing), and a question without a target product or score must not be queued."""
from tests._ml_helpers import mk_listing, mk_source
from watchtower import matching
from watchtower.models import MatchCandidate
from sqlalchemy import select


def test_members_of_no_product_is_empty_not_every_unmatched_listing(db):
    with db.session() as s:
        src = mk_source(s, "guardsrc")
        mk_listing(s, src, "u1", "Unmatched A", price=500)     # product_id NULL
        mk_listing(s, src, "u2", "Unmatched B", price=600)     # product_id NULL
        s.commit()
    with db.session() as s:
        assert matching._members(s, None) == []


def test_a_question_without_a_target_product_is_not_queued(db):
    with db.session() as s:
        src = mk_source(s, "guardq")
        l = mk_listing(s, src, "q1", "Question board", price=500)
        matching._queue(s, l, None, 0.5, ["no target"])
        matching._queue(s, l, 1, None, ["no score"])
        s.commit()
    with db.session() as s:
        assert s.scalars(select(MatchCandidate)).all() == []
