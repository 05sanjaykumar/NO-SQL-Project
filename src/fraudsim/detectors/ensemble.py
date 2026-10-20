"""
Ensemble detector — merges candidates from all detectors into unified ring objects.

Stub for Milestone 3. The ensemble handles:
- Deduplication across detectors
- Jaccard-based ring identity matching
- Hysteresis for ring status transitions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from fraudsim.detectors.base import BaseDetector, DetectorConfig
from fraudsim.graph.neo4j_client import Neo4jClient
from fraudsim.types import DetectorType, RingCandidate

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class EnsembleConfig(DetectorConfig):
    """Configuration for the Ensemble detector."""

    detector_type: DetectorType = DetectorType.ENSEMBLE
    name: str = "Ensemble"
    jaccard_merge_threshold: float = 0.5
    hysteresis_confirm: int = 3
    hysteresis_dissolve: int = 5
    extra: dict[str, Any] = field(default_factory=dict)


class EnsembleDetector(BaseDetector):
    """
    Merges candidates from all sub-detectors into unified rings.

    Stub — full implementation in Milestone 3.
    """

    def __init__(
        self,
        client: Neo4jClient,
        sub_detectors: list[BaseDetector] | None = None,
        config: EnsembleConfig | None = None,
    ) -> None:
        super().__init__(client, config or EnsembleConfig())
        self._sub_detectors = sub_detectors or []
        self._active_rings: dict[str, RingCandidate] = {}

    def detect(
        self,
        dirty_set: set[str] | None = None,
        ts: datetime | None = None,
    ) -> list[RingCandidate]:
        """Run all sub-detectors and merge their results."""
        all_candidates: list[RingCandidate] = []
        for detector in self._sub_detectors:
            if detector.enabled:
                candidates = detector.detect(dirty_set=dirty_set, ts=ts)
                all_candidates.extend(candidates)

        # Milestone 3: implement Jaccard merging, identity tracking, hysteresis
        logger.info(
            "ensemble_detect",
            n_sub_candidates=len(all_candidates),
            n_detectors=len(self._sub_detectors),
        )
        return all_candidates
