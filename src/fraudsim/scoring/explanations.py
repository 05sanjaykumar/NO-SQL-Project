"""
Explanation generator — stub for Milestone 4.

Generates per-flag explanations including feature contributions,
plain-English reasons, evidence subgraphs, and counterfactuals.
"""

from __future__ import annotations

from fraudsim.types import Explanation, RingCandidate


class ExplanationGenerator:
    """
    Generates explanations for ring flags.

    Stub — full implementation in Milestone 4.
    """

    def explain(self, candidate: RingCandidate) -> Explanation:
        """Generate an explanation for a ring candidate."""
        return Explanation(
            ring_id=candidate.ring_id,
            score=candidate.score,
            feature_contributions={},
            reason_text=f"Ring {candidate.ring_id} flagged by {candidate.detector.value}",
            evidence_subgraph=candidate.evidence,
            counterfactual="",
            confidence=candidate.confidence,
            calibration_bucket="",
        )
