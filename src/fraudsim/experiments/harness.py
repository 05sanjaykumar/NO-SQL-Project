"""
Headless experiment harness (Milestone 8).

Same code paths as the live system: EventGenerator -> DetectionEngine (+
ScoringStage) -> evaluate_run. For each (scenario, seed) the event stream is
generated once and every method processes the identical stream (paired design),
with attacker feedback off so that methods cannot change the stream they are
compared on. The adaptivity sweep turns feedback on explicitly.

Every result row carries run_id, scenario, scenario hash, seed, method, the
method's config hash and the stream digest, so each number in a table or figure
traces back to a run.
"""

from __future__ import annotations

import csv
import json
import platform
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fraudsim.detectors.engine import DetectionEngine, EngineConfig, build_detectors
from fraudsim.detectors.ensemble import EnsembleConfig, RingTracker
from fraudsim.detectors.runner import hub_degrees
from fraudsim.graph.store import InMemoryGraphStore
from fraudsim.metrics.evaluation import EvalConfig, evaluate_run
from fraudsim.scoring.early_warning import lead_times
from fraudsim.scoring.scorer import RingScorer
from fraudsim.scoring.stage import ScoringStage
from fraudsim.simulator.event_generator import EventGenerator
from fraudsim.simulator.scenario_loader import load_scenario
from fraudsim.types import Event, config_hash

DEFAULT_MODEL = Path(__file__).resolve().parents[3] / "models" / "ring_scorer_lr.joblib"

STATIC_ONLY = {
    "hub_aware": {"enabled": False},
    "temporal_cycle": {"enabled": False},
    "smurfing": {"enabled": False},
    "community": {"enabled": False},
}
PROPOSED = {"static_cypher": {"enabled": False}, "community": {"enabled": False}}


@dataclass
class Method:
    """One configuration of the pipeline under test."""

    name: str
    label: str
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    engine: dict[str, Any] = field(default_factory=dict)
    ensemble: dict[str, Any] = field(default_factory=dict)
    scored: bool = True
    family: str = "proposed"
    alert_threshold: float | None = None  # None = AlertPolicy default (0.5)

    def config(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "overrides": self.overrides,
            "engine": self.engine,
            "ensemble": self.ensemble,
            "scored": self.scored,
            "alert_threshold": self.alert_threshold,
        }


def _merge(base: dict[str, dict[str, Any]], extra: dict[str, dict[str, Any]]) -> dict:
    out = {k: dict(v) for k, v in base.items()}
    for k, v in extra.items():
        out.setdefault(k, {}).update(v)
    return out


def baseline_methods() -> list[Method]:
    community_only = {
        k: {"enabled": False} for k in ("static_cypher", "hub_aware", "temporal_cycle", "smurfing")
    }
    return [
        Method(
            "static_cap100",
            "Static Cypher (cap 100)",
            _merge(STATIC_ONLY, {"static_cypher": {"max_degree_cap": 100}}),
            scored=False,
            family="baseline",
        ),
        Method(
            "static_cap10",
            "Static Cypher (cap 10)",
            _merge(STATIC_ONLY, {"static_cypher": {"max_degree_cap": 10}}),
            scored=False,
            family="baseline",
        ),
        Method(
            "louvain",
            "Louvain communities",
            _merge(community_only, {"community": {"algorithm": "louvain"}}),
            scored=False,
            family="baseline",
        ),
        Method(
            "plain_cc",
            "Connected components",
            _merge(community_only, {"community": {"algorithm": "wcc", "min_density": 0.0}}),
            scored=False,
            family="baseline",
        ),
        Method("proposed", "Hub-aware incremental (proposed)", dict(PROPOSED), family="proposed"),
    ]


def ablation_methods() -> list[Method]:
    return [
        Method("proposed", "Full system", dict(PROPOSED), family="ablation"),
        Method(
            "no_hub_weight",
            "w/o hub weighting",
            _merge(PROPOSED, {"hub_aware": {"weight_method": "none"}}),
            family="ablation",
        ),
        Method(
            "no_time_decay",
            "w/o time decay",
            _merge(PROPOSED, {"hub_aware": {"time_decay_half_life_s": None}}),
            family="ablation",
        ),
        Method(
            "no_incremental",
            "w/o incremental scheduling",
            dict(PROPOSED),
            engine={"incremental": False},
            family="ablation",
        ),
        Method("no_scoring", "w/o risk model", dict(PROPOSED), scored=False, family="ablation"),
        Method(
            "no_hysteresis",
            "w/o hysteresis",
            dict(PROPOSED),
            ensemble={"hysteresis_confirm": 1, "hysteresis_dissolve": 1},
            family="ablation",
        ),
        Method(
            "no_hub_aware",
            "w/o hub-aware detector",
            _merge(PROPOSED, {"hub_aware": {"enabled": False}}),
            family="ablation",
        ),
        Method(
            "no_cycle",
            "w/o cycle detector",
            _merge(PROPOSED, {"temporal_cycle": {"enabled": False}}),
            family="ablation",
        ),
        Method(
            "no_smurfing",
            "w/o smurfing detector",
            _merge(PROPOSED, {"smurfing": {"enabled": False}}),
            family="ablation",
        ),
    ]


