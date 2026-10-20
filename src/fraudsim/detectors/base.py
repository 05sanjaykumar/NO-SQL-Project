"""
Base detector interface.

Every detector implements this ABC. The uniform interface enables:
- Ablation: one flag to enable/disable each detector
- Ensemble: detectors compose naturally
- Testing: mock detectors for unit tests
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fraudsim.graph.neo4j_client import Neo4jClient
from fraudsim.types import DetectorType, RingCandidate


@dataclass(slots=True)
class DetectorConfig:
    """
    Base configuration for a detector.

    Subclass for detector-specific settings.
    """

    enabled: bool = True
    name: str = ""
    detector_type: DetectorType = DetectorType.STATIC_CYPHER
    extra: dict[str, Any] = field(default_factory=dict)


class BaseDetector(ABC):
    """
    Abstract base class for all fraud ring detectors.

    Each detector:
    1. Receives a dirty set (set of user_ids touched by recent events)
    2. Queries the graph for suspicious patterns in that region
    3. Returns a list of RingCandidate objects
    """

    def __init__(self, client: Neo4jClient, config: DetectorConfig) -> None:
        self._client = client
        self._config = config

    @property
    def name(self) -> str:
        return self._config.name or self.__class__.__name__

    @property
    def detector_type(self) -> DetectorType:
        return self._config.detector_type

    @property
    def config(self) -> DetectorConfig:
        return self._config

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @abstractmethod
    def detect(
        self,
        dirty_set: set[str] | None = None,
        ts: datetime | None = None,
    ) -> list[RingCandidate]:
        """
        Run detection and return candidate rings.

        Parameters
        ----------
        dirty_set
            Set of user_ids to re-evaluate. If None, evaluate the entire graph
            (full recomputation mode, used by StaticCypherBaseline).
        ts
            Current simulation time. Detectors may use this for windowing.

        Returns
        -------
        list[RingCandidate]
            Detected ring candidates.
        """

    def detect_full(self, ts: datetime | None = None) -> list[RingCandidate]:
        """Force full-graph detection (no dirty set optimization)."""
        return self.detect(dirty_set=None, ts=ts)
