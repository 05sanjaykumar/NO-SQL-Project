"""
Ablation study definitions.

Evaluates the contribution of each system component:
- no_hub_weight: Disable inverse-degree continuous weighting (fall back to binary)
- no_time_decay: Disable temporal exponential decay
- no_incremental: Full-graph batch recomputation instead of dirty-set neighborhood
- no_scoring: Pure heuristic rules without calibrated risk model
- no_hysteresis: Direct status transitions without confirmation delay
- leave_one_detector_out: Remove individual detectors one at a time
"""

from __future__ import annotations

from typing import Any

ABLATION_VARIANTS: dict[str, dict[str, Any]] = {
    "full_system": {
        "description": "Full proposed system with all components enabled",
        "hub_weighting": True,
        "time_decay": True,
        "incremental": True,
        "scoring_model": True,
        "hysteresis": True,
    },
    "no_hub_weight": {
        "description": "Ablation: unweighted edges (binary shared attribute)",
        "hub_weighting": False,
        "time_decay": True,
        "incremental": True,
        "scoring_model": True,
        "hysteresis": True,
    },
    "no_time_decay": {
        "description": "Ablation: time decay disabled (all historical edges equal)",
        "hub_weighting": True,
        "time_decay": False,
        "incremental": True,
        "scoring_model": True,
        "hysteresis": True,
    },
    "no_incremental": {
        "description": "Ablation: full graph periodic recomputation",
        "hub_weighting": True,
        "time_decay": True,
        "incremental": False,
        "scoring_model": True,
        "hysteresis": True,
    },
    "no_scoring": {
        "description": "Ablation: rule-based candidates only, no calibrated score",
        "hub_weighting": True,
        "time_decay": True,
        "incremental": True,
        "scoring_model": False,
        "hysteresis": True,
    },
    "no_hysteresis": {
        "description": "Ablation: immediate confirmation without temporal hysteresis",
        "hub_weighting": True,
        "time_decay": True,
        "incremental": True,
        "scoring_model": True,
        "hysteresis": False,
    },
}