@dataclass
class Stream:
    scenario: str
    seed: int
    raw: dict[str, Any]
    generator: EventGenerator
    events: list[Event]


def make_stream(
    scenario: str | dict[str, Any], seed: int, overrides: dict[str, Any] | None = None
) -> Stream:
    raw = load_scenario(scenario) if isinstance(scenario, str) else scenario
    raw = json.loads(json.dumps(raw))  # deep copy
    for key, value in (overrides or {}).items():
        _set_path(raw, key, value)
    gen = EventGenerator(seed=seed, run_id=f"s{seed}", config=raw)
    events = gen.generate()
    return Stream(str(raw.get("name")), seed, raw, gen, events)


def _set_path(cfg: dict[str, Any], dotted: str, value: Any) -> None:
    node = cfg
    parts = dotted.split(".")
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def run_method(
    stream: Stream,
    method: Method,
    scorer: RingScorer | None,
    experiment: str,
    batch_size: int = 50,
    eval_cfg: EvalConfig | None = None,
    detect_window: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Process one stream with one method and return a flat result row."""
    store = InMemoryGraphStore()
    # Full-graph baselines run every 5 simulated seconds (rather than the live default
    # of 30 s) so their two-hit confirmation is not held back by the timer.
    engine_cfg = EngineConfig(batch_size=batch_size, **({"full_interval_s": 5.0} | method.engine))
    tracker = RingTracker(EnsembleConfig(**method.ensemble))
    stage = ScoringStage(scorer=scorer) if (method.scored and scorer is not None) else None
    if stage is not None and method.alert_threshold is not None:
        stage.policy.default_threshold = method.alert_threshold
    engine = DetectionEngine(
        store,
        build_detectors(store, method.overrides),
        tracker=tracker,
        config=engine_cfg,
        scorer=stage,
    )
    start = time.perf_counter()
    engine.run(stream.events)
    wall = time.perf_counter() - start
    gen = stream.generator
    start_t = gen.ts(0.0).timestamp()
    end_t = gen.ts().timestamp()
    metrics = evaluate_run(
        gen.ledger,
        engine.tracker,
        engine.reports,
        start_t,
        end_t,
        stage is not None,
        cfg=eval_cfg,
        hub_degree_of=hub_degrees(gen),
    )
    s = metrics.summary
    leads = [x["lead_time_s"] for x in lead_times(gen.ledger, engine.tracker)] if stage else []
    det = s["latency_ms"]["detect_per_batch"]
    full_ms = [
        sum(
            v
            for k, v in r.detector_ms.items()
            if k in ("StaticCypherBaseline", "CommunityDetector")
        )
        + r.timings_ms.get("full_view", 0.0)
        for r in engine.reports
        if r.full_run
    ]
    method_hash = config_hash(method.config())
    run_id = f"{experiment}:{stream.scenario}:{stream.seed}:{method.name}"
    by_evasion: dict[str, list[bool]] = {}
    for row in metrics.rings:
        by_evasion.setdefault(row["evasion"], []).append(bool(row["detected"]))
    return {
        "experiment": experiment,
        "run_id": run_id,
        "scenario": stream.scenario,
        "scenario_hash": config_hash(stream.raw),
        "seed": stream.seed,
        "method": method.name,
        "method_label": method.label,
        "family": method.family,
        "method_hash": method_hash,
        "stream_digest": gen.digest[:16],
        "events": len(stream.events),
        "true_rings": s["true_rings"],
        "rings_flagged": s["rings_flagged"],
        "rings_detected": s["rings_detected"],
        "false_rings": s["false_positive_rings"],
        "precision": s["ring"]["precision"],
        "recall": s["ring"]["recall"],
        "f1": s["ring"]["f1"],
        "user_precision": s["user"]["precision"],
        "user_recall": s["user"]["recall"],
        "user_f1": s["user"]["f1"],
        "ttd_p50_s": s["time_to_detect_s"]["p50"],
        "ttd_p90_s": s["time_to_detect_s"]["p90"],
        "detect_ms_p50": det["p50"],
        "detect_ms_p95": det["p95"],
        "detect_ms_p99": det["p99"],
        "full_graph_ms_p50": _p50(full_ms),
        "ingest_ms_per_event_p50": s["latency_ms"]["ingest_per_event"]["p50"],
        "events_per_s": s["throughput"]["events_per_s"],
        "wall_s": round(wall, 3),
        "precision_at_10": s["triage"].get("precision_at_10"),
        "pr_auc": s["triage"].get("pr_auc"),
        "alerts_per_hour": s["triage"].get("alerts_per_hour"),
        "early_warnings_before_activation": sum(1 for x in leads if x is not None and x > 0),
        **{f"miss_{k}": v for k, v in s["missed_reasons"].items()},
        "recall_by_evasion": json.dumps(
            {k: round(sum(v) / len(v), 4) for k, v in sorted(by_evasion.items())}
        ),
        "detected_by_evasion": json.dumps(
            {k: [sum(v), len(v)] for k, v in sorted(by_evasion.items())}
        ),
    }


def run_grid(
    experiment: str,
    scenarios: Iterable[str],
    seeds: Iterable[int],
    methods: list[Method],
    out_dir: Path,
    scenario_overrides: dict[str, Any] | None = None,
    model_path: Path = DEFAULT_MODEL,
    log: Callable[[str], None] = print,
    extra_columns: dict[str, Any] | None = None,
    eval_cfg: EvalConfig | None = None,
) -> Path:
    """Run every method on every (scenario, seed) stream; append rows to results.csv."""
    out_dir = Path(out_dir) / experiment
    out_dir.mkdir(parents=True, exist_ok=True)
    scorer = RingScorer.load(model_path) if Path(model_path).exists() else None
    path = out_dir / "results.csv"
    rows_written = 0
    t0 = time.perf_counter()
    for scenario in scenarios:
        for seed in seeds:
            stream = make_stream(scenario, seed, scenario_overrides)
            for method in methods:
                row = run_method(stream, method, scorer, experiment, eval_cfg=eval_cfg)
                row.update(extra_columns or {})
                _append(path, row)
                rows_written += 1
                log(
                    f"[{experiment}] {stream.scenario} seed={seed} {method.name:16s} "
                    f"P={_f(row['precision'])} R={_f(row['recall'])} F1={_f(row['f1'])} "
                    f"det_p50={_f(row['detect_ms_p50'], 1)}ms ({time.perf_counter() - t0:.0f}s)"
                )
    _write_manifest(
        out_dir,
        experiment,
        list(scenarios) if not isinstance(scenarios, list) else scenarios,
        list(seeds),
        methods,
        scenario_overrides,
        model_path,
        rows_written,
    )
    return path


def _p50(xs: list[float]) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    return round(ys[len(ys) // 2], 3)


def _f(x: Any, d: int = 2) -> str:
    return "-" if x is None else f"{x:.{d}f}"


def _append(path: Path, row: dict[str, Any]) -> None:
    exists = path.exists()
    if exists:
        with open(path, encoding="utf-8") as f:
            header = next(csv.reader(f))
        missing = [k for k in row if k not in header]
        if missing:
            raise ValueError(f"{path}: new columns {missing}; use a fresh experiment directory")
    else:
        header = list(row)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        if not exists:
            w.writeheader()
        w.writerow(row)


def _write_manifest(
    out_dir: Path,
    experiment: str,
    scenarios: list[str],
    seeds: list[int],
    methods: list[Method],
    overrides: dict[str, Any] | None,
    model_path: Path,
    rows: int,
) -> None:
    from fraudsim.logging.replay import file_sha256

    manifest_path = out_dir / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"grids": []}
    )
    manifest["grids"].append(
        {
            "experiment": experiment,
            "scenarios": scenarios,
            "scenario_hashes": {
                s: config_hash(load_scenario(s)) for s in scenarios if isinstance(s, str)
            },
            "seeds": seeds,
            "methods": [
                m.config() | {"label": m.label, "hash": config_hash(m.config())} for m in methods
            ],
            "scenario_overrides": overrides or {},
            "model": {"path": str(model_path), "sha256": file_sha256(model_path)}
            if Path(model_path).exists()
            else None,
            "rows": rows,
            "python": platform.python_version(),
        }
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
