"""
Milestone 9: property tests for ring matching (hypothesis) and per-rule tests of
the static Cypher baseline on hand-built micro-graphs.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from test_detectors import G, ids

from fraudsim.detectors.static_cypher import StaticCypherBaseline, StaticCypherConfig
from fraudsim.metrics.matching import compute_precision_recall_f1, jaccard, match_rings
from fraudsim.types import DetectorType, GroundTruthRing, RingCandidate, RingType

# No per-example deadline: these properties are about results, not speed, and a
# busy CI machine must not turn a slow example into a failure.
settings.register_profile("no_deadline", deadline=None)
settings.load_profile("no_deadline")

USERS = [f"u{i}" for i in range(30)]
member_sets = st.sets(st.sampled_from(USERS), min_size=1, max_size=8)


def _true(rings: list[set[str]]) -> list[GroundTruthRing]:
    return [
        GroundTruthRing(ring_id=f"t{i}", ring_type=RingType.SHARED_DEVICE, members=m)
        for i, m in enumerate(rings)
    ]


def _det(rings: list[set[str]]) -> list[RingCandidate]:
    return [
        RingCandidate(
            ring_id=f"d{i}",
            detector=DetectorType.HUB_AWARE,
            ring_type=RingType.SHARED_DEVICE,
            members=m,
        )
        for i, m in enumerate(rings)
    ]


# ─── Jaccard ──────────────────────────────────────────────────────────


@given(member_sets, member_sets)
def test_jaccard_is_symmetric_and_bounded(a: set[str], b: set[str]) -> None:
    j = jaccard(a, b)
    assert j == jaccard(b, a)
    assert 0.0 <= j <= 1.0
    assert (j == 1.0) == (a == b)
    assert (j == 0.0) == (not a & b)


@given(member_sets, member_sets, member_sets)
def test_jaccard_distance_triangle_inequality(a: set[str], b: set[str], c: set[str]) -> None:
    # 1 - Jaccard is a metric on finite sets.
    d = lambda x, y: 1.0 - jaccard(x, y)  # noqa: E731
    assert d(a, c) <= d(a, b) + d(b, c) + 1e-12


# ─── Matching ─────────────────────────────────────────────────────────

rings = st.lists(member_sets, min_size=0, max_size=6)
thresholds = st.sampled_from([0.3, 0.5, 0.8])


@settings(max_examples=200)
@given(rings, rings, thresholds)
def test_matching_is_one_to_one_and_partitions_rings(
    det: list[set[str]], true: list[set[str]], t: float
) -> None:
    matches, missed, fps = match_rings(_det(det), _true(true), threshold=t)
    matched_true = [m.true_ring_id for m in matches]
    matched_det = [m.detected_ring_id for m in matches]
    assert len(set(matched_true)) == len(matched_true)  # each true ring at most once
    assert len(set(matched_det)) == len(matched_det)  # each flag at most once
    assert all(m.jaccard >= t for m in matches)
    assert sorted(matched_true + missed) == sorted(f"t{i}" for i in range(len(true)))
    assert sorted(matched_det + fps) == sorted(f"d{i}" for i in range(len(det)))


@settings(max_examples=200)
@given(rings, rings)
def test_raising_the_threshold_never_adds_matches(
    det: list[set[str]], true: list[set[str]]
) -> None:
    low = {
        (m.detected_ring_id, m.true_ring_id) for m in match_rings(_det(det), _true(true), 0.3)[0]
    }
    high = {
        (m.detected_ring_id, m.true_ring_id) for m in match_rings(_det(det), _true(true), 0.8)[0]
    }
    assert len(high) <= len(low)


@given(st.lists(member_sets, min_size=1, max_size=6, unique_by=frozenset))
def test_perfect_detector_scores_one(true: list[set[str]]) -> None:
    scores = compute_precision_recall_f1(_det(true), _true(true))
    assert scores["precision"] == scores["recall"] == scores["f1"] == 1.0


@given(rings, rings)
def test_scores_are_bounded(det: list[set[str]], true: list[set[str]]) -> None:
    scores = compute_precision_recall_f1(_det(det), _true(true))
    assert all(0.0 <= v <= 1.0 for v in scores.values() if isinstance(v, float))


# ─── Static Cypher rules, one at a time ───────────────────────────────

ONLY = {
    "q1": {
        "enable_q2_shared_card": False,
        "enable_q3_money_cycle": False,
        "enable_q4_shared_ip": False,
    },
    "q2": {
        "enable_q1_shared_device": False,
        "enable_q3_money_cycle": False,
        "enable_q4_shared_ip": False,
    },
    "q3": {
        "enable_q1_shared_device": False,
        "enable_q2_shared_card": False,
        "enable_q4_shared_ip": False,
    },
    "q4": {
        "enable_q1_shared_device": False,
        "enable_q2_shared_card": False,
        "enable_q3_money_cycle": False,
    },
}


def _micro() -> G:
    g = G().users(*ids("d", 3), *ids("c", 3), *ids("x", 3), *ids("i", 3), *ids("h", 15))
    g.share(ids("d", 3), "device", "dev1")  # Q1: shared device
    g.share(ids("c", 3), "card", "card1")  # Q2: shared card
    x1, x2, x3 = ids("x", 3)  # Q3: money cycle
    g.txn(x1, x2, 100, 1).txn(x2, x3, 95, 2).txn(x3, x1, 90, 3)
    g.share(ids("i", 3), "ip", "ip1")  # Q4: small shared IP
    g.share(ids("h", 15), "ip", "office")  # Q4: busy IP hub (15 users)
    return g


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("q1", {"d01", "d02", "d03"}),
        ("q2", {"c01", "c02", "c03"}),
        ("q3", {"x01", "x02", "x03"}),
        ("q4", {"i01", "i02", "i03"}),
    ],
)
def test_each_static_rule_alone(rule: str, expected: set[str]) -> None:
    g = _micro()
    store = g.store()
    det = StaticCypherBaseline(
        config=StaticCypherConfig(max_degree_cap=10, **ONLY[rule]), store=store
    )
    groups = {frozenset(c.members) for c in det.detect(g.full())}
    assert groups == {frozenset(expected)}


@pytest.mark.parametrize(("cap", "hub_flagged"), [(10, False), (20, True)])
def test_degree_cap_hides_or_shows_the_busy_ip(cap: int, hub_flagged: bool) -> None:
    g = _micro()
    store = g.store()
    det = StaticCypherBaseline(
        config=StaticCypherConfig(max_degree_cap=cap, **ONLY["q4"]), store=store
    )
    groups = {frozenset(c.members) for c in det.detect(g.full())}
    assert (frozenset(ids("h", 15)) in groups) is hub_flagged
