"""
Ring scorer — stub for Milestone 4.

Two-stage design: rules generate candidates, then a calibrated model
produces a risk score in [0,1].
"""

from __future__ import annotations

from typing import Any

from fraudsim.types import RingCandidate


class RingScorer:
    """
    Scores ring candidates using a trained model.

    Stub — full implementation in Milestone 4.
    """

    def __init__(self, model_type: str = "logistic_regression") -> None:
        self._model_type = model_type
        self._model: Any = None

    def score(self, candidate: RingCandidate) -> float:
        """Return a risk score in [0, 1] for a ring candidate."""
        # Milestone 4: use the trained model
        # For now, return the detector's raw score
        return candidate.score

    def score_batch(self, candidates: list[RingCandidate]) -> list[float]:
        """Score a batch of candidates."""
        return [self.score(c) for c in candidates]
