"""
Train and evaluate the ring risk model on simulator ground truth.

Protocol (no leakage):
- Training data comes from `train_seeds`, test data from disjoint `test_seeds`.
- Within the training runs the split is by time: rows from the first
  `fit_fraction` of each run's duration fit the model, rows from the rest fit
  the isotonic calibrator.
- A row is one scored snapshot of a tracked ring (features at that moment).
  Its label is 1 if the ring's members overlap some ground-truth ring's
  members (all members it ever had) with Jaccard >= `label_jaccard`.

The adaptive attacker gets no feedback in training runs, so the training data
does not depend on any model.
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from fraudsim.detectors.runner import run_detection
from fraudsim.metrics.matching import jaccard
from fraudsim.scoring.features import FEATURE_NAMES
from fraudsim.scoring.scorer import RingScorer
from fraudsim.scoring.stage import ScoringStage
from fraudsim.simulator.config import parse_simulation_config
from fraudsim.simulator.scenario_loader import load_scenario
from fraudsim.types import config_hash, new_run_id

DEFAULT_SCENARIOS = ("baseline", "high_noise", "hub_heavy", "adversarial", "drift", "scale_sweep")


@dataclass(slots=True)
class Row:
    scenario: str
    seed: int
    run_id: str
    ring_id: str
    t_frac: float
    status: str
    label: int
    best_jaccard: float
    features: list[float]


def collect_rows(scenario: str, seed: int) -> tuple[list[Row], dict[str, Any]]:
    """One closed-loop run (no attacker feedback) with feature recording."""
    raw = load_scenario(scenario)
    duration = parse_simulation_config(raw).duration_seconds
    stage = ScoringStage(scorer=None, record=True)
    result = run_detection(raw, seed, None, attacker_feedback=False, scoring=stage)
    gen = result.generator
    start = gen.ts(0.0).timestamp()
    truth = [
        {u for _, action, u in ring.member_history if action == "join"}
        for ring in gen.ledger.rings.values()
    ]
    rows = []
    for r in stage.rows:
        members = set(r["members"])
        best = max((jaccard(members, gt) for gt in truth), default=0.0)
        rows.append(
            Row(
                scenario=scenario,
                seed=seed,
                run_id=result.manifest["run_id"],
                ring_id=r["ring_id"],
                t_frac=(r["t"] - start) / duration,
                status=r["status"],
                label=int(best >= 0.5),
                best_jaccard=round(best, 4),
                features=r["features"],
            )
        )
    info = {
        "scenario": scenario,
        "seed": seed,
        "run_id": result.manifest["run_id"],
        "config_hash": result.manifest["config_hash"],
        "stream_digest": result.manifest["stream_digest"],
        "rows": len(rows),
        "positives": sum(r.label for r in rows),
    }
    return rows, info


def _matrix(rows: list[Row]) -> tuple[np.ndarray, np.ndarray]:
    x = np.array([r.features for r in rows], dtype=float).reshape(-1, len(FEATURE_NAMES))
    y = np.array([r.label for r in rows], dtype=int)
    return x, y


def write_rows(path: Path, rows: list[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                *("scenario", "seed", "run_id", "ring_id", "t_frac", "status", "label"),
                "best_jaccard",
                *FEATURE_NAMES,
            ]
        )
        for r in rows:
            w.writerow(
                [r.scenario, r.seed, r.run_id, r.ring_id, round(r.t_frac, 6), r.status, r.label]
                + [r.best_jaccard]
                + [round(v, 6) for v in r.features]
            )


def train_scorer(
    model_type: str,
    train_seeds: list[int],
    test_seeds: list[int],
    scenarios: tuple[str, ...] = DEFAULT_SCENARIOS,
    fit_fraction: float = 0.7,
    out_model: Path = Path("models/ring_scorer_lr.joblib"),
    out_runs: Path = Path("runs/training"),
    log: Any = print,
) -> tuple[RingScorer, dict[str, Any]]:
    if set(train_seeds) & set(test_seeds):
        raise ValueError("train and test seeds must be disjoint")
    training_id = new_run_id()
    started = time.perf_counter()
    train_rows: list[Row] = []
    test_rows: list[Row] = []
    runs: list[dict[str, Any]] = []
    for scenario in scenarios:
        for seed in train_seeds:
            rows, info = collect_rows(scenario, seed)
            train_rows += rows
            runs.append(info | {"split": "train"})
            log(
                f"  train {scenario:12} seed {seed}: {info['rows']} rows, "
                f"{info['positives']} positive"
            )
        for seed in test_seeds:
            rows, info = collect_rows(scenario, seed)
            test_rows += rows
            runs.append(info | {"split": "test"})
            log(
                f"  test  {scenario:12} seed {seed}: {info['rows']} rows, "
                f"{info['positives']} positive"
            )

    fit = [r for r in train_rows if r.t_frac < fit_fraction]
    cal = [r for r in train_rows if r.t_frac >= fit_fraction]
    x_fit, y_fit = _matrix(fit)
    x_cal, y_cal = _matrix(cal)
    x_test, y_test = _matrix(test_rows)

    scorer = RingScorer(model_type).fit(x_fit, y_fit, x_cal, y_cal)
    metrics = {
        "fit_slice": scorer.evaluate(x_fit, y_fit),
        "calibration_slice": scorer.evaluate(x_cal, y_cal),
        "test": scorer.evaluate(x_test, y_test),
    }
    scorer.meta.metrics = metrics
    scorer.meta.reliability = scorer.reliability_table(x_test, y_test)
    scorer.meta.train = {
        "training_id": training_id,
        "scenarios": list(scenarios),
        "scenario_hashes": {s: config_hash(load_scenario(s)) for s in scenarios},
        "train_seeds": train_seeds,
        "test_seeds": test_seeds,
        "fit_fraction": fit_fraction,
        "label": "Jaccard(members, any ground-truth ring's all-time members) >= 0.5",
        "rows": {"fit": len(fit), "calibration": len(cal), "test": len(test_rows)},
        "runs": runs,
        "dataset": str(out_runs / training_id),
        "wall_s": round(time.perf_counter() - started, 2),
    }
    scorer.save(out_model)
    write_rows(out_runs / training_id / "train_rows.csv", train_rows)
    write_rows(out_runs / training_id / "test_rows.csv", test_rows)
    (out_runs / training_id / "summary.json").write_text(
        json.dumps({"model": str(out_model), **scorer.meta.train, "metrics": metrics}, indent=2),
        encoding="utf-8",
    )
    return scorer, metrics


def parse_seeds(spec: str) -> list[int]:
    """'101-105' or '101,103,107' -> list of ints."""
    out: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out
