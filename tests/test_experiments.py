"""
Unit tests for the experiment harness.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fraudsim.experiments.ablations import ABLATION_VARIANTS
from fraudsim.experiments.baselines import BASELINES
from fraudsim.experiments.latex_export import generate_baseline_table
from fraudsim.experiments.plotting import plot_pr_frontier
from fraudsim.experiments.runner import run_single_experiment
from fraudsim.simulator.scenario_loader import load_scenario


def test_baselines_registry():
    assert "static_cypher_cap100" in BASELINES
    assert "static_cypher_cap10" in BASELINES
    assert "community_louvain" in BASELINES
    assert "plain_cc" in BASELINES


def test_ablations_registry():
    assert "full_system" in ABLATION_VARIANTS
    assert "no_hub_weight" in ABLATION_VARIANTS
    assert "no_time_decay" in ABLATION_VARIANTS
    assert "no_incremental" in ABLATION_VARIANTS


def test_experiment_run(tmp_path: Path):
    cfg = load_scenario("baseline")
    res = run_single_experiment("baseline", 42, tmp_path, cfg)
    assert res["scenario"] == "baseline"
    assert res["seed"] == 42
    assert res["events_processed"] > 0
    assert res["rings_ground_truth"] == 4
    assert (tmp_path / f"{res['run_id']}.json").exists()
    assert (tmp_path / "runs" / res["run_id"] / "events.jsonl").exists()
    # No detector has run yet, so no detection numbers may be reported.
    for key in ("precision", "recall", "f1", "latency_p50_ms"):
        assert res[key] is None


def test_figures_and_tables_skip_without_results(tmp_path: Path):
    # No results -> nothing is written (no placeholder numbers).
    assert plot_pr_frontier(tmp_path, tmp_path / "none") is None
    assert generate_baseline_table(tmp_path, tmp_path / "none") is None
    assert not list(tmp_path.glob("*.pdf")) and not list(tmp_path.glob("*.tex"))


def test_figures_and_tables_from_real_runs(tmp_path: Path):
    from fraudsim.experiments.harness import baseline_methods, run_grid
    from fraudsim.experiments.latex_export import generate_numbers
    from fraudsim.experiments.plotting import plot_evasion

    results = tmp_path / "results"
    methods = [m for m in baseline_methods() if m.name in ("static_cap10", "proposed")]
    csv = run_grid("e1_baselines", ["baseline"], [1001], methods, results, log=lambda _: None)
    df = pd.read_csv(csv)
    assert set(df["method"]) == {"static_cap10", "proposed"}

    out = tmp_path / "paper"
    assert plot_evasion(out, results)
    assert (out / "fig5_evasion.pdf").exists()
    tab = generate_baseline_table(out, results)
    text = tab.read_text(encoding="utf-8")
    assert "sha256" in text and "Static" in text
    # The table's F1 for the proposed method is exactly the run's F1.
    f1 = df.set_index("method").loc["proposed", "f1"]
    assert f"{(0.0 if pd.isna(f1) else f1):.2f}" in text
    nums = generate_numbers(out, results).read_text(encoding="utf-8")
    assert r"\EPropF" in nums
