"""
Pytest configuration and shared fixtures for FraudSim test suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure src/ is on sys.path for test discovery
src_dir = Path(__file__).resolve().parents[1] / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from fraudsim.types import Event, EventType, GroundTruthRing, RingType


@pytest.fixture
def sample_event() -> Event:
    """Fixture providing a sample user join event."""
    return Event(
        event_type=EventType.USER_JOIN,
        payload={"user_id": "u99", "name": "Test User"},
    )


@pytest.fixture
def sample_gt_ring() -> GroundTruthRing:
    """Fixture providing a ground-truth ring."""
    return GroundTruthRing(
        ring_id="ring_test_01",
        ring_type=RingType.SHARED_DEVICE,
        initial_members={"u01", "u02", "u03", "u04"},
        current_members={"u01", "u02", "u03", "u04"},
        shared_attributes={"device_007"},
    )
