"""
Unit tests for core domain types, identifiers, and configuration hashes.
"""

from __future__ import annotations

from fraudsim.types import (
    DetectorType,
    Event,
    EventType,
    RingCandidate,
    RingType,
    config_hash,
    new_event_id,
    new_run_id,
)


def test_identifiers_uniqueness():
    e1 = new_event_id()
    e2 = new_event_id()
    assert e1 != e2
    assert len(e1) == 32

    r1 = new_run_id()
    r2 = new_run_id()
    assert r1 != r2
    assert len(r1) == 12


def test_config_hash_deterministic():
    cfg1 = {"scenario": "baseline", "seed": 42, "speed": 1.5}
    cfg2 = {"speed": 1.5, "scenario": "baseline", "seed": 42}  # key order swapped
    cfg3 = {"scenario": "baseline", "seed": 43, "speed": 1.5}

    assert config_hash(cfg1) == config_hash(cfg2)
    assert config_hash(cfg1) != config_hash(cfg3)


def test_event_serialization():
    event = Event.now(
        event_type=EventType.TRANSACTION,
        payload={"from_user": "u1", "to_user": "u2", "amount": 100.0},
    )
    d = event.to_dict()
    assert d["event_type"] == "transaction"
    assert d["payload"]["amount"] == 100.0
    assert "event_id" in d
    assert "ts" in d

    restored = Event.from_dict(d)
    assert restored.event_id == event.event_id
    assert restored.event_type == EventType.TRANSACTION


def test_ring_candidate_serialization():
    cand = RingCandidate(
        ring_id="cand_01",
        detector=DetectorType.STATIC_CYPHER,
        ring_type=RingType.SHARED_DEVICE,
        members={"u01", "u02", "u03"},
        score=0.92,
        evidence={"nodes": ["u01", "u02", "u03", "device_007"]},
    )
    d = cand.to_dict()
    assert d["detector"] == "static_cypher"
    assert set(d["members"]) == {"u01", "u02", "u03"}
    assert d["score"] == 0.92
