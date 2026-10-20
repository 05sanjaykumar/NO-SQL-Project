"""
API request and response schemas (Pydantic models).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    neo4j_connected: bool = False


class RunStatusResponse(BaseModel):
    run_id: str
    status: str  # idle, running, paused, stopped
    scenario: str
    seed: int
    events_emitted: int = 0
    rings_detected: int = 0
    speed_multiplier: float = 1.0  # 0 = as fast as possible
    sim_time_s: float = 0.0
    rate: float = 0.0  # benign events per simulated second


class StartRunRequest(BaseModel):
    scenario: str = "baseline"
    seed: int = 42
    speed_multiplier: float = Field(default=1.0, ge=0.0)
    hub_weight_enabled: bool = True
    hub_degree_cap: float | None = None


class SpeedRequest(BaseModel):
    speed_multiplier: float = Field(ge=0.0, description="0 = as fast as possible")


class RateRequest(BaseModel):
    events_per_second: float = Field(ge=0.0)


class InjectRingRequest(BaseModel):
    ring_type: str = "shared_device"
    evasion: str = "none"
    size: int = Field(default=4, ge=2, le=12)


class DetectorToggleRequest(BaseModel):
    enabled: bool


class ConfigRequest(BaseModel):
    alert_threshold: float | None = None
    edge_threshold: float | None = None
    hub_weight_method: str | None = None


class AnalystFeedbackRequest(BaseModel):
    action: str  # "confirm" or "dismiss"
    analyst_id: str = "analyst_1"
    note: str = ""


class RingDetailResponse(BaseModel):
    ring_id: str
    status: str
    ring_type: str
    score: float
    members: list[str]
    features: dict[str, Any] = Field(default_factory=dict)
    explanation: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class MetricsResponse(BaseModel):
    """Live metrics of the current run. None = not measurable yet (never a placeholder)."""

    run_id: str | None = None
    sim_time_s: float | None = None
    precision: float | None = None  # ring level, against the simulator's ground truth
    recall: float | None = None
    f1: float | None = None
    rings_detected: int = 0
    rings_missed: int = 0
    false_positive_rings: int = 0
    user_precision: float | None = None
    user_recall: float | None = None
    time_to_detect_p50_s: float | None = None
    latency_p50_ms: float | None = None  # detection per batch
    latency_p95_ms: float | None = None
    latency_p99_ms: float | None = None
    throughput_events_per_sec: float | None = None  # events / engine processing time
    total_events: int = 0
    active_rings: int = 0
    summary: dict[str, Any] = Field(default_factory=dict)
