"""
Headless experiment runner for reproducible evaluation across scenarios and seeds.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fraudsim.simulator.recorder import run_simulation
from fraudsim.simulator.scenario_loader import load_scenario

# Detection metrics are produced once the detection engine (Milestone 3) and metrics
# (Milestone 5) exist. Until then they are reported as None, never as placeholders.
DETECTION_METRICS = (
    "rings_detected",
    "precision",
    "recall",
    "f1",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
)


def run_single_experiment(
    scenario_name: str,
    seed: int,
    output_dir: Path,
    scenario_cfg: dict[str, Any],
) -> dict[str, Any]:
    """Execute a single scenario run for one seed and return what was actually measured."""
    run_id = f"exp_{scenario_name}_{seed}_{int(time.time())}"
    manifest = run_simulation(scenario_cfg, seed, output_dir / "runs", run_id=run_id)

    result: dict[str, Any] = {
        "run_id": run_id,
        "scenario": scenario_name,
        "seed": seed,
        "config_hash": manifest["config_hash"],
        "timestamp": datetime.now(UTC).isoformat(),
        "stream_digest": manifest["stream_digest"],
        "sim_time_s": manifest["sim_time_s"],
        "events_processed": manifest["events"],
        "rings_ground_truth": manifest["rings"]["total_rings"],
        "generation_wall_s": manifest["generation_wall_s"],
        "generation_events_per_s": manifest["generation_events_per_s"],
        **{k: None for k in DETECTION_METRICS},
        "detection_status": "not_run: detection engine arrives in Milestone 3",
    }

    run_file = output_dir / f"{run_id}.json"
    with open(run_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="FraudSim Headless Experiment Runner")
    parser.add_argument("--scenario", type=str, default="baseline", help="Scenario name")
    parser.add_argument("--seeds", type=int, default=5, help="Number of random seeds")
    parser.add_argument(
        "--output-dir", type=str, default="results/", help="Directory to save results"
    )
    args = parser.parse_args()

    out_path = Path(args.output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    scenario_cfg = load_scenario(args.scenario)

    print(f"=== Starting Experiment: scenario={args.scenario}, seeds={args.seeds} ===")
    results: list[dict[str, Any]] = []

    for seed_idx in range(args.seeds):
        seed = 42 + seed_idx * 17
        print(f"  -> Running seed {seed} ({seed_idx + 1}/{args.seeds})...")
        res = run_single_experiment(args.scenario, seed, out_path, scenario_cfg)
        results.append(res)

    # Write summary CSV
    summary_csv = out_path / f"{args.scenario}_summary.csv"
    if results:
        fieldnames = list(results[0].keys())
        with open(summary_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)

    print(f"=== Experiment Complete! Results written to {summary_csv} ===")


if __name__ == "__main__":
    main()
