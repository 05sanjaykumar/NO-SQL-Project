"""Unit tests for the adversarial search space.

No simulation here: these cover the rules that keep a "found evasion" honest.
"""

from __future__ import annotations

import random

import pytest

from fraudsim.adversary.space import (
    MAX_FORMATION_FRACTION,
    MIN_ACTIONS,
    AttackPlan,
    mutate,
    sample,
)
from fraudsim.types import EvasionMode, RingType


def _plan(**kw) -> AttackPlan:
    base = dict(
        ring_type=RingType.SHARED_DEVICE,
        evasion=EvasionMode.NONE,
        size=4,
        grow_interval_s=2.0,
        action_interval_s=3.0,
        active_duration_s=90.0,
        mutate_prob=0.05,
        intensity=1.0,
    )
    return AttackPlan(**{**base, **kw})


def test_a_normal_plan_is_valid() -> None:
    assert _plan().is_valid()


def test_a_ring_that_never_acts_is_rejected() -> None:
    # Acting once every 60s for 90s is fewer than MIN_ACTIONS actions.
    plan = _plan(action_interval_s=60.0, active_duration_s=90.0)
    assert plan.expected_actions() < MIN_ACTIONS
    assert not plan.is_valid()


def test_a_ring_that_never_finishes_forming_is_rejected() -> None:
    # Recruiting 2 more members at 60s each cannot finish inside 90s.
    plan = _plan(size=4, grow_interval_s=60.0, active_duration_s=90.0)
    assert plan.time_to_full_size_s() > plan.active_duration_s * MAX_FORMATION_FRACTION
    assert not plan.is_valid()


def test_a_two_member_ring_is_not_a_ring() -> None:
    assert not _plan(size=2).is_valid()


@pytest.mark.parametrize("seed", range(25))
def test_sampling_only_produces_valid_plans(seed: int) -> None:
    assert sample(random.Random(seed), hubs=("office_wifi",)).is_valid()


@pytest.mark.parametrize("seed", range(25))
def test_mutation_preserves_validity(seed: int) -> None:
    rng = random.Random(seed)
    assert mutate(sample(rng), rng, hubs=("office_wifi",)).is_valid()


def test_money_cycle_gets_a_length() -> None:
    spec = _plan(ring_type=RingType.MONEY_CYCLE, size=5).to_ring_spec()
    assert 3 <= spec["length"] <= 6


def test_ring_spec_carries_the_dials() -> None:
    spec = _plan(grow_interval_s=4.25).to_ring_spec("r1")
    assert spec["id"] == "r1"
    assert spec["grow_interval_s"] == 4.25
    assert spec["evasion"] == "none"
