"""
Experiment configurations and parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExperimentConfig:
    """Configuration for a headless experiment run."""

    scenario: str = "baseline"
    seeds: list[int] = field(default_factory=lambda: [42])
    output_dir: str = "results/"
    duration_seconds: int = 60
    speed_multiplier: float = 1.0
    hub_weight_enabled: bool = True
    detectors: list[str] = field(
        default_factory=lambda: [
            "static_cypher",
            "hubaware",
            "cycle",
            "smurfing",
            "community",
            "ensemble",
        ]
    )
    extra_params: dict[str, Any] = field(default_factory=dict)
