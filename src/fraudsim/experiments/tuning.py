"""
Hyperparameter selection on validation seeds (never on test seeds).

Grid: alert threshold x hub-aware edge threshold. The configuration with the
best mean ring-level F1 over the validation runs is written to
configs/tuned.json together with the validation seeds, scenarios and the
results file, so every reported experiment can name where its settings came from.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from fraudsim.experiments.harness import PROPOSED, Method, _merge, run_grid

# 0.7, then 0.8 and 0.9, were added while the best value sat on the upper edge.
ALERT_THRESHOLDS = (0.2, 0.3, 0.5, 0.7, 0.8, 0.9)
# 0.3 and then 0.2 were added after the best value sat on the lower edge of the grid.
EDGE_THRESHOLDS = (0.2, 0.3, 0.4, 0.5, 0.6)
# The accuracy scenarios; scale_sweep is a latency benchmark (experiment E4).
SCENARIOS = ("baseline", "high_noise", "hub_heavy", "adversarial", "drift")
VALIDATION_SEEDS = (301, 302, 303)


def candidate_methods() -> list[Method]:
    out = []
    for at in ALERT_THRESHOLDS:
        for et in EDGE_THRESHOLDS:
            out.append(
                Method(
                    f"tune_a{at}_e{et}",
                    f"alert {at}, edge {et}",
                    _merge(PROPOSED, {"hub_aware": {"edge_threshold": et}}),
                    family="tuning",
                    alert_threshold=at,
                )
            )
    return out


def select(results_csv: Path) -> dict[str, Any]:
    by_method: dict[str, list[float]] = defaultdict(list)
    with open(results_csv, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["scenario"] in SCENARIOS and int(row["seed"]) in VALIDATION_SEEDS:
                by_method[row["method"]].append(float(row["f1"]) if row["f1"] else 0.0)
    expected = len(SCENARIOS) * len(VALIDATION_SEEDS)
    incomplete = {m: len(v) for m, v in by_method.items() if len(v) != expected}
    if incomplete or len(by_method) != len(ALERT_THRESHOLDS) * len(EDGE_THRESHOLDS):
        raise RuntimeError(f"tuning grid incomplete (expected {expected} runs each): {incomplete}")
    scores = {m: round(mean(v), 4) for m, v in by_method.items()}
    best = max(scores, key=lambda m: (scores[m], m))
    at = float(best.split("_a")[1].split("_e")[0])
    et = float(best.split("_e")[1])
    return {"alert_threshold": at, "edge_threshold": et, "mean_f1": scores[best], "grid": scores}


def tune(out_dir: Path, log: Any = print, select_only: bool = False) -> dict[str, Any]:
    """Run the grid (or, with select_only, reuse an existing complete one) and select."""
    if select_only:
        path = Path(out_dir) / "tuning" / "results.csv"
    else:
        path = run_grid(
            "tuning", list(SCENARIOS), list(VALIDATION_SEEDS), candidate_methods(), out_dir, log=log
        )
    chosen = select(path)
    record = {
        **chosen,
        "selected_by": "max mean ring-level F1 on validation seeds",
        "validation_seeds": list(VALIDATION_SEEDS),
        "scenarios": list(SCENARIOS),
        "results": str(path),
    }
    target = Path(__file__).resolve().parents[3] / "configs" / "tuned.json"
    target.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def load_tuned() -> dict[str, Any] | None:
    target = Path(__file__).resolve().parents[3] / "configs" / "tuned.json"
    return json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
