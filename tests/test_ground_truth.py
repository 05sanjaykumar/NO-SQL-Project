"""
Unit tests for the GroundTruthLedger.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fraudsim.simulator.ground_truth import GroundTruthLedger
from fraudsim.types import EvasionMode, RingLifecycle, RingType


def test_ground_truth_lifecycle():
    ledger = GroundTruthLedger()
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    t1 = datetime(2026, 1, 1, 10, 5, tzinfo=UTC)
    t2 = datetime(2026, 1, 1, 10, 10, tzinfo=UTC)
    t3 = datetime(2026, 1, 1, 10, 20, tzinfo=UTC)

    # 1. Spawn
    ring = ledger.spawn_ring(
        ring_id="ring_test",
        ring_type=RingType.SHARED_DEVICE,
        members={"u01", "u02"},
        ts=t0,
        evasion_mode=EvasionMode.HUB_HIDING,
    )
    assert ring.lifecycle == RingLifecycle.SPAWNED
    assert ring.members == {"u01", "u02"}
    assert len(ledger.rings) == 1

    # 2. Activate
    ledger.activate_ring("ring_test", t1)
    assert ring.lifecycle == RingLifecycle.ACTIVE
    assert len(ledger.active_rings()) == 1

    # 3. Grow
    ledger.grow_ring("ring_test", "u03", t2)
    assert ring.members == {"u01", "u02", "u03"}
    assert ring.lifecycle == RingLifecycle.GROWING

    # 4. Dissolve
    ledger.dissolve_ring("ring_test", t3)
    assert ring.lifecycle == RingLifecycle.DISSOLVED
    assert len(ledger.active_rings()) == 0
