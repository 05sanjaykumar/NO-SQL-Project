"""
Ring feature extraction — stub for Milestone 4.

Extracts features from ring candidates for the scoring model.
"""

from __future__ import annotations

from dataclasses import dataclass

from fraudsim.types import RingCandidate


@dataclass(frozen=True, slots=True)
class RingFeatures:
    """Feature vector for a ring candidate."""

    ring_id: str
    size: int = 0
    density: float = 0.0
    attribute_sharing_type: str = ""
    hub_adjusted_weight: float = 0.0
    cycle_present: bool = False
    cycle_length: int = 0
    temporal_burstiness: float = 0.0
    account_age_similarity: float = 0.0
    amount_mean: float = 0.0
    amount_std: float = 0.0
    growth_rate: float = 0.0
    risk_signal_fraction: float = 0.0
    hub_exposure: float = 0.0

    def to_vector(self) -> list[float]:
        """Convert to a numeric feature vector for the scoring model."""
        return [
            float(self.size),
            self.density,
            self.hub_adjusted_weight,
            float(self.cycle_present),
            float(self.cycle_length),
            self.temporal_burstiness,
            self.account_age_similarity,
            self.amount_mean,
            self.amount_std,
            self.growth_rate,
            self.risk_signal_fraction,
            self.hub_exposure,
        ]

    @staticmethod
    def feature_names() -> list[str]:
        return [
            "size",
            "density",
            "hub_adjusted_weight",
            "cycle_present",
            "cycle_length",
            "temporal_burstiness",
            "account_age_similarity",
            "amount_mean",
            "amount_std",
            "growth_rate",
            "risk_signal_fraction",
            "hub_exposure",
        ]


def extract_features(candidate: RingCandidate) -> RingFeatures:
    """
    Extract features from a ring candidate.

    Stub — full implementation in Milestone 4.
    """
    return RingFeatures(
        ring_id=candidate.ring_id,
        size=len(candidate.members),
    )
