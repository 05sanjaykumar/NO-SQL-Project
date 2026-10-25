"""
Milestone 9: a full 60-second scenario run headless, end to end, asserting that
metrics are produced and that the run is deterministic and replayable.
"""

from __future__ import annotations

import json
from pathlib import Path

from fraudsim.detectors.runner import run_detection
from fraudsim.logging.replay import replay_run
from fraudsim.simulator.scenario_loader import load_scenario


def _run_dir(root: Path) -> Path:
    (d,) = [p for p in root.iterdir() if p.is_dir()]
    return d


def test_sixty_second_scenario_headless(tmp_path: Path) -> None:
    assert load_scenario("baseline")["simulation"]["duration_seconds"] == 60
    run = run_detection("baseline", 11, tmp_path)
    d = _run_dir(tmp_path)

    for name in (
        "events.jsonl",
        "detections.jsonl",
        "metrics.jsonl",
        "audit.jsonl",
        "manifest.json",
    ):
        assert (d / name).exists(), name
    summary = json.loads((d / "metrics" / "summary.json").read_text(encoding="utf-8"))
    assert (d / "metrics" / "metrics.prom").read_text(encoding="utf-8").strip()

    assert summary["true_rings"] == 4
    ring = summary["ring"]
    assert ring["tp"] + ring["fn"] == summary["true_rings"]
    assert ring["tp"] + ring["fp"] == summary["rings_flagged"]
    for key in ("precision", "recall", "f1"):
        assert ring[key] is None or 0.0 <= ring[key] <= 1.0
    assert ring["recall"] > 0  # the clean scenario's rings are found
    assert summary["latency_ms"]["detect_per_batch"]["p50"] is not None
    assert summary["throughput"]["events_per_s"] > 0

    # Same scenario and seed: identical stream and identical metrics.
    again = run_detection("baseline", 11, None)
    assert again.manifest["stream_digest"] == run.manifest["stream_digest"]
    assert again.metrics.summary["ring"] == summary["ring"]


def test_headless_run_replays_identically(tmp_path: Path) -> None:
    run_detection("hub_heavy", 5, tmp_path)
    result = replay_run(_run_dir(tmp_path))
    assert result.identical, result.differences
    assert result.detection_records > 0
