"""Milestone 6: hash-chained audit trail, structured logs, deterministic replay."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fraudsim.detectors.runner import run_detection
from fraudsim.logging.audit import AuditTrail, replay_audit
from fraudsim.logging.replay import replay_run
from fraudsim.scoring.scorer import RingScorer
from fraudsim.scoring.stage import ScoringStage

MODEL = Path(__file__).resolve().parents[1] / "models" / "ring_scorer_lr.joblib"


def test_audit_chain_detects_tampering(tmp_path: Path) -> None:
    trail = AuditTrail(tmp_path, "r1")
    for i in range(5):
        trail.record(
            "STATUS_CHANGED", f"R{i}", {"status": "confirmed"}, ts=f"2026-01-01T00:00:0{i}"
        )
    assert trail.verify() == (True, None)
    lines = trail.audit_file.read_text().splitlines()

    edited = json.loads(lines[2])
    edited["details"]["status"] = "dissolved"
    trail.audit_file.write_text("\n".join([*lines[:2], json.dumps(edited), *lines[3:]]) + "\n")
    ok, problem = trail.verify()
    assert not ok and "entry 3" in problem

    trail.audit_file.write_text("\n".join(lines[:2] + lines[3:]) + "\n")  # deleted entry
    assert not trail.verify()[0]
    trail.audit_file.write_text("\n".join([lines[1], lines[0], *lines[2:]]) + "\n")  # reordered
    assert not trail.verify()[0]

    trail.audit_file.write_text("\n".join(lines) + "\n")
    more = AuditTrail(tmp_path, "r1")  # reopening continues the chain
    more.record("FLAG_RAISED", "R9", {})
    assert more.verify() == (True, None) and more.read_entries()[-1].seq == 6


@pytest.fixture(scope="module")
def logged_run(tmp_path_factory: pytest.TempPathFactory):
    out = tmp_path_factory.mktemp("runs")
    scoring = ScoringStage(scorer=RingScorer.load(MODEL)) if MODEL.exists() else None
    return run_detection("adversarial", 42, out, duration_s=70.0, scoring=scoring)


def test_structured_log_streams(logged_run) -> None:
    run_dir = Path(logged_run.manifest["run_dir"])
    events = [json.loads(x) for x in (run_dir / "events.jsonl").read_text().splitlines()]
    assert len(events) == logged_run.manifest["events"]
    assert all(e["run_id"] == logged_run.manifest["run_id"] and e["event_id"] for e in events)
    dets = [json.loads(x) for x in (run_dir / "detections.jsonl").read_text().splitlines()]
    assert dets and all({"run_id", "component", "event_id", "ts", "change"} <= set(d) for d in dets)
    flagged = [d for d in dets if d["change"] in ("alert", "confirmed")]
    assert flagged and all(d.get("explanation") for d in flagged if d["change"] == "alert")
    metrics = [json.loads(x) for x in (run_dir / "metrics.jsonl").read_text().splitlines()]
    assert metrics[-1]["component"] == "evaluation" and "summary" in metrics[-1]


def test_replay_is_identical_and_audit_rebuilds_state(logged_run) -> None:
    run_dir = Path(logged_run.manifest["run_dir"])
    result = replay_run(run_dir)
    assert result.identical, result.differences
    assert result.detection_records > 0 and result.audit_entries == result.detection_records

    trail = AuditTrail(run_dir, logged_run.manifest["run_id"])
    assert trail.verify() == (True, None)
    states = replay_audit(trail.read_entries())
    for ring in logged_run.engine.tracker.rings.values():
        assert states[ring.ring_id].status == ring.status.value
        assert states[ring.ring_id].members == sorted(ring.members)


def test_replay_detects_changed_logs(logged_run, tmp_path: Path) -> None:
    import shutil

    src = Path(logged_run.manifest["run_dir"])
    copy = tmp_path / src.name
    shutil.copytree(src, copy)
    lines = (copy / "detections.jsonl").read_text().splitlines()
    rec = json.loads(lines[0])
    rec["members"] = rec["members"][:-1]
    (copy / "detections.jsonl").write_text("\n".join([json.dumps(rec), *lines[1:]]) + "\n")
    result = replay_run(copy)
    assert not result.identical and "detection #1" in result.differences[0]

    if logged_run.manifest.get("model"):
        fake = tmp_path / "other.joblib"
        fake.write_bytes(b"not the model")
        with pytest.raises(ValueError, match="differs"):
            replay_run(src, model_path=fake)
