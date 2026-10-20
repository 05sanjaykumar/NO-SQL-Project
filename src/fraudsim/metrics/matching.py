"""
Ring-level matching — maps detected rings to ground-truth rings.

Used by the metrics subsystem to compute precision, recall, and F1.
"""

from __future__ import annotations

from fraudsim.types import GroundTruthRing, MatchResult, RingCandidate


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity of two sets."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_rings(
    detected: list[RingCandidate],
    ground_truth: list[GroundTruthRing],
    threshold: float = 0.5,
) -> tuple[list[MatchResult], list[str], list[str]]:
    """
    Match detected rings to ground-truth rings using Jaccard similarity.

    Returns
    -------
    matches : list[MatchResult]
        All pairings above threshold.
    missed_ring_ids : list[str]
        Ground-truth ring_ids with no detected match.
    false_positive_ids : list[str]
        Detected ring_ids with no ground-truth match.
    """
    matches: list[MatchResult] = []
    matched_true: set[str] = set()
    matched_detected: set[str] = set()

    # Greedy matching: best Jaccard first
    pairs: list[tuple[float, RingCandidate, GroundTruthRing]] = []
    for d in detected:
        for g in ground_truth:
            j = jaccard(d.members, g.members)
            if j >= threshold:
                pairs.append((j, d, g))

    pairs.sort(key=lambda x: x[0], reverse=True)

    for j, d, g in pairs:
        if d.ring_id in matched_detected or g.ring_id in matched_true:
            continue
        matches.append(
            MatchResult(
                detected_ring_id=d.ring_id,
                true_ring_id=g.ring_id,
                jaccard=j,
                is_true_positive=True,
                members_detected=set(d.members),
                members_true=set(g.members),
            )
        )
        matched_true.add(g.ring_id)
        matched_detected.add(d.ring_id)

    missed_ring_ids = [g.ring_id for g in ground_truth if g.ring_id not in matched_true]
    false_positive_ids = [d.ring_id for d in detected if d.ring_id not in matched_detected]

    return matches, missed_ring_ids, false_positive_ids


def compute_precision_recall_f1(
    detected: list[RingCandidate],
    ground_truth: list[GroundTruthRing],
    threshold: float = 0.5,
) -> dict[str, float]:
    """
    Compute ring-level precision, recall, and F1.

    Returns a dict with keys: precision, recall, f1, n_detected,
    n_true, n_tp, n_fp, n_fn.
    """
    matches, missed, false_positives = match_rings(detected, ground_truth, threshold)

    tp = len(matches)
    fp = len(false_positives)
    fn = len(missed)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "n_detected": len(detected),
        "n_true": len(ground_truth),
        "n_tp": tp,
        "n_fp": fp,
        "n_fn": fn,
    }
