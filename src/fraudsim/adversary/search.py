"""
The search loop, and the report it produces.

One round is: the strategist proposes a plan, the simulator runs it, the
detector scores it, the outcome goes back into the history. Repeat for a fixed
budget so strategists can be compared on equal terms.

Each plan is evaluated on several seeds and only counts as an evasion if it
goes undetected on all of them. A plan that slips past once out of five is
luck, not an attack.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fraudsim.adversary.space import AttackPlan
from fraudsim.adversary.strategists import Strategist, make_strategist
from fraudsim.adversary.trial import TrialResult, hubs_in, run_trial
from fraudsim.types import EvasionMode


@dataclass
class SearchReport:
    """Everything the search found."""

    strategist: str
    base_scenario: str
    rounds: int
    seeds: list[int]
    history: list[TrialResult] = field(default_factory=list)
    wall_s: float = 0.0

    @property
    def evasions(self) -> list[TrialResult]:
        return [r for r in self.history if r.evaded]

    @property
    def first_evasion_round(self) -> int | None:
        for i, r in enumerate(self.history, 1):
            if r.evaded:
                return i
        return None

    def novel_evasions(self) -> list[TrialResult]:
        """Evasions that do not rely on an evasion mode already known to work.

        low_and_slow and identity_rotation are reported in the paper as
        defeating every method tested, so finding them again confirms a known
        result. Anything else is new.
        """
        known = {EvasionMode.LOW_AND_SLOW, EvasionMode.IDENTITY_ROTATION}
        return [r for r in self.evasions if r.plan.evasion not in known]

    def summary(self) -> dict[str, Any]:
        return {
            "strategist": self.strategist,
            "base_scenario": self.base_scenario,
            "rounds": self.rounds,
            "seeds": self.seeds,
            "evasions_found": len(self.evasions),
            "evasion_rate": round(len(self.evasions) / max(self.rounds, 1), 4),
            "novel_evasions": len(self.novel_evasions()),
            "first_evasion_round": self.first_evasion_round,
            "wall_s": round(self.wall_s, 1),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "evasions": [r.as_dict() for r in self.evasions],
            "history": [r.as_dict() for r in self.history],
        }

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path


def _evaluate(
    plan: AttackPlan,
    seeds: list[int],
    base_scenario: str,
    duration_s: float,
    backend: str,
    model_path: Path | None,
) -> TrialResult:
    """Run a plan on every seed; it evades only if it evades on all of them."""
    results = [
        run_trial(
            plan,
            base_scenario=base_scenario,
            seed=s,
            duration_s=duration_s,
            out_dir=None,
            backend=backend,
            model_path=model_path,
        )
        for s in seeds
    ]
    worst = max(results, key=lambda r: r.ring_recall)  # the seed that caught it best
    return TrialResult(
        plan=plan,
        evaded=all(r.evaded for r in results),
        ring_recall=sum(r.ring_recall for r in results) / len(results),
        ring_precision=sum(r.ring_precision for r in results) / len(results),
        false_positive_rings=worst.false_positive_rings,
        events=worst.events,
        seed=worst.seed,
    )


def search(
    *,
    strategist: str = "evolutionary",
    rounds: int = 40,
    seeds: tuple[int, ...] = (42, 43, 44),
    base_scenario: str = "adversarial",
    duration_s: float = 120.0,
    backend: str = "memory",
    model_path: Path | None = None,
    rng_seed: int = 7,
    on_round: Callable[[int, TrialResult], None] | None = None,
) -> SearchReport:
    """Run the adversary for `rounds` proposals and return what it found."""
    rng = random.Random(rng_seed)
    hubs = hubs_in(base_scenario)
    engine: Strategist = make_strategist(strategist, rng, hubs)

    report = SearchReport(
        strategist=strategist,
        base_scenario=base_scenario,
        rounds=rounds,
        seeds=list(seeds),
    )
    started = time.perf_counter()
    for i in range(1, rounds + 1):
        plan = engine.propose(report.history)
        result = _evaluate(plan, list(seeds), base_scenario, duration_s, backend, model_path)
        report.history.append(result)
        if on_round:
            on_round(i, result)
    report.wall_s = time.perf_counter() - started
    return report
