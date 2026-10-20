"""
Community detector — stub for Milestone 3.

Uses Neo4j GDS Louvain/WCC over a rolling projection as an alternative baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fraudsim.detectors.base import BaseDetector, DetectorConfig
from fraudsim.graph.neo4j_client import Neo4jClient
from fraudsim.types import DetectorType, RingCandidate


@dataclass(slots=True)
class CommunityConfig(DetectorConfig):
    """Configuration for the CommunityDetector."""

    detector_type: DetectorType = DetectorType.COMMUNITY
    name: str = "CommunityDetector"
    algorithm: str = "louvain"  # or "wcc"
    min_community_size: int = 3
    extra: dict[str, Any] = field(default_factory=dict)


class CommunityDetector(BaseDetector):
    """
    Community detection via Neo4j GDS (Louvain or WCC).

    Stub — full implementation in Milestone 3.
    """

    def __init__(
        self,
        client: Neo4jClient,
        config: CommunityConfig | None = None,
    ) -> None:
        super().__init__(client, config or CommunityConfig())

    def detect(
        self,
        dirty_set: set[str] | None = None,
        ts: datetime | None = None,
    ) -> list[RingCandidate]:
        # Milestone 3 implementation
        return []
