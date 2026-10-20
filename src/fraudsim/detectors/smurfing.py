"""
Smurfing detector — stub for Milestone 3.

Detects fan-in/fan-out patterns within a time window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fraudsim.detectors.base import BaseDetector, DetectorConfig
from fraudsim.graph.neo4j_client import Neo4jClient
from fraudsim.types import DetectorType, RingCandidate


@dataclass(slots=True)
class SmurfingConfig(DetectorConfig):
    """Configuration for the SmurfingDetector."""

    detector_type: DetectorType = DetectorType.SMURFING
    name: str = "SmurfingDetector"
    time_window_seconds: float = 3600.0
    min_fan_count: int = 3
    amount_threshold: float = 10000.0
    extra: dict[str, Any] = field(default_factory=dict)


class SmurfingDetector(BaseDetector):
    """
    Detects fan-in/fan-out smurfing patterns.

    Stub — full implementation in Milestone 3.
    """

    def __init__(
        self,
        client: Neo4jClient,
        config: SmurfingConfig | None = None,
    ) -> None:
        super().__init__(client, config or SmurfingConfig())

    def detect(
        self,
        dirty_set: set[str] | None = None,
        ts: datetime | None = None,
    ) -> list[RingCandidate]:
        # Milestone 3 implementation
        return []
