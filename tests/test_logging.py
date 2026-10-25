"""
Unit tests for structured logging and audit trail.
"""

from __future__ import annotations

import json
from pathlib import Path

from fraudsim.logging.audit import AuditTrail, load_events_for_replay
from fraudsim.logging.logger import FraudSimLogger


def test_structured_logger(tmp_path: Path):
    logger = FraudSimLogger(log_dir=tmp_path, run_id="run_test_123")
    logger.log_event({"event_type": "transaction", "amount": 100})
    logger.log_detection({"ring_id": "r1", "score": 0.95})
    logger.log_metric({"metric_name": "f1", "value": 0.98})
    logger.log_error("Test error message")

    assert (tmp_path / "events.jsonl").exists()
    assert (tmp_path / "detections.jsonl").exists()
    assert (tmp_path / "metrics.jsonl").exists()
    assert (tmp_path / "errors.log").exists()

    # Verify JSON structure
    with open(tmp_path / "events.jsonl", encoding="utf-8") as f:
        data = json.loads(f.readline())
        assert data["run_id"] == "run_test_123"
        assert data["component"] == "event_bus"
        assert data["event_type"] == "transaction"


def test_audit_trail_and_replay(tmp_path: Path):
    audit = AuditTrail(log_dir=tmp_path, run_id="run_audit_test")
    entry = audit.record("FLAG_DETECTED", "ring_x", {"score": 0.92})
    assert entry.action_type == "FLAG_DETECTED"
    assert entry.ring_id == "ring_x"

    entries = audit.read_entries()
    assert len(entries) == 1
    assert entries[0].ring_id == "ring_x"

    # Test replay iterator
    events_file = tmp_path / "events.jsonl"
    with open(events_file, "w", encoding="utf-8") as f:
        f.write(json.dumps({"event_id": "e1", "type": "user_join"}) + "\n")
        f.write(json.dumps({"event_id": "e2", "type": "transaction"}) + "\n")

    replayed = list(load_events_for_replay(events_file))
    assert len(replayed) == 2
    assert replayed[0]["event_id"] == "e1"
