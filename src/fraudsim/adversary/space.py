"""
The space of attacks the adversary can try.

Her five evasion strategies are five hand-chosen points in a much larger space:
a ring also has a type, a size, how fast it recruits, how often it acts, how
long it stays alive, and how much it mutates. An attack plan is one point in
that space, and the search looks for points the detector misses.

Bounds are deliberately conservative. A ring that never acts is trivially
undetected but is not an attack, so every plan must describe a ring that grows
to full size and transacts repeatedly - see `MIN_ACTIONS`.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, replace
from typing import Any

from fraudsim.types import EvasionMode, RingType

# A plan is only counted if the ring acts at least this many times. Without it
# the search "wins" by proposing rings so slow or short-lived that they never
# do anything, which evades detection but is not evasion.
MIN_ACTIONS = 6

# A ring must finish recruiting within this fraction of its lifetime, or it
# never really forms and "evading" means nothing.
MAX_FORMATION_FRACTION = 0.5

RING_TYPES = tuple(RingType)
EVASIONS = tuple(EvasionMode)

SIZE = (3, 8)
GROW_INTERVAL_S = (0.5, 20.0)
ACTION_INTERVAL_S = (0.5, 20.0)
ACTIVE_DURATION_S = (45.0, 120.0)
MUTATE_PROB = (0.0, 0.4)
INTENSITY = (0.2, 2.0)


@dataclass(frozen=True, slots=True)
class AttackPlan:
    """One candidate attack: a ring plus the dials that shape its behaviour."""

    ring_type: RingType
    evasion: EvasionMode
    size: int
    grow_interval_s: float
    action_interval_s: float
    active_duration_s: float
    mutate_prob: float
    intensity: float
    spawn_time_s: float = 8.0
    shared_attribute: str | None = None

    def expected_actions(self) -> float:
        """Roughly how many times this ring acts before it dissolves."""
        return self.active_duration_s / max(self.action_interval_s, 0.1)

    def time_to_full_size_s(self) -> float:
        """How long the ring takes to recruit its remaining members."""
        initial = min(2, self.size)
        return max(0, self.size - initial) * self.grow_interval_s

    def is_valid(self) -> bool:
        """
        Reject plans that evade only by failing to be a ring.

        Two degenerate ways to go undetected: never act, and never finish
        forming. Both are excluded so an evasion means the detector missed a
        ring that really assembled and really operated.
        """
        if self.size < 3 or self.expected_actions() < MIN_ACTIONS:
            return False
        return self.time_to_full_size_s() <= self.active_duration_s * MAX_FORMATION_FRACTION

    def to_ring_spec(self, ring_id: str = "adv_target") -> dict[str, Any]:
        """Render as the ring entry of a scenario config."""
        spec: dict[str, Any] = {
            "id": ring_id,
            "type": self.ring_type.value,
            "size": int(self.size),
            "spawn_time_s": round(self.spawn_time_s, 2),
            "evasion": self.evasion.value,
            "grow_interval_s": round(self.grow_interval_s, 2),
            "action_interval_s": round(self.action_interval_s, 2),
            "active_duration_s": round(self.active_duration_s, 2),
            "mutate_prob": round(self.mutate_prob, 3),
            "intensity": round(self.intensity, 2),
        }
        if self.ring_type == RingType.MONEY_CYCLE:
            spec["length"] = int(min(max(3, self.size), 6))
        if self.shared_attribute:
            spec["shared_attribute"] = self.shared_attribute
        return spec

    def summary(self) -> str:
        return (
            f"{self.ring_type.value}/{self.evasion.value} "
            f"size={self.size} grow={self.grow_interval_s:.1f}s "
            f"act={self.action_interval_s:.1f}s dur={self.active_duration_s:.0f}s"
        )

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ring_type"] = self.ring_type.value
        d["evasion"] = self.evasion.value
        return d


def _uniform(rng: random.Random, bounds: tuple[float, float]) -> float:
    return rng.uniform(*bounds)


def sample(rng: random.Random, hubs: tuple[str, ...] = ()) -> AttackPlan:
    """Draw a random valid plan."""
    for _ in range(50):
        ring_type = rng.choice(RING_TYPES)
        plan = AttackPlan(
            ring_type=ring_type,
            evasion=rng.choice(EVASIONS),
            size=rng.randint(*SIZE),
            grow_interval_s=_uniform(rng, GROW_INTERVAL_S),
            action_interval_s=_uniform(rng, ACTION_INTERVAL_S),
            active_duration_s=_uniform(rng, ACTIVE_DURATION_S),
            mutate_prob=_uniform(rng, MUTATE_PROB),
            intensity=_uniform(rng, INTENSITY),
            shared_attribute=(
                rng.choice(hubs) if hubs and ring_type in _ATTRIBUTE_RINGS and rng.random() < 0.5
                else None
            ),
        )
        if plan.is_valid():
            return plan
    # Bounds guarantee this is reachable; fall back to a known-valid plan.
    return AttackPlan(
        ring_type=RingType.SHARED_DEVICE,
        evasion=EvasionMode.NONE,
        size=4,
        grow_interval_s=2.0,
        action_interval_s=3.0,
        active_duration_s=90.0,
        mutate_prob=0.05,
        intensity=1.0,
    )


_ATTRIBUTE_RINGS = (RingType.SHARED_DEVICE, RingType.SHARED_CARD, RingType.SHARED_IP)

_NUMERIC = ("grow_interval_s", "action_interval_s", "active_duration_s", "mutate_prob", "intensity")
_BOUNDS = {
    "grow_interval_s": GROW_INTERVAL_S,
    "action_interval_s": ACTION_INTERVAL_S,
    "active_duration_s": ACTIVE_DURATION_S,
    "mutate_prob": MUTATE_PROB,
    "intensity": INTENSITY,
}


def mutate(plan: AttackPlan, rng: random.Random, hubs: tuple[str, ...] = ()) -> AttackPlan:
    """Perturb a plan slightly, keeping it valid."""
    for _ in range(50):
        changes: dict[str, Any] = {}
        field = rng.choice([*_NUMERIC, "size", "evasion", "ring_type", "shared_attribute"])
        if field in _NUMERIC:
            lo, hi = _BOUNDS[field]
            current = getattr(plan, field)
            step = (hi - lo) * 0.25
            changes[field] = min(hi, max(lo, current + rng.uniform(-step, step)))
        elif field == "size":
            changes["size"] = min(SIZE[1], max(SIZE[0], plan.size + rng.choice((-1, 1))))
        elif field == "evasion":
            changes["evasion"] = rng.choice(EVASIONS)
        elif field == "ring_type":
            changes["ring_type"] = rng.choice(RING_TYPES)
        else:
            changes["shared_attribute"] = rng.choice(hubs) if hubs and rng.random() < 0.6 else None

        candidate = replace(plan, **changes)
        if candidate.is_valid():
            return candidate
    return plan
