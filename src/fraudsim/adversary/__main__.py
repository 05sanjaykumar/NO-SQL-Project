"""
Command line for the adversarial search.

    python -m fraudsim.adversary --rounds 40 --strategist evolutionary
    python -m fraudsim.adversary --strategist random --rounds 40   # control arm
    python -m fraudsim.adversary --strategist llm --rounds 20      # needs ANTHROPIC_API_KEY
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from fraudsim.adversary.search import search


@click.command()
@click.option("--strategist", default="evolutionary",
              type=click.Choice(["random", "evolutionary", "llm"]),
              help="How the next attack is chosen")
@click.option("--rounds", default=40, help="How many attacks to try")
@click.option("--seeds", default="42,43,44", help="Comma-separated seeds; evasion must hold on all")
@click.option("--scenario", "base_scenario", default="adversarial", help="World to attack inside")
@click.option("--duration", "duration_s", default=120.0, help="Simulated seconds per trial")
@click.option("--backend", default="memory", type=click.Choice(["memory", "neo4j"]))
@click.option("--model", "model_path", default="models/ring_scorer_lr.joblib",
              help="Risk model to load, or 'none'")
@click.option("--rng-seed", default=7, help="Seed for the strategist itself")
@click.option("--out", default="results/adversary/report.json", help="Where to write the report")
@click.option("--quiet", is_flag=True, help="Only print the summary")
def main(
    strategist: str,
    rounds: int,
    seeds: str,
    base_scenario: str,
    duration_s: float,
    backend: str,
    model_path: str,
    rng_seed: int,
    out: str,
    quiet: bool,
) -> None:
    """Search for ring behaviours the detector fails to catch."""
    seed_list = tuple(int(s) for s in seeds.split(",") if s.strip())
    model = None if model_path == "none" else Path(model_path)
    if model and not model.exists():
        click.echo(f"note: {model} not found; running without a risk model", err=True)
        model = None

    def progress(i: int, result) -> None:  # noqa: ANN001 - TrialResult
        if quiet:
            return
        mark = click.style("EVADED", fg="red") if result.evaded else click.style("caught", fg="green")
        click.echo(f"  {i:>3}/{rounds}  {mark}  recall={result.ring_recall:.2f}  {result.plan.summary()}")

    report = search(
        strategist=strategist,
        rounds=rounds,
        seeds=seed_list,
        base_scenario=base_scenario,
        duration_s=duration_s,
        backend=backend,
        model_path=model,
        rng_seed=rng_seed,
        on_round=progress,
    )

    path = report.write(Path(out))
    click.echo()
    click.echo(json.dumps(report.summary(), indent=2))
    click.echo(f"\nreport: {path}")


if __name__ == "__main__":
    main()
