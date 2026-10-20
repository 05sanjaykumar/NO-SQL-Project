"""
Unit tests for FastAPI endpoints.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from fraudsim.api.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
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
