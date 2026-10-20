"""
Hub-Aware Shared Attribute detector — stub for Milestone 3.

This is the core novel detector (research contribution C1): weighted
user-user projection where edge weight = 1/log(1 + degree(attribute))
times a time decay factor. Replaces the hard degree cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fraudsim.detectors.base import BaseDetector, DetectorConfig
from fraudsim.graph.neo4j_client import Neo4jClient
from fraudsim.types import DetectorType, RingCandidate


@dataclass(slots=True)
class HubAwareConfig(DetectorConfig):
    """Configuration for the HubAwareSharedAttribute detector."""

    detector_type: DetectorType = DetectorType.HUB_AWARE
    name: str = "HubAwareSharedAttribute"
    weight_method: str = "inverse_log_degree"  # or "idf", "adamic_adar"
    time_decay_half_life: float = 3600.0  # seconds
    min_component_size: int = 3
    density_threshold: float = 0.5
    extra: dict[str, Any] = field(default_factory=dict)


class HubAwareSharedAttribute(BaseDetector):
    """
    Weighted user-user projection with hub-aware edge weighting.

    Stub — full implementation in Milestone 3.
    """

    def __init__(
        self,
        client: Neo4jClient,
        config: HubAwareConfig | None = None,
    ) -> None:
        super().__init__(client, config or HubAwareConfig())

    def detect(
        self,
        dirty_set: set[str] | None = None,
        ts: datetime | None = None,
    ) -> list[RingCandidate]:
        # Milestone 3 implementation
        return []
