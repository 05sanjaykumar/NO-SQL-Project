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
from fraudsim.metrics.exporters import write_run_metrics
from fraudsim.simulator.config import parse_simulation_config
from fraudsim.simulator.event_generator import EventGenerator
from fraudsim.simulator.scenario_loader import load_scenario
from fraudsim.types import RingStatus, config_hash, new_run_id


@dataclass(slots=True)
class DetectionRun:
    manifest: dict[str, Any]
    engine: DetectionEngine
    generator: EventGenerator
    metrics: Any = None  # fraudsim.metrics.evaluation.RunMetrics


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
    metrics = evaluate_detection(gen, engine, alert_mode)

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
        "metrics": {
            k: metrics.summary[k]
            for k in ("ring", "user", "rings_detected", "rings_missed", "false_positive_rings")
        },
        "python": platform.python_version(),
    }
    if run_dir is not None:
        write_run_metrics(metrics, run_dir / "metrics")
        gen.ledger.export_jsonl(run_dir / "ground_truth.jsonl")
        gen.ledger.export_log(run_dir / "ground_truth_log.jsonl")
        with open(run_dir / "rings.jsonl", "w", encoding="utf-8") as f:
            for ring in engine.tracker.rings.values():
                f.write(json.dumps(_ring_dict(ring), default=str) + "\n")
        with open(run_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, default=str)
        manifest["run_dir"] = str(run_dir)
    return DetectionRun(manifest, engine, gen, metrics)


def hub_degrees(gen: EventGenerator) -> dict[str, int]:
    """For each true ring: users on the busiest attribute its members share (end of run)."""
    world = gen.world
    out: dict[str, int] = {}
    for rid, ring in gen.ledger.rings.items():
        members = {u for _, a, u in ring.member_history if a == "join"}
        best = 0
        for kind, pool in world.pools.items():
            for u in members:
                for attr in world.users[u].attrs[kind] if u in world.users else ():
                    holders = pool.users.get(attr, set())
                    if len(holders & members) >= 2:
                        best = max(best, len(holders))
        out[rid] = best
    return out


def evaluate_detection(gen: EventGenerator, engine: DetectionEngine, scored: bool) -> Any:
    from fraudsim.metrics.evaluation import evaluate_run
    from fraudsim.scoring.early_warning import lead_times

    start_t = gen.ts(0.0).timestamp()
    end_t = gen.ts().timestamp()
    leads = lead_times(gen.ledger, engine.tracker) if scored else []
    lead_values = [x["lead_time_s"] for x in leads if x["lead_time_s"] is not None]
    from fraudsim.metrics.evaluation import percentiles

    return evaluate_run(
        gen.ledger,
        engine.tracker,
        engine.reports,
        start_t,
        end_t,
        scored,
        hub_degree_of=hub_degrees(gen),
        extra={
            "early_warning": {
                "lead_time_s": percentiles(lead_values),
                "rings_warned_before_activation": sum(1 for v in lead_values if v > 0),
            }
            if scored
            else None
        },
    )


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


def compare_modes(
    scenario: str,
    seed: int,
    backend: str = "memory",
    duration_s: float | None = None,
    detector_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Run the same stream with incremental detection and with full recomputation
    (no attacker feedback, so both see identical events) and compare latency
    and accuracy side by side.
    """
    out: dict[str, Any] = {"scenario": scenario, "seed": seed, "backend": backend}
    for mode, incremental in (("incremental", True), ("full", False)):
        result = run_detection(
            scenario,
            seed,
            None,
            backend=backend,
            engine_config=EngineConfig(incremental=incremental),
            detector_overrides=detector_overrides,
            attacker_feedback=False,
            duration_s=duration_s,
        )
        s = result.metrics.summary
        regions = [r.region for r in result.engine.reports]
        out[mode] = {
            "run_id": result.manifest["run_id"],
            "stream_digest": result.manifest["stream_digest"],
            "detect_ms": s["latency_ms"]["detect_per_batch"],
            "batch_total_ms": s["latency_ms"]["batch_total"],
            "events_per_s": s["throughput"]["events_per_s"],
            "region_users_p50": sorted(regions)[len(regions) // 2] if regions else None,
            "ring": s["ring"],
            "rings_detected": s["rings_detected"],
        }
    inc, full = out["incremental"]["detect_ms"]["p50"], out["full"]["detect_ms"]["p50"]
    out["speedup_p50"] = round(full / inc, 3) if inc else None
    return out
