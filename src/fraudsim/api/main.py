"""
FastAPI application with REST routes and WebSocket streaming.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from fraudsim.api.live import LiveRun
from fraudsim.api.schemas import (
    AnalystFeedbackRequest,
    HealthResponse,
    MetricsResponse,
    RateRequest,
    RingDetailResponse,
    RunStatusResponse,
    SpeedRequest,
    StartRunRequest,
)
from fraudsim.api.state import state
from fraudsim.metrics.exporters import prometheus_text
from fraudsim.simulator.scenario_loader import list_scenarios
from fraudsim.types import new_run_id

app = FastAPI(
    title="FraudSim API",
    description="Hub-Aware Incremental Fraud Ring Detection API & Streaming Dashboard Service",
    version="0.1.0",
)

# Enable CORS for dashboard development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    neo4j_ok = False
    if state.neo4j_client is not None:
        try:
            neo4j_ok = state.neo4j_client.verify_connectivity()
        except Exception:
            neo4j_ok = False
    return HealthResponse(status="ok", version="0.1.0", neo4j_connected=neo4j_ok)


@app.get("/status", response_model=RunStatusResponse)
async def get_status() -> RunStatusResponse:
    live = state.live.snapshot() if state.live is not None else {}
    if state.live is not None:
        state.status = state.live.status
        state.events_emitted = live["events_emitted"]
    return RunStatusResponse(
        run_id=state.run_id,
        status=state.status,
        scenario=state.scenario,
        seed=state.seed,
        events_emitted=state.events_emitted,
        rings_detected=(
            len(state.live.engine.tracker.confirmed())
            if state.live is not None
            else len(state.active_rings)
        ),
        speed_multiplier=live.get("speed_multiplier", state.speed_multiplier),
        sim_time_s=live.get("sim_time_s", 0.0),
        rate=live.get("rate", 0.0),
    )


@app.post("/runs/start", response_model=RunStatusResponse)
async def start_run(req: StartRunRequest) -> RunStatusResponse:
    if req.scenario not in list_scenarios():
        raise HTTPException(status_code=404, detail=f"Unknown scenario '{req.scenario}'")
    if state.live is not None:
        await state.live.stop()
        state.live = None
    state.run_id = new_run_id()
    state.scenario = req.scenario
    state.seed = req.seed
    state.speed_multiplier = req.speed_multiplier
    state.events_emitted = 0
    state.active_rings.clear()
    state.live = LiveRun(
        scenario=req.scenario,
        seed=req.seed,
        run_id=state.run_id,
        speed=req.speed_multiplier,
        broadcast=state.broadcast_ws,
    )
    await state.live.start()

    await state.broadcast_ws(
        {
            "type": "RUN_STARTED",
            "run_id": state.run_id,
            "scenario": state.scenario,
            "seed": state.seed,
        }
    )

    return await get_status()


@app.post("/runs/pause", response_model=RunStatusResponse)
async def pause_run() -> RunStatusResponse:
    if state.live is not None and state.live.status == "running":
        state.live.pause()
        await state.broadcast_ws({"type": "RUN_PAUSED", "run_id": state.run_id})
    return await get_status()


@app.post("/runs/resume", response_model=RunStatusResponse)
async def resume_run() -> RunStatusResponse:
    if state.live is not None and state.live.status == "paused":
        state.live.resume()
        await state.broadcast_ws({"type": "RUN_RESUMED", "run_id": state.run_id})
    return await get_status()


@app.post("/runs/stop", response_model=RunStatusResponse)
async def stop_run() -> RunStatusResponse:
    if state.live is not None:
        await state.live.stop()
        state.events_emitted = state.live.events_consumed
        state.live = None
    state.status = "stopped"
    await state.broadcast_ws({"type": "RUN_STOPPED", "run_id": state.run_id})
    return await get_status()


@app.post("/runs/speed", response_model=RunStatusResponse)
async def set_speed(req: SpeedRequest) -> RunStatusResponse:
    """Change the speed multiplier (simulated seconds per wall second; 0 = max)."""
    if state.live is None:
        raise HTTPException(status_code=409, detail="No run in progress")
    state.live.set_speed(req.speed_multiplier)
    state.speed_multiplier = req.speed_multiplier
    await state.broadcast_ws({"type": "RUN_SPEED", "speed_multiplier": req.speed_multiplier})
    return await get_status()


@app.post("/runs/rate", response_model=RunStatusResponse)
async def set_rate(req: RateRequest) -> RunStatusResponse:
    """Change the benign event rate (events per simulated second)."""
    if state.live is None:
        raise HTTPException(status_code=409, detail="No run in progress")
    state.live.set_rate(req.events_per_second)
    await state.broadcast_ws({"type": "RUN_RATE", "events_per_second": req.events_per_second})
    return await get_status()


@app.get("/events/recent")
async def recent_events(limit: int = 50) -> list[dict[str, Any]]:
    """Tail of the observable event stream for the current run."""
    if state.live is None:
        return []
    return list(state.live.recent)[-max(0, limit) :]


def _sync_rings() -> None:
    if state.live is not None:
        state.active_rings.update({r["ring_id"]: r for r in state.live.rings()})


@app.get("/rings", response_model=list[RingDetailResponse])
async def list_rings() -> list[RingDetailResponse]:
    _sync_rings()
    return [RingDetailResponse(**r) for r in state.active_rings.values()]


@app.get("/rings/{ring_id}", response_model=RingDetailResponse)
async def get_ring(ring_id: str) -> RingDetailResponse:
    _sync_rings()
    if ring_id not in state.active_rings:
        raise HTTPException(status_code=404, detail=f"Ring '{ring_id}' not found")
    return RingDetailResponse(**state.active_rings[ring_id])


@app.post("/rings/{ring_id}/feedback")
async def analyst_feedback(ring_id: str, feedback: AnalystFeedbackRequest) -> dict[str, Any]:
    if ring_id not in state.active_rings:
        raise HTTPException(status_code=404, detail=f"Ring '{ring_id}' not found")
    ring = state.active_rings[ring_id]
    ring["status"] = "confirmed" if feedback.action == "confirm" else "dissolved"
    ring["updated_at"] = datetime.now(UTC).isoformat()
    adaptation = None
    if state.live is not None:
        risk = ring.get("explanation", {}).get("risk")
        adaptation = state.live.scoring.policy.feedback(
            ring_id,
            ring["ring_type"],
            float(risk if risk is not None else ring["score"]),
            feedback.action,
        )

    await state.broadcast_ws(
        {
            "type": "ANALYST_FEEDBACK",
            "ring_id": ring_id,
            "action": feedback.action,
            "analyst_id": feedback.analyst_id,
            "note": feedback.note,
        }
    )
    return {
        "status": "success",
        "ring_id": ring_id,
        "new_status": ring["status"],
        "threshold_adaptation": adaptation,
    }


def _live_summary() -> dict[str, Any]:
    if state.live is None or not state.live.engine.reports:
        return {}
    return state.live.metrics().summary


@app.get("/metrics/summary", response_model=MetricsResponse)
async def get_metrics_summary() -> MetricsResponse:
    """Metrics of the current run, computed from its batch reports and ground truth."""
    s = _live_summary()
    if not s:
        return MetricsResponse(total_events=state.events_emitted, run_id=state.run_id)
    det = s["latency_ms"]["detect_per_batch"]
    return MetricsResponse(
        run_id=state.run_id,
        sim_time_s=state.live.snapshot()["sim_time_s"] if state.live else None,
        precision=s["ring"]["precision"],
        recall=s["ring"]["recall"],
        f1=s["ring"]["f1"],
        rings_detected=s["rings_detected"],
        rings_missed=s["rings_missed"],
        false_positive_rings=s["false_positive_rings"],
        user_precision=s["user"]["precision"],
        user_recall=s["user"]["recall"],
        time_to_detect_p50_s=s["time_to_detect_s"]["p50"],
        latency_p50_ms=det["p50"],
        latency_p95_ms=det["p95"],
        latency_p99_ms=det["p99"],
        throughput_events_per_sec=s["throughput"]["events_per_s"],
        total_events=state.events_emitted,
        active_rings=len(state.live.engine.tracker.live()) if state.live else 0,
        summary=s,
    )


@app.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics() -> PlainTextResponse:
    """Prometheus-style metrics: run controls plus every numeric metric of the live run."""
    lines = [
        "# HELP fraudsim_events_total Total events processed",
        "# TYPE fraudsim_events_total counter",
        f"fraudsim_events_total {state.events_emitted}",
        "# HELP fraudsim_active_rings Total active fraud rings detected",
        "# TYPE fraudsim_active_rings gauge",
        f"fraudsim_active_rings {len(state.active_rings)}",
        "# HELP fraudsim_speed_multiplier Current simulation speed",
        "# TYPE fraudsim_speed_multiplier gauge",
        f"fraudsim_speed_multiplier {state.speed_multiplier}",
    ]
    text = "\n".join(lines) + "\n"
    summary = _live_summary()
    if summary:
        text += prometheus_text(summary, prefix="fraudsim_run", labels=f'run_id="{state.run_id}"')
    return PlainTextResponse(text)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    state.ws_clients.add(websocket)
    try:
        # Send initial status
        await websocket.send_json(
            {
                "type": "INIT_STATE",
                "run_id": state.run_id,
                "status": state.status,
                "scenario": state.scenario,
                "speed_multiplier": state.speed_multiplier,
                "rings": list(state.active_rings.values()),
            }
        )
        while True:
            _ = await websocket.receive_text()
            # Handle client actions over WS if any
    except WebSocketDisconnect:
        state.ws_clients.discard(websocket)
    except Exception:
        state.ws_clients.discard(websocket)
