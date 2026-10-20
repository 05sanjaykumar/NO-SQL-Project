"""
Scoring stage: plugs into DetectionEngine after ring tracking.

For every ring that changed in a batch it computes features, the calibrated
risk score and the full explanation, and raises:

- `early_warning` the first time a still-forming ring (status CANDIDATE)
  scores at or above its threshold ("predicted to become a ring"),
- `alert` the first time a CONFIRMED ring scores at or above its threshold.

Thresholds live in an `AlertPolicy`, one per ring type. Analyst feedback
(confirm / dismiss) moves them, and every adaptation is logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fraudsim.detectors.ensemble import RingUpdate, TrackedRing
from fraudsim.scoring.explanations import explain
from fraudsim.scoring.features import RingContext, RingFeatures, extract_features
from fraudsim.scoring.scorer import RingScorer
from fraudsim.types import RingStatus


@dataclass(slots=True)
class AlertPolicy:
    """Per-ring-type alert thresholds that adapt to analyst feedback."""

    default_threshold: float = 0.5
    learning_rate: float = 0.5
    margin: float = 0.02
    min_threshold: float = 0.05
    max_threshold: float = 0.99
    thresholds: dict[str, float] = field(default_factory=dict)
    log: list[dict[str, Any]] = field(default_factory=list)

    def threshold(self, ring_type: str) -> float:
        return self.thresholds.get(ring_type, self.default_threshold)

    def feedback(
        self, ring_id: str, ring_type: str, score: float, action: str, ts: datetime | None = None
    ) -> dict[str, Any]:
        """
        confirm: a true ring. If it scored close to (or under) the threshold,
        lower the threshold toward score - margin so similar rings alert.
        dismiss: a false alert. Raise the threshold toward score + margin so
        similar rings stop alerting.
        """
        if action not in ("confirm", "dismiss"):
            raise ValueError("action must be 'confirm' or 'dismiss'")
        old = self.threshold(ring_type)
        if action == "dismiss" and score >= old - self.margin:
            target = score + self.margin
        elif action == "confirm" and score <= old + self.margin:
            target = score - self.margin
        else:
            target = old
        new = old + self.learning_rate * (target - old)
        new = min(self.max_threshold, max(self.min_threshold, new))
        self.thresholds[ring_type] = new
        entry = {
            "ts": (ts or datetime.now(UTC)).isoformat(),
            "ring_id": ring_id,
            "ring_type": ring_type,
            "action": action,
            "score": round(score, 6),
            "threshold_before": round(old, 6),
            "threshold_after": round(new, 6),
        }
        self.log.append(entry)
        return entry


class ScoringStage:
    """Features + calibrated risk + explanation for rings that changed."""

    def __init__(
        self,
        scorer: RingScorer | None = None,
        policy: AlertPolicy | None = None,
        record: bool = False,
    ) -> None:
        self.scorer = scorer
        self.policy = policy or AlertPolicy()
        self.record = record
        self.rows: list[dict[str, Any]] = []  # feature rows (for training) when record=True

    def score_updates(
        self, engine: Any, updates: list[RingUpdate], now_t: float, since_t: float
    ) -> list[RingUpdate]:
        tracker = engine.tracker
        changed = sorted({u.ring_id for u in updates})
        # Users that belong to more than one live ring carry an extra risk signal.
        ring_count: dict[str, int] = {}
        for r in tracker.live():
            for u in r.members:
                ring_count[u] = ring_count.get(u, 0) + 1
        in_other_ring = {u for u, c in ring_count.items() if c >= 2}
        now = datetime.fromtimestamp(now_t, tz=UTC)
        extra: list[RingUpdate] = []
        for rid in changed:
            ring: TrackedRing = tracker.rings[rid]
            if ring.status == RingStatus.DISSOLVED:
                continue
            feats, view, joined = self.featurize(engine, ring, in_other_ring, now_t, since_t)
            ring.features = feats.to_dict()
            if self.record:
                self.rows.append(
                    {
                        "ring_id": rid,
                        "t": now_t,
                        "status": ring.status.value,
                        "members": sorted(ring.members),
                        "features": feats.to_vector(),
                    }
                )
            threshold = self.policy.threshold(ring.ring_type.value)
            risk, exp = explain(
                rid, ring.members, feats, view, joined, ring.evidence, self.scorer, threshold
            )
            ring.risk = risk
            ring.explanation = exp.to_dict() | {"threshold": threshold}
            if risk is None or risk < threshold:
                continue
            kind = None
            if ring.status == RingStatus.CANDIDATE and ring.early_warned_at is None:
                kind = "early_warning"
                ring.early_warned_at = now
            elif ring.status == RingStatus.CONFIRMED and ring.alerted_at is None:
                kind = "alert"
                ring.alerted_at = now
            if kind is None:
                continue
            ring.history.append({"ts": now.isoformat(), "change": kind, "risk": round(risk, 6)})
            extra.append(
                RingUpdate(
                    ts=now,
                    ring_id=rid,
                    change=kind,
                    status=ring.status,
                    members=tuple(sorted(ring.members)),
                    detectors=tuple(sorted(ring.sources)),
                    score=round(risk, 6),
                    details={"threshold": threshold, "reason": exp.reason_text},
                )
            )
        return extra

    def featurize(
        self,
        engine: Any,
        ring: TrackedRing,
        others: set[str],
        now_t: float,
        since_t: float,
    ) -> tuple[RingFeatures, Any, dict[str, float]]:
        view = engine.store.view(ring.members, since_t, now_t)
        joined = engine.store.joined(ring.members)
        cycle_ev = (ring.evidence.get("by_detector") or {}).get("temporal_cycle")
        ctx = RingContext(
            ring_id=ring.ring_id,
            members=ring.members,
            detectors=set(ring.sources),
            first_seen_t=ring.first_seen.timestamp(),
            member_history=ring.size_history,
            other_flagged_users=others,
            cycle_evidence=cycle_ev,
        )
        return extract_features(ctx, view, joined), view, joined
