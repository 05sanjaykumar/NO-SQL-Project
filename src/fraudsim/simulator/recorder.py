"""
Headless simulation runs with on-disk artefacts.

A run writes everything needed to audit or replay it under runs/<run_id>/:

- events.jsonl            the observable stream (what detectors will see)
- ground_truth.jsonl      one line per ring: type, evasion, members over time, timestamps
- ground_truth_log.jsonl  append-only ring lifecycle log
- hubs.jsonl              benign shared infrastructure (evaluation only, never for detectors)
- manifest.json           run_id, scenario, seed, config hash, counts, stream digest, timings
"""

from __future__ import annotations

import json
import platform
import time
from pathlib import Path
from typing import Any

from fraudsim.simulator.config import parse_simulation_config
from fraudsim.simulator.event_generator import EventGenerator
from fraudsim.simulator.scenario_loader import load_scenario
from fraudsim.types import config_hash, new_run_id


def run_simulation(
    scenario: str | dict[str, Any],
    seed: int,
    out_dir: Path | None,
    duration_s: float | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run one scenario headless and (optionally) write its artefacts. Returns the manifest."""
    raw = load_scenario(scenario) if isinstance(scenario, str) else scenario
    config = parse_simulation_config(raw)
    run_id = run_id or new_run_id()
    gen = EventGenerator(seed=seed, run_id=run_id, config=config)

    run_dir: Path | None = None
    events_file = None
    if out_dir is not None:
        run_dir = Path(out_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        events_file = open(run_dir / "events.jsonl", "w", encoding="utf-8")  # noqa: SIM115

    start = time.perf_counter()
    try:
        for event in gen.iter_events(duration_s):
            if events_file is not None:
                events_file.write(json.dumps(event.to_dict(), separators=(",", ":")) + "\n")
    finally:
        if events_file is not None:
            events_file.close()
    wall = time.perf_counter() - start

    summary = gen.summary()
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "scenario": config.name,
        "seed": seed,
        "config_hash": config_hash({"scenario": raw, "seed": seed, "duration_s": duration_s}),
        "scenario_hash": config_hash(raw),
        "duration_s": duration_s if duration_s is not None else config.duration_seconds,
        "sim_time_s": summary["sim_time_s"],
        "events": summary["events"],
        "events_by_type": summary["events_by_type"],
        "users_total": summary["users_total"],
        "users_active_at_end": summary["users_active"],
        "hubs": summary["hubs"],
        "rings": summary["rings"],
        "adaptive_history": summary["adaptive_history"],
        "control_log": gen.control_log,
        "stream_digest": gen.digest,
        "generation_wall_s": round(wall, 6),
        "generation_events_per_s": round(summary["events"] / wall, 1) if wall > 0 else None,
        "python": platform.python_version(),
    }
    if run_dir is not None:
        gen.ledger.export_jsonl(run_dir / "ground_truth.jsonl")
        gen.ledger.export_log(run_dir / "ground_truth_log.jsonl")
        with open(run_dir / "hubs.jsonl", "w", encoding="utf-8") as f:
            for hub in gen.hubs.values():
                f.write(json.dumps(hub, sort_keys=True) + "\n")
        with open(run_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, default=str)
        manifest["run_dir"] = str(run_dir)
    return manifest
