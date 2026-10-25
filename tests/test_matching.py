"""
Unit tests for ring-level matching metrics (Jaccard matching, PR/F1).
"""

from __future__ import annotations

from fraudsim.metrics.matching import compute_precision_recall_f1, jaccard, match_rings
from fraudsim.types import DetectorType, GroundTruthRing, RingCandidate, RingType


def test_jaccard_similarity():
    assert jaccard(set(), set()) == 1.0
    assert jaccard({"a"}, set()) == 0.0
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a", "b"}, {"b", "c"}) == 1 / 3
    assert jaccard({"a", "b", "c"}, {"a", "b"}) == 2 / 3


def test_match_rings_exact_and_partial():
    gt1 = GroundTruthRing(
        ring_id="gt_1",
        ring_type=RingType.SHARED_DEVICE,
        members={"u1", "u2", "u3", "u4"},
    )
    gt2 = GroundTruthRing(
        ring_id="gt_2",
        ring_type=RingType.SHARED_CARD,
        members={"u10", "u11", "u12"},
    )

    det1 = RingCandidate(
        ring_id="det_1",
        detector=DetectorType.STATIC_CYPHER,
        ring_type=RingType.SHARED_DEVICE,
        members={"u1", "u2", "u3", "u4"},  # exact match: jaccard = 1.0
    )
    det2 = RingCandidate(
        ring_id="det_2",
        detector=DetectorType.STATIC_CYPHER,
        ring_type=RingType.SHARED_CARD,
        members={"u10", "u11"},  # partial match: jaccard = 2/3 = 0.67
    )
    det_fp = RingCandidate(
        ring_id="det_fp",
        detector=DetectorType.STATIC_CYPHER,
        ring_type=RingType.SHARED_IP,
        members={"u90", "u91"},  # false positive
    )

    matches, missed, false_positives = match_rings([det1, det2, det_fp], [gt1, gt2], threshold=0.5)

    assert len(matches) == 2
    assert missed == []
    assert false_positives == ["det_fp"]

    metrics = compute_precision_recall_f1([det1, det2, det_fp], [gt1, gt2], threshold=0.5)
    assert metrics["n_tp"] == 2
    assert metrics["n_fp"] == 1
    assert metrics["n_fn"] == 0
    assert round(metrics["precision"], 4) == round(2 / 3, 4)
    assert metrics["recall"] == 1.0


def test_match_rings_threshold_sensitivity():
    gt = GroundTruthRing(
        ring_id="gt", ring_type=RingType.SHARED_DEVICE, members={"u1", "u2", "u3", "u4"}
    )
    det = RingCandidate(
        ring_id="det",
        detector=DetectorType.STATIC_CYPHER,
        ring_type=RingType.SHARED_DEVICE,
        members={"u1", "u2"},
    )  # Jaccard = 2/4 = 0.5

    # At threshold 0.5 -> matches
    m1, missed1, _ = match_rings([det], [gt], threshold=0.5)
    assert len(m1) == 1
    assert len(missed1) == 0

    # At threshold 0.8 -> misses
    m2, missed2, fps2 = match_rings([det], [gt], threshold=0.8)
    assert len(m2) == 0
    assert len(missed2) == 1
    assert len(fps2) == 1
