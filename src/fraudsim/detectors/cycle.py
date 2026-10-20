"""
Temporal cycle detector — stub for Milestone 3.

Bounded-length, time-windowed directed cycles with amount-consistency checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fraudsim.detectors.base import BaseDetector, DetectorConfig
from fraudsim.graph.neo4j_client import Neo4jClient
from fraudsim.types import DetectorType, RingCandidate


@dataclass(slots=True)
class TemporalCycleConfig(DetectorConfig):
    """Configuration for the TemporalCycleDetector."""

    detector_type: DetectorType = DetectorType.TEMPORAL_CYCLE
    name: str = "TemporalCycleDetector"
    min_cycle_length: int = 3
    max_cycle_length: int = 6
    time_window_seconds: float = 3600.0
    amount_tolerance: float = 0.2  # 20% tolerance for amount consistency
    extra: dict[str, Any] = field(default_factory=dict)


class TemporalCycleDetector(BaseDetector):
    """
    Detects directed money cycles within a time window.

    Stub — full implementation in Milestone 3.
    """

    def __init__(
        self,
        client: Neo4jClient,
        config: TemporalCycleConfig | None = None,
    ) -> None:
        super().__init__(client, config or TemporalCycleConfig())

    def detect(
        self,
        dirty_set: set[str] | None = None,
        ts: datetime | None = None,
    ) -> list[RingCandidate]:
        # Milestone 3 implementation
        return []
