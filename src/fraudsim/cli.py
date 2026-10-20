"""
Command Line Interface (CLI) for fraudsim.
"""

from __future__ import annotations

import click
import uvicorn


@click.group()
@click.version_option(version="0.1.0")
@click.option("--log-level", default="INFO", help="Console log level (DEBUG shows ring updates)")
def main(log_level: str) -> None:
    """FraudSim: Hub-Aware Incremental Fraud Ring Detection System."""
    import logging

    import structlog

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        )
    )


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
@click.option("--rate", default=None, type=float, help="Benign events per simulated second")
@click.option("--duration", default=None, type=float, help="Override duration (sim seconds)")
def run(scenario: str, seed: int, speed: float, rate: float | None, duration: float | None) -> None:
    """Stream a scenario in real time (speed-scaled), printing each event as JSON."""
    import asyncio
    import json

    from fraudsim.simulator.event_generator import EventGenerator
    from fraudsim.simulator.player import StreamPlayer
    from fraudsim.simulator.scenario_loader import load_scenario
    from fraudsim.stream.event_bus import AsyncQueueBus
    from fraudsim.types import Event, new_run_id

    gen = EventGenerator(seed=seed, run_id=new_run_id(), config=load_scenario(scenario))
    if rate is not None:
        gen.set_rate(rate)

    def show(event: Event) -> None:
        click.echo(json.dumps(event.to_dict(), separators=(",", ":")))

    async def _play() -> None:
        bus = AsyncQueueBus(maxsize=0)
        player = StreamPlayer(gen, bus, speed=speed, until_s=duration, on_event=show)
        stats = await player.run()
        click.echo(json.dumps({"player": stats.to_dict(), "digest": gen.digest}), err=True)

    asyncio.run(_play())


@main.command()
@click.option("--scenario", default="baseline", help="Scenario configuration name")
@click.option("--seed", default=42, help="RNG seed")
@click.option("--duration", default=None, type=float, help="Override duration (sim seconds)")
@click.option("--out", default="runs/", help="Directory for run artefacts")
def simulate(scenario: str, seed: int, duration: float | None, out: str) -> None:
    """Run the simulator headless and write events + ground truth to runs/<run_id>/."""
    import json
    from pathlib import Path

    from fraudsim.simulator.recorder import run_simulation

    manifest = run_simulation(scenario, seed, Path(out), duration_s=duration)
    keys = (
        "run_id",
        "scenario",
        "seed",
        "config_hash",
        "sim_time_s",
        "events",
        "events_by_type",
        "rings",
        "stream_digest",
        "generation_events_per_s",
        "run_dir",
    )
    click.echo(json.dumps({k: manifest[k] for k in keys}, indent=2))


@main.command()
@click.option("--scenario", default="baseline", help="Scenario configuration name")
@click.option("--seed", default=42, help="RNG seed")
@click.option("--backend", default="memory", type=click.Choice(["memory", "neo4j"]))
@click.option("--batch-size", default=50, help="Events per micro-batch")
@click.option("--k-hop", default=2, help="Dirty-set expansion radius (user hops)")
@click.option("--full", is_flag=True, help="Ablation: full recomputation every batch")
@click.option("--disable", multiple=True, help="Detector type to switch off (repeatable)")
@click.option("--no-feedback", is_flag=True, help="Do not tell the adaptive attacker about flags")
@click.option("--duration", default=None, type=float, help="Override duration (sim seconds)")
@click.option("--out", default="runs/", help="Directory for run artefacts")
def detect(
    scenario: str,
    seed: int,
    backend: str,
    batch_size: int,
    k_hop: int,
    full: bool,
    disable: tuple[str, ...],
    no_feedback: bool,
    duration: float | None,
    out: str,
) -> None:
    """Simulate a scenario and run incremental detection on it (closed loop)."""
    import json
    from pathlib import Path

    from fraudsim.detectors.engine import EngineConfig
    from fraudsim.detectors.runner import run_detection

    result = run_detection(
        scenario,
        seed,
        Path(out),
        backend=backend,
        engine_config=EngineConfig(batch_size=batch_size, k_hop=k_hop, incremental=not full),
        detector_overrides={d: {"enabled": False} for d in disable},
        attacker_feedback=not no_feedback,
        duration_s=duration,
    )
    m = result.manifest
    keys = ("run_id", "scenario", "seed", "backend", "config_hash", "events", "detection")
    click.echo(json.dumps({k: m[k] for k in keys} | {"run_dir": m.get("run_dir")}, indent=2))


@main.command("demo-story")
@click.option("--seed", default=7, help="Demo seed (not used for training or testing the model)")
@click.option("--out", default="demo/", help="Output directory for story.html")
@click.option("--model", "model_path", default=None, help="Trained scorer (.joblib)")
@click.option("--open/--no-open", "open_browser", default=False, help="Open the page when done")
def demo_story(seed: int, out: str, model_path: str | None, open_browser: bool) -> None:
    """Hidden-ring demo: degree-capped rules vs the hub-aware system, as one HTML page."""
    import webbrowser
    from pathlib import Path

    from fraudsim.demo.story import DEFAULT_MODEL, run_story

    page = run_story(seed=seed, out_dir=Path(out), model_path=Path(model_path or DEFAULT_MODEL))
    click.echo(f"Wrote {page.resolve()}")
    if open_browser:
        webbrowser.open(page.resolve().as_uri())


@main.command("train-scorer")
@click.option(
    "--model",
    "model_type",
    default="logistic_regression",
    type=click.Choice(["logistic_regression", "gradient_boosting"]),
)
@click.option("--train-seeds", default="101-105", help="e.g. 101-105 or 101,103")
@click.option("--test-seeds", default="201-203", help="Disjoint from --train-seeds")
@click.option("--scenarios", default="", help="Comma-separated; default: all six")
@click.option("--out", default=None, help="Model path (default models/ring_scorer_<lr|gb>.joblib)")
def train_scorer_cmd(
    model_type: str, train_seeds: str, test_seeds: str, scenarios: str, out: str | None
) -> None:
    """Train + calibrate the ring risk model on simulator ground truth."""
    import json
    from pathlib import Path

    from fraudsim.scoring.train import DEFAULT_SCENARIOS, parse_seeds, train_scorer

    short = "lr" if model_type == "logistic_regression" else "gb"
    path = Path(out or f"models/ring_scorer_{short}.joblib")
    names = tuple(s for s in scenarios.split(",") if s) or DEFAULT_SCENARIOS
    _scorer, metrics = train_scorer(
        model_type,
        parse_seeds(train_seeds),
        parse_seeds(test_seeds),
        names,
        out_model=path,
        log=click.echo,
    )
    click.echo(json.dumps({"model": str(path), "metrics": metrics}, indent=2))


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
