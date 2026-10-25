"""
Unit tests for FastAPI endpoints.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fraudsim.api.main import app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # Live runs write their logs and audit trail under FRAUDSIM_RUNS_DIR/live/<run_id>.
    monkeypatch.setenv("FRAUDSIM_RUNS_DIR", str(tmp_path))
    # Context manager keeps one event loop alive across requests, so the background
    # player task started by /runs/start survives until the test ends.
    with TestClient(app) as c:
        yield c
        c.post("/runs/stop")


def test_health_endpoint(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"


def test_status_endpoint(client: TestClient):
    resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    assert "status" in data


def test_runs_lifecycle(client: TestClient):
    # Start
    start_resp = client.post("/runs/start", json={"scenario": "baseline", "seed": 42})
    assert start_resp.status_code == 200
    assert start_resp.json()["status"] == "running"

    # Pause
    pause_resp = client.post("/runs/pause")
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == "paused"

    # Resume
    resume_resp = client.post("/runs/resume")
    assert resume_resp.status_code == 200
    assert resume_resp.json()["status"] == "running"

    # Stop
    stop_resp = client.post("/runs/stop")
    assert stop_resp.status_code == 200
    assert stop_resp.json()["status"] == "stopped"


def test_metrics_and_prometheus(client: TestClient):
    resp = client.get("/metrics/summary")
    assert resp.status_code == 200
    assert "f1" in resp.json()
    # Without a run there is nothing to measure: no placeholder numbers.
    client.post("/runs/stop")
    empty = client.get("/metrics/summary").json()
    assert empty["precision"] is None and empty["latency_p50_ms"] is None

    prom_resp = client.get("/metrics")
    assert prom_resp.status_code == 200
    assert "fraudsim_events_total" in prom_resp.text


def test_live_stream_controls(client: TestClient):
    start = client.post(
        "/runs/start", json={"scenario": "baseline", "seed": 7, "speed_multiplier": 0}
    )
    assert start.status_code == 200
    run_id = start.json()["run_id"]

    speed = client.post("/runs/speed", json={"speed_multiplier": 5.0})
    assert speed.status_code == 200
    assert speed.json()["speed_multiplier"] == 5.0

    rate = client.post("/runs/rate", json={"events_per_second": 3.0})
    assert rate.status_code == 200
    assert rate.json()["rate"] == 3.0

    client.post("/runs/speed", json={"speed_multiplier": 0})
    for _ in range(50):
        status = client.get("/status").json()
        if status["events_emitted"] > 0:
            break
        time.sleep(0.02)
    assert status["events_emitted"] > 0
    assert status["run_id"] == run_id

    recent = client.get("/events/recent", params={"limit": 5}).json()
    assert 0 < len(recent) <= 5
    assert all(e["run_id"] == run_id for e in recent)
    assert all("ring" not in e["payload"] for e in recent)

    assert client.post("/runs/speed", json={"speed_multiplier": -1}).status_code == 422


def test_unknown_scenario_and_controls_without_run(client: TestClient):
    assert client.post("/runs/start", json={"scenario": "nope"}).status_code == 404
    client.post("/runs/stop")
    assert client.post("/runs/rate", json={"events_per_second": 1}).status_code == 409


def test_live_run_detects_rings(client: TestClient):
    client.post("/runs/start", json={"scenario": "baseline", "seed": 3, "speed_multiplier": 0})
    for _ in range(300):
        status = client.get("/status").json()
        if status["status"] == "finished":
            break
        time.sleep(0.05)
    time.sleep(0.6)  # let the consumer flush its last batch
    rings = client.get("/rings").json()
    assert any(r["status"] == "confirmed" for r in rings)
    one = client.get(f"/rings/{rings[0]['ring_id']}").json()
    assert one["members"] and one["explanation"]["detectors"]
    assert client.get("/status").json()["rings_detected"] >= 1


def test_live_metrics_come_from_the_run(client: TestClient):
    client.post("/runs/start", json={"scenario": "baseline", "seed": 3, "speed_multiplier": 0})
    for _ in range(300):
        if client.get("/status").json()["status"] == "finished":
            break
        time.sleep(0.05)
    time.sleep(0.6)
    m = client.get("/metrics/summary").json()
    assert m["recall"] is not None and 0.0 <= m["recall"] <= 1.0
    assert m["latency_p50_ms"] is not None and m["latency_p50_ms"] > 0
    assert m["summary"]["true_rings"] == 4
    prom = client.get("/metrics").text
    assert 'fraudsim_run_ring_recall{run_id="' in prom


def test_live_run_is_logged_audited_and_replayable(client: TestClient, tmp_path: Path):
    from fraudsim.logging.audit import AuditTrail
    from fraudsim.logging.replay import replay_run

    run_id = client.post(
        "/runs/start", json={"scenario": "baseline", "seed": 4, "speed_multiplier": 0}
    ).json()["run_id"]
    for _ in range(300):
        if client.get("/status").json()["status"] == "finished":
            break
        time.sleep(0.05)
    time.sleep(0.6)
    rings = client.get("/rings").json()
    assert rings
    resp = client.post(f"/rings/{rings[0]['ring_id']}/feedback", json={"action": "dismiss"})
    assert resp.json()["threshold_adaptation"]["action"] == "dismiss"
    client.post("/runs/stop")

    run_dir = tmp_path / "live" / run_id
    for name in (
        "events.jsonl",
        "detections.jsonl",
        "batches.jsonl",
        "audit.jsonl",
        "manifest.json",
    ):
        assert (run_dir / name).exists(), name
    trail = AuditTrail(run_dir, run_id)
    ok, problem = trail.verify()
    assert ok, problem
    kinds = [e.action_type for e in trail.read_entries()]
    assert "ANALYST_DISMISS" in kinds and "THRESHOLD_UPDATED" in kinds
    # Live batches were cut by wall-clock idle timeouts; replay reuses the logged
    # boundaries, so the detections must still be identical.
    result = replay_run(run_dir)
    assert result.identical, result.differences


def test_dashboard_controls(client: TestClient):
    names = {s["name"] for s in client.get("/scenarios").json()}
    assert {"baseline", "demo_story", "adversarial"} <= names
    cat = client.get("/catalog").json()
    assert "money_cycle" in cat["ring_types"] and "hub_hiding" in cat["evasion_modes"]
    assert client.post("/runs/inject", json={"ring_type": "shared_ip"}).status_code == 409

    # 30x real time (60 simulated seconds in about 2 s), paused before injecting: at
    # unthrottled speed a warm process emitted the whole stream before the injection
    # arrived, so the injected ring never activated and this test was flaky.
    client.post("/runs/start", json={"scenario": "hub_heavy", "seed": 2, "speed_multiplier": 30})
    client.post("/runs/pause")
    inj = client.post(
        "/runs/inject", json={"ring_type": "money_cycle", "evasion": "none", "size": 4}
    )
    assert inj.status_code == 200 and inj.json()["ring_id"].startswith("inj_")
    assert client.post("/runs/inject", json={"ring_type": "teleport"}).status_code == 422

    dets = client.post("/detectors/community", json={"enabled": False}).json()
    assert {d["type"]: d["enabled"] for d in dets}["community"] is False
    assert client.post("/detectors/nope", json={"enabled": False}).status_code == 404

    cfg = client.post(
        "/config", json={"alert_threshold": 0.7, "hub_weight_method": "adamic_adar"}
    ).json()
    assert cfg["alert_threshold"] == 0.7 and cfg["hub_weight_method"] == "adamic_adar"
    assert client.post("/config", json={"alert_threshold": 1.5}).status_code == 422

    client.post("/runs/resume")
    for _ in range(1200):  # up to 60 s: the run takes ~15 s and longer on a busy machine
        if client.get("/status").json()["status"] == "finished":
            break
        time.sleep(0.05)
    time.sleep(0.6)
    g = client.get("/graph", params={"oracle": True, "max_users": 100}).json()
    assert 0 < g["shown_users"] <= g["total_active_users"]
    ids = {n["id"] for n in g["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])
    assert any(n.get("hub") for n in g["nodes"])
    o = g["oracle"]
    assert any(r["true_ring"].startswith("inj_") for r in o["true_rings"])
    assert all(v["verdict"] in ("true", "false") for v in o["tracked"].values())
