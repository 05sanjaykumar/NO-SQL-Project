"""
Core domain types shared across the entire fraud detection system.

Every module imports from here. No domain type lives anywhere else.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# ─── Identifiers ──────────────────────────────────────────────────────


def new_event_id() -> str:
    """Generate a globally unique event identifier."""
    return uuid.uuid4().hex


def new_run_id() -> str:
    """Generate a globally unique run identifier."""
    return uuid.uuid4().hex[:12]


def config_hash(config: dict[str, Any]) -> str:
    """Deterministic SHA-256 hash of a configuration dict."""
    raw = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ─── Enums ────────────────────────────────────────────────────────────


class NodeType(StrEnum):
    """Graph node labels."""

    USER = "User"
    DEVICE = "Device"
    CREDIT_CARD = "CreditCard"
    IP_ADDRESS = "IPAddress"
    TRANSACTION = "Transaction"


class RelType(StrEnum):
    """Graph relationship types."""

    USED_DEVICE = "USED_DEVICE"
    HAS_CARD = "HAS_CARD"
    LOGGED_IN_FROM = "LOGGED_IN_FROM"
    PERFORMED = "PERFORMED"
    SENT_TO = "SENT_TO"


class EventType(StrEnum):
    """Types of events the simulator can emit."""

    USER_JOIN = "user_join"
    USER_LEAVE = "user_leave"
    DEVICE_LINK = "device_link"
    CARD_LINK = "card_link"
    IP_LINK = "ip_link"
    ATTRIBUTE_UNLINK = "attribute_unlink"
    TRANSACTION = "transaction"
    # Ring lifecycle types below are recorded in the ground-truth ledger only.
    # They are never published on the event bus (see GROUND_TRUTH_ONLY_EVENTS).
    RING_SPAWN = "ring_spawn"
    RING_GROW = "ring_grow"
    RING_ACTIVATE = "ring_activate"
    RING_MUTATE = "ring_mutate"
    RING_DISSOLVE = "ring_dissolve"


# Event types that carry ground truth. The simulator must never emit these on the
# observable stream; detectors only ever see the remaining (observable) types.
GROUND_TRUTH_ONLY_EVENTS = frozenset(
    {
        EventType.RING_SPAWN,
        EventType.RING_GROW,
        EventType.RING_ACTIVATE,
        EventType.RING_MUTATE,
        EventType.RING_DISSOLVE,
    }
)


class RingType(StrEnum):
    """Types of fraud rings the system can detect."""

    SHARED_DEVICE = "shared_device"
    SHARED_CARD = "shared_card"
    SHARED_IP = "shared_ip"
    MONEY_CYCLE = "money_cycle"
    SMURFING_FAN_IN = "smurfing_fan_in"
    SMURFING_FAN_OUT = "smurfing_fan_out"
    LAYERED_CHAIN = "layered_chain"
    MIXED_ATTRIBUTE = "mixed_attribute"


class RingLifecycle(StrEnum):
    """Lifecycle phases of a fraud ring in the simulator."""

    SPAWNED = "spawned"
    GROWING = "growing"
    ACTIVE = "active"
    MUTATING = "mutating"
    DISSOLVED = "dissolved"


class RingStatus(StrEnum):
    """Detection status of a ring candidate."""

    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    DISSOLVED = "dissolved"


class EvasionMode(StrEnum):
    """Evasion strategies used by simulated attackers."""

    NONE = "none"
    HUB_HIDING = "hub_hiding"
    LOW_AND_SLOW = "low_and_slow"
    IDENTITY_ROTATION = "identity_rotation"
    DECOY_EDGES = "decoy_edges"
    ADAPTIVE = "adaptive"


class DetectorType(StrEnum):
    """Identifiers for each detector implementation."""

    STATIC_CYPHER = "static_cypher"
    HUB_AWARE = "hub_aware"
    TEMPORAL_CYCLE = "temporal_cycle"
    SMURFING = "smurfing"
    COMMUNITY = "community"
    ENSEMBLE = "ensemble"


# ─── Events ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Event:
    """
    A single event in the simulation stream.

    Every event has a unique event_id and a timestamp. The payload
    carries the event-type-specific data as a plain dict.
    """

    event_id: str
    event_type: EventType
    ts: datetime
    payload: dict[str, Any]
    run_id: str = ""
    seq: int = 0  # monotonic sequence number within a run

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "ts": self.ts.isoformat(),
            "payload": self.payload,
            "run_id": self.run_id,
            "seq": self.seq,
        }

    @classmethod
    def now(
        cls,
        event_type: EventType,
        payload: dict[str, Any],
        run_id: str = "",
        seq: int = 0,
    ) -> Event:
        return cls(
            event_id=new_event_id(),
            event_type=event_type,
            ts=datetime.now(UTC),
            payload=payload,
            run_id=run_id,
            seq=seq,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        ts_val = data["ts"]
        ts = datetime.fromisoformat(ts_val) if isinstance(ts_val, str) else ts_val
        return cls(
            event_id=data["event_id"],
            event_type=EventType(data["event_type"]),
            ts=ts,
            payload=data.get("payload", {}),
            run_id=data.get("run_id", ""),
            seq=data.get("seq", 0),
        )


# ─── Ground Truth ─────────────────────────────────────────────────────


@dataclass(slots=True)
class GroundTruthRing:
    """
    A single fraud ring as tracked by the ground-truth ledger.

    This is NEVER visible to detectors. It records the full lifecycle
    of a ring from spawn to dissolution, including member changes.
    """

    ring_id: str
    ring_type: RingType
    evasion_mode: EvasionMode = EvasionMode.NONE
    lifecycle: RingLifecycle = RingLifecycle.SPAWNED
    members: set[str] = field(default_factory=set)
    shared_attributes: dict[str, str] = field(default_factory=dict)
    spawn_ts: datetime | None = None
    activate_ts: datetime | None = None
    dissolve_ts: datetime | None = None
    member_history: list[tuple[datetime, str, str]] = field(default_factory=list)
    # (timestamp, action="join"|"leave", user_id)
    # Simulator-side detail: anchors, cycle order, the concrete strategy an adaptive
    # attacker picked, mutation count, flags observed by the attacker.
    details: dict[str, Any] = field(default_factory=dict)

    def add_member(self, user_id: str, ts: datetime) -> None:
        self.members.add(user_id)
        self.member_history.append((ts, "join", user_id))

    def remove_member(self, user_id: str, ts: datetime) -> None:
        self.members.discard(user_id)
        self.member_history.append((ts, "leave", user_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ring_id": self.ring_id,
            "ring_type": self.ring_type.value,
            "evasion_mode": self.evasion_mode.value,
            "lifecycle": self.lifecycle.value,
            "members": sorted(self.members),
            "shared_attributes": self.shared_attributes,
            "spawn_ts": self.spawn_ts.isoformat() if self.spawn_ts else None,
            "activate_ts": self.activate_ts.isoformat() if self.activate_ts else None,
            "dissolve_ts": self.dissolve_ts.isoformat() if self.dissolve_ts else None,
            "member_history": [
                {"ts": ts.isoformat(), "action": action, "user_id": uid}
                for ts, action, uid in self.member_history
            ],
            "details": self.details,
        }


# ─── Detection Results ────────────────────────────────────────────────


@dataclass(slots=True)
class RingCandidate:
    """
    A candidate fraud ring produced by a detector.

    This is the universal output type for all detectors.
    """

    ring_id: str
    detector: DetectorType
    ring_type: RingType
    members: set[str]
    status: RingStatus = RingStatus.CANDIDATE
    score: float = 0.0
    confidence: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)
    shared_attributes: dict[str, Any] = field(default_factory=dict)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    confirmed_count: int = 0  # consecutive detection count for hysteresis

    def to_dict(self) -> dict[str, Any]:
        return {
            "ring_id": self.ring_id,
            "detector": self.detector.value,
            "ring_type": self.ring_type.value,
            "members": sorted(self.members),
            "status": self.status.value,
            "score": self.score,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "shared_attributes": self.shared_attributes,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }


@dataclass(frozen=True, slots=True)
class Explanation:
    """
    Explanation for a ring flag.

    Contains everything an analyst needs to understand and audit the detection.
    """

    ring_id: str
    score: float
    feature_contributions: dict[str, float]
    reason_text: str
    evidence_subgraph: dict[str, Any]
    counterfactual: str
    confidence: float
    calibration_bucket: str  # e.g. "0.8-0.9"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ring_id": self.ring_id,
            "score": self.score,
            "feature_contributions": self.feature_contributions,
            "reason_text": self.reason_text,
            "evidence_subgraph": self.evidence_subgraph,
            "counterfactual": self.counterfactual,
            "confidence": self.confidence,
            "calibration_bucket": self.calibration_bucket,
        }


# ─── Metrics ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class MatchResult:
    """Result of matching a detected ring against ground truth."""

    detected_ring_id: str
    true_ring_id: str | None
    jaccard: float
    is_true_positive: bool
    members_detected: set[str] = field(default_factory=set)
    members_true: set[str] = field(default_factory=set)


@dataclass(slots=True)
class LatencyRecord:
    """A single latency measurement."""

    component: str  # e.g. "ingest", "detect", "end_to_end"
    duration_ms: float
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))
    batch_size: int = 1
    run_id: str = ""
    event_id: str = ""


# ─── Configuration ────────────────────────────────────────────────────


@dataclass(slots=True)
class RunConfig:
    """
    Complete configuration for a single run.

    Contains everything needed to reproduce the run: the scenario,
    RNG seed, detector settings, and a config hash for traceability.
    """

    run_id: str = field(default_factory=new_run_id)
    seed: int = 42
    scenario: str = "baseline"
    scenario_config: dict[str, Any] = field(default_factory=dict)
    detectors_enabled: list[DetectorType] = field(default_factory=lambda: list(DetectorType))
    detector_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    batch_size: int = 10
    event_rate: float = 100.0  # events per second
    duration_seconds: float = 60.0
    k_hop: int = 2  # dirty set expansion radius
    jaccard_threshold: float = 0.5
    hub_weight_method: str = "inverse_log_degree"  # or "idf", "adamic_adar"
    time_decay_half_life: float = 3600.0  # seconds
    hysteresis_confirm: int = 3  # consecutive detections to confirm
    hysteresis_dissolve: int = 5  # consecutive misses to dissolve
    ttl_seconds: float = 86400.0  # 24 hours
    scoring_model: str = "logistic_regression"  # or "gradient_boosting"

    @property
    def hash(self) -> str:
        return config_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "seed": self.seed,
            "scenario": self.scenario,
            "scenario_config": self.scenario_config,
            "detectors_enabled": [d.value for d in self.detectors_enabled],
            "detector_configs": self.detector_configs,
            "batch_size": self.batch_size,
            "event_rate": self.event_rate,
            "duration_seconds": self.duration_seconds,
            "k_hop": self.k_hop,
            "jaccard_threshold": self.jaccard_threshold,
            "hub_weight_method": self.hub_weight_method,
            "time_decay_half_life": self.time_decay_half_life,
            "hysteresis_confirm": self.hysteresis_confirm,
            "hysteresis_dissolve": self.hysteresis_dissolve,
            "ttl_seconds": self.ttl_seconds,
            "scoring_model": self.scoring_model,
        }
