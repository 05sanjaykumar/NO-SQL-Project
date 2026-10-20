"""
Command Line Interface (CLI) for fraudsim.
"""

from __future__ import annotations

import click
import uvicorn


@click.group()
@click.version_option(version="0.1.0")
def main() -> None:
    """FraudSim: Hub-Aware Incremental Fraud Ring Detection System."""
    pass


@main.command()
@click.option("--host", default="0.0.0.0", help="API host")
@click.option("--port", default=8000, help="API port")
@click.option("--reload/--no-reload", default=False, help="Enable auto-reload")
def api(host: str, port: int, reload: bool) -> None:
    """Launch the FastAPI + WebSocket backend server."""
    click.echo(f"Starting FraudSim API on http://{host}:{port}")
    uvicorn.run("fraudsim.api.main:app", host=host, port=port, reload=reload)


@main.command()
@click.option("--scenario", default="baseline", help="Scenario configuration name")
@click.option("--seed", default=42, help="RNG seed")
@click.option("--speed", default=1.0, help="Simulation speed multiplier")
def run(scenario: str, seed: int, speed: float) -> None:
    """Run a simulation interactively."""
    click.echo(f"Running simulation scenario={scenario}, seed={seed}, speed={speed}x")


@main.command()
@click.option("--scenario", default="baseline", help="Scenario name")
@click.option("--seeds", default=5, help="Number of random seeds")
@click.option("--output-dir", default="results/", help="Results directory")
def experiment(scenario: str, seeds: int, output_dir: str) -> None:
    """Run a headless experiment across multiple seeds."""
    import sys

    from fraudsim.experiments.runner import main as run_exp

    sys.argv = [
        "fraudsim-exp",
        "--scenario",
        scenario,
        "--seeds",
        str(seeds),
        "--output-dir",
        output_dir,
    ]
    run_exp()


@main.command()
def seed() -> None:
    """Seed the Neo4j database using the legacy seed dataset."""
    import subprocess
    import sys

    click.echo("Running seed.py...")
    subprocess.run([sys.executable, "seed.py"], check=True)


if __name__ == "__main__":
    main()
