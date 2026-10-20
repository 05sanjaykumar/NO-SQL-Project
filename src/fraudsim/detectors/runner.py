"""
Closed-loop headless run: simulator -> detection engine -> (flags back to the
adaptive attacker), writing every batch report and ring update to disk.

runs/<run_id>/
  events.jsonl           observable stream
  ground_truth.jsonl     ledger (evaluation only)
  batches.jsonl          one BatchReport per micro-batch (timings, region size, ...)
  ring_updates.jsonl     every tracked-ring change (created/confirmed/grew/.../dissolved)
  rings.jsonl            final state of every tracked ring
  manifest.json          run_id, scenario, seed, config hashes, engine/detector config
"""

from __future__ import annotations

import json
import platform
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fraudsim.detectors.engine import DetectionEngine, EngineConfig, build_detectors
from fraudsim.graph.store import GraphStore, InMemoryGraphStore
from fraudsim.simulator.config import parse_simulation_config
from fraudsim.simulator.event_generator import EventGenerator
from fraudsim.simulator.scenario_loader import load_scenario
from fraudsim.types import RingStatus, config_hash, new_run_id


@dataclass(slots=True)
class DetectionRun:
    manifest: dict[str, Any]
    engine: DetectionEngine
    generator: EventGenerator


def make_store(backend: str) -> GraphStore:
    if backend == "memory":
        return InMemoryGraphStore()
    if backend == "neo4j":
        import os

        from fraudsim.graph.neo4j_client import Neo4jClient
        from fraudsim.graph.neo4j_store import Neo4jGraphStore

        client = Neo4jClient(
            uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            user=os.environ.get("NEO4J_USER", "neo4j"),
            password=os.environ.get("NEO4J_PASSWORD", "fraudring123"),
        )
        client.wait_for_ready()
        client.wipe()
        client.ensure_schema()
        return Neo4jGraphStore(client)
    raise ValueError(f"unknown backend {backend!r} (memory | neo4j)")


def run_detection(
    scenario: str | dict[str, Any],
    seed: int,
    out_dir: Path | None,
    backend: str = "memory",
    engine_config: EngineConfig | None = None,
    detector_overrides: dict[str, dict[str, Any]] | None = None,
    attacker_feedback: bool = True,
    duration_s: float | None = None,
    run_id: str | None = None,
    scoring: Any = None,
) -> DetectionRun:
    """
    scoring: optional fraudsim.scoring.stage.ScoringStage. With a fitted model,
    the attacker is told about *alerts* (confirmed and risk >= threshold);
    without one, about confirmed rings.
    """
    raw = load_scenario(scenario) if isinstance(scenario, str) else scenario
    sim_cfg = parse_simulation_config(raw)
    run_id = run_id or new_run_id()
    gen = EventGenerator(seed=seed, run_id=run_id, config=sim_cfg)
    store = make_store(backend)
    ecfg = engine_config or EngineConfig()
    engine = DetectionEngine(
        store, build_detectors(store, detector_overrides), config=ecfg, scorer=scoring
    )
    alert_mode = scoring is not None and getattr(scoring, "scorer", None) is not None

    run_dir: Path | None = None
    files: dict[str, Any] = {}
    if out_dir is not None:
        run_dir = Path(out_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        for name in ("events", "batches", "ring_updates"):
            files[name] = open(run_dir / f"{name}.jsonl", "w", encoding="utf-8")  # noqa: SIM115

    flagged_sent: set[str] = set()
    feedback_calls = 0
    start = time.perf_counter()
    try:
        batch = []
        for event in gen.iter_events(duration_s):
            batch.append(event)
            if "events" in files:
                files["events"].write(json.dumps(event.to_dict(), separators=(",", ":")) + "\n")
            if len(batch) < ecfg.batch_size:
                continue
            report = engine.process(batch)
            batch = []
            _write_report(files, report)
            if attacker_feedback:
                newly = _flagged_in(report, alert_mode) - flagged_sent
                if newly:
                    gen.notify_flagged(newly)
                    flagged_sent |= newly
                    feedback_calls += 1
        if batch:
            _write_report(files, engine.process(batch))
    finally:
        for f in files.values():
            f.close()
    wall = time.perf_counter() - start

    manifest: dict[str, Any] = {
        "run_id": run_id,
        "scenario": sim_cfg.name,
        "seed": seed,
        "backend": backend,
        "config_hash": config_hash(
            {
                "scenario": raw,
                "seed": seed,
                "engine": ecfg.to_dict(),
                "detectors": detector_overrides or {},
                "feedback": attacker_feedback,
                "duration_s": duration_s,
            }
        ),
        "scenario_hash": config_hash(raw),
        "engine": ecfg.to_dict(),
        "detector_overrides": detector_overrides or {},
        "attacker_feedback": attacker_feedback,
        "feedback_calls": feedback_calls,
        "events": gen.events_emitted,
        "stream_digest": gen.digest,
        "ground_truth": gen.ledger.summary(),
        "detection": engine.summary(),
        "wall_s": round(wall, 4),
        "python": platform.python_version(),
    }
    if run_dir is not None:
        gen.ledger.export_jsonl(run_dir / "ground_truth.jsonl")
        gen.ledger.export_log(run_dir / "ground_truth_log.jsonl")
        with open(run_dir / "rings.jsonl", "w", encoding="utf-8") as f:
            for ring in engine.tracker.rings.values():
                f.write(json.dumps(_ring_dict(ring), default=str) + "\n")
        with open(run_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, default=str)
        manifest["run_dir"] = str(run_dir)
    return DetectionRun(manifest, engine, gen)


def _flagged_in(report: Any, alert_mode: bool) -> set[str]:
    if alert_mode:
        return {u for upd in report.updates if upd.change == "alert" for u in upd.members}
    out: set[str] = set()
    for upd in report.updates:
        if upd.change == "confirmed" or (
            upd.status == RingStatus.CONFIRMED and upd.change in ("grew", "created")
        ):
            out |= set(upd.members)
    return out


def _write_report(files: dict[str, Any], report: Any) -> None:
    if "batches" in files:
        data = report.to_dict()
        updates = data.pop("updates")
        data["n_updates"] = len(updates)
        files["batches"].write(json.dumps(data, separators=(",", ":")) + "\n")
        for upd in updates:
            files["ring_updates"].write(json.dumps(upd, separators=(",", ":")) + "\n")


def _ring_dict(ring: Any) -> dict[str, Any]:
    return {
        "ring_id": ring.ring_id,
        "status": ring.status.value,
        "ring_type": ring.ring_type.value,
        "members": sorted(ring.members),
        "score": ring.score,
        "sources": sorted(ring.sources),
        "first_seen": ring.first_seen.isoformat(),
        "last_seen": ring.last_seen.isoformat(),
        "confirmed_at": ring.confirmed_at.isoformat() if ring.confirmed_at else None,
        "dissolved_at": ring.dissolved_at.isoformat() if ring.dissolved_at else None,
        "parent": ring.parent,
        "history": ring.history,
        "evidence": ring.evidence,
        "risk": ring.risk,
        "features": ring.features,
        "explanation": ring.explanation,
        "alerted_at": ring.alerted_at.isoformat() if ring.alerted_at else None,
        "early_warned_at": ring.early_warned_at.isoformat() if ring.early_warned_at else None,
    }
