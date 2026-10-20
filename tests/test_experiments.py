"""
Unit tests for the experiment harness.
"""

from __future__ import annotations

from pathlib import Path

from fraudsim.experiments.ablations import ABLATION_VARIANTS
from fraudsim.experiments.baselines import BASELINES
from fraudsim.experiments.latex_export import generate_ablation_table, generate_baseline_table
from fraudsim.experiments.plotting import plot_latency_scaling, plot_pr_frontier
from fraudsim.experiments.runner import run_single_experiment


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
    cfg = {"name": "test_scenario", "version": "1.0"}
    res = run_single_experiment("test_scenario", 42, tmp_path, cfg)
    assert res["scenario"] == "test_scenario"
    assert res["seed"] == 42
    assert "precision" in res
    assert "recall" in res
    assert "latency_p50_ms" in res
    assert (tmp_path / f"{res['run_id']}.json").exists()


def test_plotting_and_latex(tmp_path: Path):
    plot_pr_frontier(tmp_path)
    plot_latency_scaling(tmp_path)
    assert (tmp_path / "fig1_pr_frontier.pdf").exists()
    assert (tmp_path / "fig2_latency_scaling.pdf").exists()

    generate_baseline_table(tmp_path)
    generate_ablation_table(tmp_path)
    assert (tmp_path / "tab1_baselines.tex").exists()
    assert (tmp_path / "tab2_ablation.tex").exists()
