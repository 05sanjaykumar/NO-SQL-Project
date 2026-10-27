"""
Strategists: the thing that decides which attack to try next.

Three are provided, all behind one interface so they can be compared on equal
terms - same budget, same seeds, same scoring:

random        independent samples. The control arm: any adaptive strategist
              has to beat this to have earned its cost.
evolutionary  keeps the best plans so far and perturbs them. Adaptive, free,
              deterministic under a seed.
llm           asks a language model to read the history and propose the next
              plan. Needs ANTHROPIC_API_KEY and the `anthropic` package; it
              reports clearly if either is missing rather than failing late.
"""

from __future__ import annotations

import json
import os
import random
from typing import Protocol

from fraudsim.adversary.space import (
    ACTION_INTERVAL_S,
    ACTIVE_DURATION_S,
    GROW_INTERVAL_S,
    INTENSITY,
    MUTATE_PROB,
    SIZE,
    AttackPlan,
    mutate,
    sample,
)
from fraudsim.adversary.trial import TrialResult
from fraudsim.types import EvasionMode, RingType


class Strategist(Protocol):
    """Proposes the next attack given everything tried so far."""

    name: str

    def propose(self, history: list[TrialResult]) -> AttackPlan: ...


class RandomStrategist:
    """Independent samples. The control arm."""

    name = "random"

    def __init__(self, rng: random.Random, hubs: tuple[str, ...] = ()) -> None:
        self._rng = rng
        self._hubs = hubs

    def propose(self, history: list[TrialResult]) -> AttackPlan:
        return sample(self._rng, self._hubs)


class EvolutionaryStrategist:
    """Keep the best plans, perturb them, explore occasionally."""

    name = "evolutionary"

    def __init__(
        self,
        rng: random.Random,
        hubs: tuple[str, ...] = (),
        elite: int = 5,
        explore_prob: float = 0.3,
    ) -> None:
        self._rng = rng
        self._hubs = hubs
        self._elite = elite
        self._explore_prob = explore_prob

    def propose(self, history: list[TrialResult]) -> AttackPlan:
        if not history or self._rng.random() < self._explore_prob:
            return sample(self._rng, self._hubs)
        best = sorted(history, key=lambda r: r.fitness, reverse=True)[: self._elite]
        return mutate(self._rng.choice(best).plan, self._rng, self._hubs)


_SYSTEM = """You are red-teaming a fraud-ring detector inside a simulator. No real
system or real people are involved; the goal is to find weaknesses so they can be
fixed.

You choose the behaviour of one simulated fraud ring. You will be shown the plans
already tried and whether the detector caught each one. Propose the next plan most
likely to go undetected, reasoning about why previous attempts failed.

Reply with JSON only, no prose:
{"ring_type": str, "evasion": str, "size": int, "grow_interval_s": float,
 "action_interval_s": float, "active_duration_s": float, "mutate_prob": float,
 "intensity": float, "shared_attribute": str or null}

A plan where active_duration_s / action_interval_s < 6 is rejected as invalid:
a ring that never acts is not an attack."""


class LLMStrategist:
    """Asks a model to read the history and propose the next plan."""

    name = "llm"

    def __init__(
        self,
        rng: random.Random,
        hubs: tuple[str, ...] = (),
        model: str = "claude-sonnet-5-5",
        fallback: Strategist | None = None,
    ) -> None:
        self._rng = rng
        self._hubs = hubs
        self._model = model
        self._fallback = fallback or EvolutionaryStrategist(rng, hubs)
        self._client = None
        self.unavailable: str | None = None

        if not os.environ.get("ANTHROPIC_API_KEY"):
            self.unavailable = "ANTHROPIC_API_KEY is not set"
            return
        try:
            import anthropic
        except ImportError:
            self.unavailable = "the 'anthropic' package is not installed"
            return
        self._client = anthropic.Anthropic()

    def propose(self, history: list[TrialResult]) -> AttackPlan:
        if self._client is None:
            return self._fallback.propose(history)
        try:
            return self._propose_via_model(history)
        except Exception as exc:  # noqa: BLE001 - never let the search die on an API error
            self.unavailable = f"{type(exc).__name__}: {exc}"
            return self._fallback.propose(history)

    def _propose_via_model(self, history: list[TrialResult]) -> AttackPlan:
        recent = [
            {"plan": r.plan.as_dict(), "evaded": r.evaded, "ring_recall": round(r.ring_recall, 3)}
            for r in history[-25:]
        ]
        prompt = json.dumps(
            {
                "ring_types": [t.value for t in RingType],
                "evasions": [e.value for e in EvasionMode],
                "hubs_available": list(self._hubs),
                "bounds": {
                    "size": list(SIZE),
                    "grow_interval_s": list(GROW_INTERVAL_S),
                    "action_interval_s": list(ACTION_INTERVAL_S),
                    "active_duration_s": list(ACTIVE_DURATION_S),
                    "mutate_prob": list(MUTATE_PROB),
                    "intensity": list(INTENSITY),
                },
                "tried": recent,
            }
        )
        reply = self._client.messages.create(
            model=self._model,
            max_tokens=600,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in reply.content if getattr(b, "type", "") == "text")
        start, end = text.find("{"), text.rfind("}")
        raw = json.loads(text[start : end + 1])

        plan = AttackPlan(
            ring_type=RingType(raw["ring_type"]),
            evasion=EvasionMode(raw["evasion"]),
            size=int(raw["size"]),
            grow_interval_s=float(raw["grow_interval_s"]),
            action_interval_s=float(raw["action_interval_s"]),
            active_duration_s=float(raw["active_duration_s"]),
            mutate_prob=float(raw["mutate_prob"]),
            intensity=float(raw["intensity"]),
            shared_attribute=raw.get("shared_attribute") or None,
        )
        return plan if plan.is_valid() else self._fallback.propose(history)


def make_strategist(name: str, rng: random.Random, hubs: tuple[str, ...]) -> Strategist:
    if name == "random":
        return RandomStrategist(rng, hubs)
    if name == "evolutionary":
        return EvolutionaryStrategist(rng, hubs)
    if name == "llm":
        return LLMStrategist(rng, hubs)
    raise ValueError(f"unknown strategist: {name}")
