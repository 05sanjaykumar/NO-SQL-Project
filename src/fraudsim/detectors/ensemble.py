"""
Ensemble: merge candidates from all detectors into unified rings, and track
ring identity and status over time.

- `merge_candidates` unions candidates (from any detectors) whose member sets
  overlap (Jaccard >= merge_jaccard, or the smaller set is mostly inside the
  larger one) into one unified candidate that keeps every detector's evidence.
- `RingTracker` gives rings stable ids across micro-batches by Jaccard
  matching against the rings it already tracks, and handles grow, shrink,
  merge and split. Status moves CANDIDATE -> CONFIRMED after `confirm_hits`
  consecutive detections, and -> DISSOLVED after `dissolve_misses`
  consecutive evaluations that did not find it (hysteresis, so rings do not
  flap). A miss only counts when one of the detectors that found the ring
  actually evaluated a region containing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from fraudsim.detectors.base import BaseDetector, DetectorConfig
from fraudsim.graph.store import GraphStore, GraphView
from fraudsim.metrics.matching import jaccard
from fraudsim.types import DetectorType, RingCandidate, RingStatus, RingType

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class EnsembleConfig(DetectorConfig):
    """Configuration for merging and tracking."""

    detector_type: DetectorType = DetectorType.ENSEMBLE
    name: str = "Ensemble"
    merge_jaccard: float = 0.5  # cross-detector merge within one evaluation
    merge_containment: float = 0.8  # or |A & B| / min(|A|, |B|) >= this
    match_jaccard: float = 0.5  # identity matching across evaluations
    split_jaccard: float = 0.2  # a leftover piece overlapping a ring this much is a split
    hysteresis_confirm: int = 2
    hysteresis_dissolve: int = 3
    extra: dict[str, Any] = field(default_factory=dict)


def _overlaps(a: set[str], b: set[str], cfg: EnsembleConfig) -> bool:
    inter = len(a & b)
    if not inter:
        return False
    return (
        jaccard(a, b) >= cfg.merge_jaccard or inter / min(len(a), len(b)) >= cfg.merge_containment
    )


def merge_candidates(cands: list[RingCandidate], cfg: EnsembleConfig) -> list[RingCandidate]:
    """Union overlapping candidates into unified rings (deterministic order)."""
    cands = sorted(cands, key=lambda c: (c.detector.value, sorted(c.members), c.ring_id))
    parent = list(range(len(cands)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            if _overlaps(cands[i].members, cands[j].members, cfg):
                parent[find(j)] = find(i)

    groups: dict[int, list[RingCandidate]] = {}
    for i, c in enumerate(cands):
        groups.setdefault(find(i), []).append(c)

    unified = []
    for group in groups.values():
        members: set[str] = set().union(*(c.members for c in group))
        best = max(group, key=lambda c: (c.score, c.detector.value))
        types = sorted({c.ring_type.value for c in group})
        unified.append(
            RingCandidate(
                ring_id="",
                detector=DetectorType.ENSEMBLE,
                ring_type=best.ring_type if len(types) == 1 else _combined_type(group),
                members=members,
                score=max(c.score for c in group),
                confidence=max(c.confidence for c in group),
                evidence={
                    "detectors": sorted({c.detector.value for c in group}),
                    "ring_types": types,
                    "by_detector": {
                        c.detector.value: {
                            "members": sorted(c.members),
                            "score": c.score,
                            "ring_type": c.ring_type.value,
                            **c.evidence,
                        }
                        for c in group
                    },
                },
                shared_attributes={k: v for c in group for k, v in c.shared_attributes.items()},
                first_seen=min(c.first_seen for c in group if c.first_seen),
                last_seen=max(c.last_seen for c in group if c.last_seen),
            )
        )
    return sorted(unified, key=lambda c: sorted(c.members))


def _combined_type(group: list[RingCandidate]) -> RingType:
    # Prefer the most specific behavioural pattern when detectors disagree.
    order = [
        RingType.MONEY_CYCLE,
        RingType.SMURFING_FAN_IN,
        RingType.SMURFING_FAN_OUT,
        RingType.LAYERED_CHAIN,
        RingType.SHARED_DEVICE,
        RingType.SHARED_CARD,
        RingType.SHARED_IP,
        RingType.MIXED_ATTRIBUTE,
    ]
    present = {c.ring_type for c in group}
    return next(t for t in order if t in present)


@dataclass(slots=True)
class TrackedRing:
    ring_id: str
    members: set[str]
    status: RingStatus
    ring_type: RingType
    score: float
    sources: set[str]  # detector names that have found this ring
    first_seen: datetime
    last_seen: datetime
    hits: int = 1
    misses: int = 0
    confirmed_at: datetime | None = None
    dissolved_at: datetime | None = None
    parent: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_candidate(self) -> RingCandidate:
        return RingCandidate(
            ring_id=self.ring_id,
            detector=DetectorType.ENSEMBLE,
            ring_type=self.ring_type,
            members=set(self.members),
            status=self.status,
            score=self.score,
            confidence=self.score,
            evidence=self.evidence,
            first_seen=self.first_seen,
            last_seen=self.last_seen,
            confirmed_count=self.hits,
        )


@dataclass(frozen=True, slots=True)
class RingUpdate:
    """One change to a tracked ring, emitted by the tracker (logged and audited later)."""

    ts: datetime
    ring_id: str
    change: str  # created | confirmed | grew | shrank | merged_into | split_from | dissolved
    status: RingStatus
    members: tuple[str, ...]
    detectors: tuple[str, ...]
    score: float
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "ring_id": self.ring_id,
            "change": self.change,
            "status": self.status.value,
            "members": list(self.members),
            "detectors": list(self.detectors),
            "score": self.score,
            "details": self.details,
        }


class RingTracker:
    """Stable ring identities and hysteresis over successive evaluations."""

    def __init__(self, config: EnsembleConfig | None = None) -> None:
        self.cfg = config or EnsembleConfig()
        self.rings: dict[str, TrackedRing] = {}
        self._counter = 0

    def _new_id(self) -> str:
        self._counter += 1
        return f"R{self._counter:06d}"

    def live(self) -> list[TrackedRing]:
        return [r for r in self.rings.values() if r.status != RingStatus.DISSOLVED]

    def confirmed(self) -> list[TrackedRing]:
        return [r for r in self.rings.values() if r.status == RingStatus.CONFIRMED]

    def flagged_users(self) -> set[str]:
        return set().union(*(r.members for r in self.confirmed())) if self.confirmed() else set()

    def update(
        self,
        candidates: list[RingCandidate],
        evaluated: dict[str, set[str] | None],
        now: datetime,
    ) -> list[RingUpdate]:
        """
        Fold one evaluation into the tracked state.

        `candidates` are raw detector outputs. `evaluated` maps each detector that
        ran to the users it evaluated (None = the whole graph).
        """
        cfg = self.cfg
        updates: list[RingUpdate] = []
        unified = merge_candidates(candidates, cfg)
        live = sorted(self.live(), key=lambda r: r.ring_id)
        before = {r.ring_id: set(r.members) for r in live}  # members prior to this update

        # Greedy best-Jaccard matching between unified candidates and live rings.
        pairs = sorted(
            (
                (jaccard(u.members, r.members), i, r.ring_id)
                for i, u in enumerate(unified)
                for r in live
                if u.members & r.members
            ),
            key=lambda x: (-x[0], x[1], x[2]),
        )
        cand_to_ring: dict[int, str] = {}
        ring_to_cand: dict[str, int] = {}
        for j, i, rid in pairs:
            if j < cfg.match_jaccard:
                break
            if i in cand_to_ring or rid in ring_to_cand:
                continue
            cand_to_ring[i] = rid
            ring_to_cand[rid] = i

        # Matched: grow / shrink / hit / confirm.
        for i, rid in sorted(cand_to_ring.items()):
            ring, cand = self.rings[rid], unified[i]
            self._absorb(ring, cand, now, updates)
            # Other live rings now (mostly) inside this one were merged into it.
            for other in live:
                if other.ring_id in ring_to_cand or other.status == RingStatus.DISSOLVED:
                    continue
                inside = len(other.members & cand.members) / len(other.members)
                if inside >= cfg.merge_containment:
                    self._dissolve(other, now, updates, change="merged_into", into=rid)

        # Unmatched candidates: split pieces of a live ring, merges, or new rings.
        for i, cand in enumerate(unified):
            if i in cand_to_ring:
                continue
            overlapping = [
                r for r in live if jaccard(cand.members, before[r.ring_id]) >= cfg.split_jaccard
            ]
            ring = self._create(cand, now, updates)
            # This candidate absorbs live rings it now (mostly) contains: a merge.
            for other in overlapping:
                if other.ring_id in ring_to_cand:
                    ring.parent = ring.parent or other.ring_id
                    continue
                if len(other.members & cand.members) / len(other.members) >= cfg.merge_containment:
                    self._dissolve(other, now, updates, change="merged_into", into=ring.ring_id)
                    ring.hits = max(ring.hits, other.hits + 1)
                    ring.first_seen = min(ring.first_seen, other.first_seen)
                elif ring.parent is None:
                    ring.parent = other.ring_id
            if ring.parent:
                updates.append(self._event(ring, now, "split_from", {"parent": ring.parent}))
            self._maybe_confirm(ring, now, updates)

        # Live rings not matched: a miss if one of their detectors looked at them.
        for ring in live:
            if ring.ring_id in ring_to_cand or ring.status == RingStatus.DISSOLVED:
                continue
            looked = any(
                det in evaluated and (evaluated[det] is None or ring.members & evaluated[det])  # type: ignore[operator]
                for det in ring.sources
            )
            if not looked:
                continue
            ring.misses += 1
            ring.hits = 0
            if ring.misses >= cfg.hysteresis_dissolve:
                self._dissolve(ring, now, updates)

        for upd in updates:
            logger.debug("ring_update", **upd.to_dict())
        return updates

    # ─── Helpers ──────────────────────────────────────────────────────

    def _create(self, cand: RingCandidate, now: datetime, updates: list[RingUpdate]) -> TrackedRing:
        ring = TrackedRing(
            ring_id=self._new_id(),
            members=set(cand.members),
            status=RingStatus.CANDIDATE,
            ring_type=cand.ring_type,
            score=cand.score,
            sources=set(cand.evidence.get("detectors", [])),
            first_seen=now,
            last_seen=now,
            evidence=cand.evidence,
        )
        self.rings[ring.ring_id] = ring
        updates.append(self._event(ring, now, "created"))
        return ring

    def _absorb(
        self, ring: TrackedRing, cand: RingCandidate, now: datetime, updates: list[RingUpdate]
    ) -> None:
        added, removed = cand.members - ring.members, ring.members - cand.members
        ring.members = set(cand.members)
        ring.score = cand.score
        ring.ring_type = cand.ring_type
        ring.sources |= set(cand.evidence.get("detectors", []))
        ring.evidence = cand.evidence
        ring.last_seen = now
        ring.hits += 1
        ring.misses = 0
        if added:
            updates.append(self._event(ring, now, "grew", {"added": sorted(added)}))
        if removed:
            updates.append(self._event(ring, now, "shrank", {"removed": sorted(removed)}))
        self._maybe_confirm(ring, now, updates)

    def _maybe_confirm(self, ring: TrackedRing, now: datetime, updates: list[RingUpdate]) -> None:
        if ring.status == RingStatus.CANDIDATE and ring.hits >= self.cfg.hysteresis_confirm:
            ring.status = RingStatus.CONFIRMED
            ring.confirmed_at = now
            updates.append(self._event(ring, now, "confirmed"))

    def _dissolve(
        self,
        ring: TrackedRing,
        now: datetime,
        updates: list[RingUpdate],
        change: str = "dissolved",
        into: str | None = None,
    ) -> None:
        ring.status = RingStatus.DISSOLVED
        ring.dissolved_at = now
        details = {"into": into} if into else {"misses": ring.misses}
        updates.append(self._event(ring, now, change, details))

    @staticmethod
    def _event(
        ring: TrackedRing, now: datetime, change: str, details: dict[str, Any] | None = None
    ) -> RingUpdate:
        ring.history.append({"ts": now.isoformat(), "change": change, **(details or {})})
        return RingUpdate(
            ts=now,
            ring_id=ring.ring_id,
            change=change,
            status=ring.status,
            members=tuple(sorted(ring.members)),
            detectors=tuple(sorted(ring.sources)),
            score=ring.score,
            details=details or {},
        )


class EnsembleDetector(BaseDetector):
    """
    Convenience wrapper: run sub-detectors on one view and return unified
    candidates. The DetectionEngine uses merge_candidates + RingTracker
    directly so it can schedule incremental and full-graph detectors separately.
    """

    incremental = True

    def __init__(
        self,
        sub_detectors: list[BaseDetector] | None = None,
        config: EnsembleConfig | None = None,
        store: GraphStore | None = None,
    ) -> None:
        super().__init__(config or EnsembleConfig(), store)
        self.cfg: EnsembleConfig = self._config  # type: ignore[assignment]
        self._sub_detectors = sub_detectors or []

    def detect(self, view: GraphView) -> list[RingCandidate]:
        raw = [c for d in self._sub_detectors if d.enabled for c in d.detect(view)]
        return merge_candidates(raw, self.cfg)
