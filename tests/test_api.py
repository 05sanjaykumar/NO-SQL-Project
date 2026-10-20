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
