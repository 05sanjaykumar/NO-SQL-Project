"""
Command Line Interface (CLI) for fraudsim.
"""

from __future__ import annotations

import json

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
@click.option(
    "--model",
    "model_path",
    default="models/ring_scorer_lr.joblib",
    help="Risk model for alerts and explanations ('none' to run without one)",
)
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
    model_path: str,
) -> None:
    """Simulate a scenario and run incremental detection on it (closed loop)."""
    import json
    from pathlib import Path

    from fraudsim.detectors.engine import EngineConfig
    from fraudsim.detectors.runner import run_detection

    scoring = None
    if model_path != "none" and Path(model_path).exists():
        from fraudsim.scoring.scorer import RingScorer
        from fraudsim.scoring.stage import ScoringStage

        scoring = ScoringStage(scorer=RingScorer.load(Path(model_path)))
    elif model_path != "none":
        click.echo(f"note: {model_path} not found; running without a risk model", err=True)

    result = run_detection(
        scenario,
        seed,
        Path(out),
        backend=backend,
        engine_config=EngineConfig(batch_size=batch_size, k_hop=k_hop, incremental=not full),
        detector_overrides={d: {"enabled": False} for d in disable},
        attacker_feedback=not no_feedback,
        duration_s=duration,
        scoring=scoring,
    )
    m = result.manifest
    keys = (
        "run_id",
        "scenario",
        "seed",
        "backend",
        "config_hash",
        "events",
        "detection",
        "metrics",
    )
    click.echo(json.dumps({k: m[k] for k in keys} | {"run_dir": m.get("run_dir")}, indent=2))


@main.command()
@click.argument("run_dir")
@click.option("--model", "model_path", default=None, help="Override the model path")
def replay(run_dir: str, model_path: str | None) -> None:
    """Re-run detection on a logged run and check the results are identical."""
    import json
    import sys

    from fraudsim.logging.audit import AuditTrail
    from fraudsim.logging.replay import replay_run

    result = replay_run(run_dir, model_path)
    ok, problem = AuditTrail(run_dir, result.run_id).verify()
    click.echo(
        json.dumps(result.to_dict() | {"audit_chain_ok": ok, "audit_problem": problem}, indent=2)
    )
    sys.exit(0 if result.identical and ok else 1)


@main.command("audit-verify")
@click.argument("run_dir")
def audit_verify(run_dir: str) -> None:
    """Verify the hash chain of a run's audit trail."""
    import json
    import sys
    from pathlib import Path

    from fraudsim.logging.audit import AuditTrail

    manifest = json.loads((Path(run_dir) / "manifest.json").read_text(encoding="utf-8"))
    trail = AuditTrail(run_dir, manifest["run_id"])
    ok, problem = trail.verify()
    click.echo(json.dumps({"entries": len(trail.read_entries()), "ok": ok, "problem": problem}))
    sys.exit(0 if ok else 1)


@main.command("compare-latency")
@click.option("--scenario", default="scale_sweep")
@click.option("--seed", default=42)
@click.option("--backend", default="memory", type=click.Choice(["memory", "neo4j"]))
@click.option("--duration", default=None, type=float)
@click.option("--out", default=None, help="Write the comparison JSON here")
def compare_latency(
    scenario: str, seed: int, backend: str, duration: float | None, out: str | None
) -> None:
    """Incremental vs full recomputation on the same stream (latency + accuracy)."""
    import json
    from pathlib import Path

    from fraudsim.detectors.runner import compare_modes

    result = compare_modes(scenario, seed, backend=backend, duration_s=duration)
    text = json.dumps(result, indent=2)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    click.echo(text)


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
@click.option(
    "--suite",
    default=None,
    help="Paper experiment: tune | baselines | ablations | frontier | scale | hub_size | "
    "sweeps | adaptivity | all (writes results/<experiment>/results.csv)",
)
@click.option("--scenario", default="baseline", help="Scenario name (simulation-only run)")
@click.option("--seeds", default=5, help="Number of test seeds (1001, 1002, ...)")
@click.option("--output-dir", default="results/", help="Results directory")
def experiment(suite: str | None, scenario: str, seeds: int, output_dir: str) -> None:
    """Run a paper experiment suite, or a simulation-only run of one scenario."""
    import sys

    if suite:
        from pathlib import Path

        from fraudsim.experiments.suite import run_suite

        click.echo(run_suite(suite, Path(output_dir), seeds, log=click.echo))
        return

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
@click.option("--dataset", type=click.Choice(["amlworld", "elliptic", "paysim"]), required=True)
@click.option("--path", default=None, help="Dataset folder (default data/raw/<dataset>)")
@click.option("--max-rows", default=None, type=int, help="Read only the first N rows")
@click.option("--window-s", default=900.0, help="Rolling transaction window of the views")
@click.option("--start-day", default=0.0, help="AMLworld: slice start, days from the first date")
@click.option("--days", default=None, type=float, help="AMLworld: slice length in days")
@click.option("--output-dir", default="results/", help="Appends to <output-dir>/public/results.csv")
def public(
    dataset: str,
    path: str | None,
    max_rows: int | None,
    window_s: float,
    start_day: float,
    days: float | None,
    output_dir: str,
) -> None:
    """Replay a public dataset through the transaction detectors and score it."""
    from pathlib import Path

    from fraudsim.datasets import load_dataset, run_public
    from fraudsim.experiments.harness import _append

    kw = {"max_rows": max_rows} if dataset != "elliptic" else {}
    if dataset == "amlworld":
        kw |= {"start_day": start_day, "days": days}
    ds = load_dataset(dataset, path or f"data/raw/{dataset}", **kw)
    row = run_public(ds, window_s=window_s)
    row = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in row.items()}
    row["window_s"] = window_s
    out = Path(output_dir) / "public"
    out.mkdir(parents=True, exist_ok=True)
    _append(out / "results.csv", row)
    click.echo(json.dumps(row, indent=2))


@main.command()
def seed() -> None:
    """Seed the Neo4j database using the legacy seed dataset."""
    import subprocess
    import sys

    click.echo("Running seed.py...")
    subprocess.run([sys.executable, "seed.py"], check=True)


if __name__ == "__main__":
    main()
