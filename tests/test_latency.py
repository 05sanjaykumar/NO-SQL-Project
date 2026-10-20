"""
Unit tests for LatencyTracker.
"""

from __future__ import annotations

from fraudsim.metrics.latency import LatencyTracker
from fraudsim.types import LatencyRecord


def test_latency_tracker_empty():
    tracker = LatencyTracker()
    stats = tracker.stats("ingest")
    assert stats == {"count": 0}


def test_latency_tracker_percentiles():
    tracker = LatencyTracker()
    # Add values 1 to 100 ms
    for i in range(1, 101):
        tracker.record("detect", float(i))

    stats = tracker.stats("detect")
    assert stats["count"] == 100
    assert stats["min"] == 1.0
    assert stats["max"] == 100.0
    assert stats["mean"] == 50.5
    assert stats["p50"] == 51.0
    assert stats["p95"] == 96.0
    assert stats["p99"] == 100.0


def test_latency_tracker_reset_and_all():
    tracker = LatencyTracker()
    tracker.add(LatencyRecord(event_id="e1", component="graph_write", duration_ms=4.5))
    tracker.add(LatencyRecord(event_id="e2", component="detector", duration_ms=12.2))

    all_s = tracker.all_stats()
    assert "graph_write" in all_s
    assert "detector" in all_s
    assert all_s["graph_write"]["count"] == 1

    tracker.reset()
    assert tracker.all_stats() == {}
