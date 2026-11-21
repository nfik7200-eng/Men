from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bip.intel import graph_explorer as ge
from bip.intel.common import J

C, A, B, T = "0xc", "0xa", "0xb", "0xtoken"


def d(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def ev(tx: str, day: int | None, status: str, amount: int | None = 10, pos: int = 100) -> J:
    return {
        "tx_id": tx,
        "position": pos + (day or 0),
        "block_time": d(day) if day else None,
        "asset": "native",
        "amount_raw": None if amount is None else str(amount),
        "provider": "p1",
        "verification": "x",
        "verification_status": status,
        "provider_response": {"provider": "p1", "request_id": f"r-{tx}", "raw_ref": f"raw/{tx}"},
    }


def edge(eid: str, s: str, t: str, evs: list[J], count: int | None = None) -> J:
    return {
        "id": eid,
        "source": s,
        "target": t,
        "kind": "native_transfer",
        "asset": "native",
        "count": len(evs) if count is None else count,
        "amount_raw": "0",
        "first_seen": None,
        "last_seen": None,
        "evidence": evs,
        "verification": {},
    }


@pytest.fixture
def g() -> J:
    return {
        "chain": "ethereum",
        "center": C,
        "nodes": [{"id": x, "kind": "address", "depth": 0, "degree": 0} for x in (C, A, B)],
        "edges": [
            edge(
                "e0",
                C,
                A,
                [
                    ev("t1", 1, "cross_verified"),
                    ev("t2", 5, "single_source"),
                    ev("t3", 9, "conflict"),
                ],
            ),
            edge("e1", B, C, [ev("t4", 20, "unverified")]),
        ],
        "verification": {},
    }


def test_no_filter_keeps_everything_and_recomputes(g: J) -> None:
    out = ge.explore(g)
    assert len(out["edges"]) == 2
    assert len(out["nodes"]) == 3
    e0 = out["edges"][0]
    assert e0["count"] == 3
    assert e0["amount_raw"] == "30"
    assert e0["classes"] == {"hypothesis": 1, "single_source": 1, "verified": 1}
    assert out["filtered_out"]["total"] == 0


def test_time_window_slices_edges_and_hides_nodes(g: J) -> None:
    out = ge.explore(g, since=d(2), until=d(10))
    assert [e["id"] for e in out["edges"]] == ["e0"]
    assert out["edges"][0]["count"] == 2
    assert out["edges"][0]["first_seen"] == d(5).isoformat()
    assert {n["id"] for n in out["nodes"]} == {C, A}  # B had only out-of-window evidence
    assert out["filtered_out"]["outside_time_window"] == 2
    assert out["filtered_out"]["nodes_hidden"] == 1


def test_window_bounds_are_inclusive(g: J) -> None:
    out = ge.explore(g, since=d(5), until=d(5))
    assert [x["tx_id"] for x in out["edges"][0]["evidence"]] == ["t2"]


def test_status_filter(g: J) -> None:
    out = ge.explore(g, statuses={"verified"})
    assert [x["tx_id"] for e in out["edges"] for x in e["evidence"]] == ["t1"]
    assert out["verification"]["overall"] == "corroborated"
    hyp = ge.explore(g, statuses={"hypothesis"})
    assert hyp["edges"][0]["evidence"][0]["tx_id"] == "t3"
    assert hyp["filtered_out"]["by_status"] == 3


def test_unverified_counts_as_single_source() -> None:
    assert ge.evidence_class("unverified") == "single_source"
    assert ge.evidence_class("invalidated") == "hypothesis"
    assert ge.evidence_class("anything_new") == "hypothesis"


def test_undated_excluded_only_when_window_active(g: J) -> None:
    g["edges"][1]["evidence"][0]["block_time"] = None
    assert len(ge.explore(g)["edges"]) == 2
    out = ge.explore(g, since=d(1))
    assert out["filtered_out"]["undated"] == 1
    assert [e["id"] for e in out["edges"]] == ["e0"]


def test_incomplete_edge_is_not_recomputed(g: J) -> None:
    g["edges"][0]["count"] = 50  # 47 observations were not retained
    e = ge.explore(g, since=d(2))["edges"][0]
    assert e["evidence_complete"] is False
    assert e["count"] == 50
    assert e["unfiltered"]["count"] == 50


def test_amount_none_when_any_missing(g: J) -> None:
    g["edges"][0]["evidence"][0]["amount_raw"] = None
    assert ge.explore(g)["edges"][0]["amount_raw"] is None


def test_timeline_and_validation(g: J) -> None:
    tl = ge.explore(g)["timeline"]
    assert tl["start"] == d(1).isoformat()
    assert tl["end"] == d(20).isoformat()
    assert sum(b["count"] for b in tl["buckets"]) == 4
    # timeline ignores the active filter so the slider keeps its full range
    assert ge.explore(g, since=d(9))["timeline"] == tl
    with pytest.raises(ValueError, match=r"until|status"):
        ge.explore(g, since=d(9), until=d(1))
    with pytest.raises(ValueError, match=r"until|status"):
        ge.parse_statuses(["bogus"])
    assert ge.parse_statuses(None) == frozenset(ge.EVIDENCE_STATUSES)


def test_inspect_node_trail(g: J) -> None:
    out = ge.explore(g)
    t = ge.inspect_node(out, A)
    assert t is not None
    assert [r["tx_id"] for r in t["transactions"]] == ["t1", "t2", "t3"]
    assert t["block_range"] == {"min": 101, "max": 109}
    assert t["providers"] == {"p1": 3}
    assert t["transactions"][0]["provider_response"]["request_id"] == "r-t1"
    center = ge.inspect_node(out, C)
    assert center is not None
    assert center["transaction_count"] == 4
    assert ge.inspect_node(out, "0xmissing") is None


def test_empty_graph_timeline() -> None:
    out = ge.explore(
        {
            "chain": "ethereum",
            "center": C,
            "nodes": [{"id": C, "kind": "address", "depth": 0, "degree": 0}],
            "edges": [],
        }
    )
    assert out["timeline"]["buckets"] == []
    assert len(out["nodes"]) == 1
