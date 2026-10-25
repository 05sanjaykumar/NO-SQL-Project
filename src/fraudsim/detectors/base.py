"""
Base detector interface.

Every detector implements this ABC. The uniform interface enables:
- Ablation: one flag (`config.enabled`) to switch each detector off
- Ensemble: candidates from any detector merge into tracked rings
- Testing: detectors run on hand-built GraphViews, no database needed

Incremental detectors (`incremental = True`) evaluate the GraphView of the
dirty region for every micro-batch. Full-graph detectors (`incremental =
False`: the static Cypher baseline, GDS community detection) run on a
timer against the whole graph, which is how they would be deployed.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from fraudsim.graph.store import GraphStore, GraphView
from fraudsim.types import DetectorType, RingCandidate, RingType


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

    Each detector receives a GraphView (the region to evaluate) and returns
    RingCandidate objects. Candidate ids are only local labels; stable ring
    identity across time is assigned by the RingTracker.
    """

    incremental: bool = True

    def __init__(self, config: DetectorConfig, store: GraphStore | None = None) -> None:
        self._config = config
        self.store = store

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
    def detect(self, view: GraphView) -> list[RingCandidate]:
        """Evaluate the region in `view` and return candidate rings."""

    def _candidate(
        self,
        ring_type: RingType,
        members: set[str],
        view: GraphView,
        score: float,
        evidence: dict[str, Any],
        shared_attributes: dict[str, Any] | None = None,
    ) -> RingCandidate:
        key = hashlib.sha1(",".join(sorted(members)).encode()).hexdigest()[:10]
        return RingCandidate(
            ring_id=f"{self.detector_type.value}:{key}",
            detector=self.detector_type,
            ring_type=ring_type,
            members=set(members),
            score=round(float(score), 6),
            confidence=round(float(score), 6),
            evidence=evidence,
            shared_attributes=shared_attributes or {},
            first_seen=view.now,
            last_seen=view.now,
        )
