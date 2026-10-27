"""
Run one attack plan against the detector and report whether it got through.

The scenario is built in memory and handed to `run_detection` as a dict, so
nothing is written into configs/ and none of the detection code changes. The
world (users, hubs, noise) is taken from an existing scenario so the background
is realistic; only the ring list is replaced, with exactly one ring - the one
under test. With a single planted ring, `rings_detected` answers the question
directly.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fraudsim.adversary.space import AttackPlan
from fraudsim.detectors.engine import EngineConfig
from fraudsim.detectors.runner import run_detection
from fraudsim.simulator.scenario_loader import load_scenario

RING_ID = "adv_target"


@dataclass(frozen=True, slots=True)
class TrialResult:
    """What happened when one plan was run."""

    plan: AttackPlan
    evaded: bool
    ring_recall: float
    ring_precision: float
    false_positive_rings: int
    events: int
    seed: int

    @property
    def fitness(self) -> float:
        """Higher is a better attack. Evasion dominates; recall breaks ties."""
        return (1.0 if self.evaded else 0.0) + (1.0 - self.ring_recall)

    def as_dict(self) -> dict[str, Any]:
        return {
            "evaded": self.evaded,
            "ring_recall": round(self.ring_recall, 4),
            "ring_precision": round(self.ring_precision, 4),
            "false_positive_rings": self.false_positive_rings,
            "events": self.events,
            "seed": self.seed,
            "fitness": round(self.fitness, 4),
            "plan": self.plan.as_dict(),
        }


def hubs_in(scenario: str) -> tuple[str, ...]:
    """Names of the benign hubs in a scenario, which a ring can hide behind."""
    raw = load_scenario(scenario)
    dist = (raw.get("simulation") or {}).get("hub_distribution") or {}
    return tuple(dist.keys())


def build_scenario(base: str, plan: AttackPlan, duration_s: float) -> dict[str, Any]:
    """The base world, with its rings replaced by the single ring under test."""
    raw = copy.deepcopy(load_scenario(base))
    raw["name"] = f"{raw.get('name', base)}__adversary"
    raw["rings"] = [plan.to_ring_spec(RING_ID)]
    # No extra random rings: the one planted ring must be the only ground truth.
    raw["random_rings"] = {"rate_per_min": 0.0}
    sim = raw.setdefault("simulation", {})
    sim["duration_seconds"] = duration_s
    return raw


def run_trial(
    plan: AttackPlan,
    *,
    base_scenario: str = "adversarial",
    seed: int = 42,
    duration_s: float = 120.0,
    out_dir: Path | None = None,
    backend: str = "memory",
    model_path: Path | None = None,
) -> TrialResult:
    """Execute one plan and score it."""
    scenario = build_scenario(base_scenario, plan, duration_s)

    scoring = None
    if model_path and model_path.exists():
        from fraudsim.scoring.scorer import RingScorer
        from fraudsim.scoring.stage import ScoringStage

        scoring = ScoringStage(scorer=RingScorer.load(model_path))

    result = run_detection(
        scenario,
        seed,
        out_dir,
        backend=backend,
        engine_config=EngineConfig(batch_size=50, k_hop=2, incremental=True),
        duration_s=duration_s,
        scoring=scoring,
    )
    m = result.manifest.get("metrics") or {}
    ring = m.get("ring") or {}
    detected = int(m.get("rings_detected") or 0)

    return TrialResult(
        plan=plan,
        evaded=detected == 0,
        ring_recall=float(ring.get("recall") or 0.0),
        ring_precision=float(ring.get("precision") or 0.0),
        false_positive_rings=int(m.get("false_positive_rings") or 0),
        events=int(result.manifest.get("events") or 0),
        seed=seed,
    )
