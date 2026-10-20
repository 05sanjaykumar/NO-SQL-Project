"""
Tests for the legacy seed.py bridge and order-preserving graph writes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fraudsim.graph.neo4j_client import group_consecutive
from fraudsim.simulator.event_generator import EventGenerator
from fraudsim.types import Event, EventType, RingType


def test_seed_events_carry_no_labels_and_ledger_has_the_four_rings() -> None:
    gen = EventGenerator(seed=42, run_id="seed")
    events = gen.generate_seed_events()
    assert all("ring" not in e.payload for e in events)

    rings = gen.ledger.rings
    assert set(rings) == {"seed_A", "seed_B", "seed_C", "seed_cycle"}
    assert rings["seed_A"].members == {"u01", "u02", "u03", "u04"}
    assert rings["seed_A"].shared_attributes == {"device": "device_007"}
    assert rings["seed_B"].ring_type == RingType.SHARED_CARD
    assert rings["seed_C"].members == {"u27", "u28", "u29"}
    assert rings["seed_cycle"].members == {"u21", "u22", "u23"}
    for ring in rings.values():
        assert ring.spawn_ts <= ring.activate_ts

    # The office-wifi hub (12 users on ip_001) is not a ring. Some hub users belong to
    # the cycle ring in seed.py, but the hub as a group is never labelled fraud.
    hub_users = {e.payload["user_id"] for e in events if e.payload.get("ip") == "ip_001"}
    assert len(hub_users) == 12
    assert not any(r.members >= hub_users for r in rings.values())
    assert all("ip_001" not in r.shared_attributes.values() for r in rings.values())


def _ev(seq: int, etype: EventType, **payload: object) -> Event:
    return Event(
        event_id=f"e{seq}",
        event_type=etype,
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        payload=dict(payload),
        seq=seq,
    )


def test_group_consecutive_preserves_order() -> None:
    events = [
        _ev(1, EventType.USER_JOIN, user_id="u1"),
        _ev(2, EventType.DEVICE_LINK, user_id="u1", device_id="d1"),
        _ev(3, EventType.ATTRIBUTE_UNLINK, user_id="u1", attr_type="device", attr_id="d1"),
        _ev(4, EventType.DEVICE_LINK, user_id="u1", device_id="d1"),
        _ev(5, EventType.DEVICE_LINK, user_id="u2", device_id="d1"),
    ]
    groups = group_consecutive(events)
    assert [g[0] for g in groups] == [
        EventType.USER_JOIN,
        EventType.DEVICE_LINK,
        EventType.ATTRIBUTE_UNLINK,
        EventType.DEVICE_LINK,
    ]
    assert [r["event_id"] for r in groups[-1][1]] == ["e4", "e5"]


def test_group_consecutive_refuses_ground_truth_events() -> None:
    with pytest.raises(ValueError, match="ground-truth"):
        group_consecutive([_ev(1, EventType.RING_SPAWN, ring_id="x")])
