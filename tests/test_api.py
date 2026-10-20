"""
Unit tests for FastAPI endpoints.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fraudsim.api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


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
