"""
The experiment suite behind the paper (Milestone 8).

Each experiment writes results/<name>/results.csv (+ manifest.json). Seeds
1001+ are test seeds: never used for training (101-110, 201-205) or threshold
selection (301-303). The proposed method uses the thresholds selected on
validation seeds (configs/tuned.json); `proposed_default` keeps the a-priori 0.5.

  E1 baselines   RQ1  all scenarios, every baseline vs proposed
  E2 ablations   RQ5  remove one component at a time
  E3 frontier    RQ1  degree cap sweep vs hub-weighting sweep, shared-attribute rings only
  E4 scale       RQ2  incremental vs full recomputation as the graph grows
  E5 hub size    RQ3  how busy a hub a ring can hide behind
  E6 sweeps      RQ2/3  event rate, ring size, window length
  E7 adaptivity  RQ3  adaptive attacker with and without detection feedback
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fraudsim.experiments.harness import (
    PROPOSED,
    STATIC_ONLY,
    Method,
    _append,
    _merge,
    ablation_methods,
    baseline_methods,
    make_stream,
    run_grid,
)
from fraudsim.experiments.tuning import load_tuned
from fraudsim.metrics.evaluation import EvalConfig
from fraudsim.scoring.scorer import RingScorer

# Accuracy scenarios; scale_sweep is the latency benchmark of E4.
ALL_SCENARIOS = ["baseline", "high_noise", "hub_heavy", "adversarial", "drift"]
SHARED_TYPES = ("shared_device", "shared_card", "shared_ip", "mixed_attribute")
Log = Callable[[str], None]


def test_seeds(n: int) -> list[int]:
    return list(range(1001, 1001 + n))


def tuned_proposed(
    name: str = "proposed", label: str = "Hub-aware incremental (proposed)"
) -> Method:
    tuned = load_tuned()
    if tuned is None:
        raise RuntimeError(
            "configs/tuned.json missing: run `fraudsim experiment --suite tune` first"
        )
    return Method(
        name,
        label,
        _merge(PROPOSED, {"hub_aware": {"edge_threshold": tuned["edge_threshold"]}}),
        family="proposed",
        alert_threshold=tuned["alert_threshold"],
    )


def _with_tuned(methods: list[Method]) -> list[Method]:
    """Apply the tuned thresholds to every proposed-family variant."""
    tuned = load_tuned() or {}
    out = []
    for m in methods:
        if m.scored and m.family in ("proposed", "ablation"):
            m.alert_threshold = tuned.get("alert_threshold")
        if m.family in ("proposed", "ablation") and "hub_aware" in m.overrides:
            m.overrides["hub_aware"].setdefault("edge_threshold", tuned.get("edge_threshold", 0.5))
        elif m.family in ("proposed", "ablation"):
            m.overrides["hub_aware"] = {"edge_threshold": tuned.get("edge_threshold", 0.5)}
        out.append(m)
    return out


def e1_baselines(out: Path, seeds: int, log: Log, scenarios: list[str] | None = None) -> Path:
    methods = [m for m in baseline_methods() if m.name != "proposed"]
    methods.append(tuned_proposed())
    methods.append(Method("proposed_default", "Proposed, a-priori thresholds", dict(PROPOSED)))
    return run_grid(
        "e1_baselines", scenarios or ALL_SCENARIOS, test_seeds(seeds), methods, out, log=log
    )


def e2_ablations(out: Path, seeds: int, log: Log) -> Path:
    scenarios = ["adversarial", "hub_heavy", "high_noise", "baseline"]
    return run_grid(
        "e2_ablations", scenarios, test_seeds(seeds), _with_tuned(ablation_methods()), out, log=log
    )


FRONTIER_SCENARIOS = ["hub_heavy", "adversarial", "demo_story", "high_noise"]
FRONTIER_ALERTS = (0.2, 0.5, 0.7, 0.8, 0.9, 0.95)


def frontier_scored_methods() -> list[Method]:
    """The same two mechanisms with the calibrated scorer on top, sweeping the alert threshold."""
    tuned = load_tuned() or {}
    cap = _merge(
        STATIC_ONLY, {"static_cypher": {"max_degree_cap": 10, "enable_q3_money_cycle": False}}
    )
    hub = {
        "static_cypher": {"enabled": False},
        "community": {"enabled": False},
        "temporal_cycle": {"enabled": False},
        "smurfing": {"enabled": False},
        "hub_aware": {"edge_threshold": tuned.get("edge_threshold", 0.5)},
    }
    out = []
    for at in FRONTIER_ALERTS:
        out.append(
            Method(
                f"capS_{at}",
                f"degree cap 10 + scorer, alert {at}",
                cap,
                family="cap_scored",
                alert_threshold=at,
            )
        )
        out.append(
            Method(
                f"hubS_{at}",
                f"hub-aware + scorer, alert {at}",
                hub,
                family="hub_scored",
                alert_threshold=at,
            )
        )
    return out


def e3_frontier(out: Path, seeds: int, log: Log) -> Path:
    """Degree cap vs hub weighting, judged only on shared-attribute rings."""
    methods = []
    for cap in (3, 5, 10, 20, 50, 100, 200):
        methods.append(
            Method(
                f"cap_{cap}",
                f"degree cap {cap}",
                _merge(
                    STATIC_ONLY,
                    {"static_cypher": {"max_degree_cap": cap, "enable_q3_money_cycle": False}},
                ),
                scored=False,
                family="cap",
            )
        )
    hub_only = {
        "static_cypher": {"enabled": False},
        "community": {"enabled": False},
        "temporal_cycle": {"enabled": False},
        "smurfing": {"enabled": False},
    }
    for et in (0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.3):
        methods.append(
            Method(
                f"hub_{et}",
                f"hub-aware edge threshold {et}",
                _merge(hub_only, {"hub_aware": {"edge_threshold": et}}),
                scored=False,
                family="hub",
            )
        )
    methods += frontier_scored_methods()
    return run_grid(
        "e3_frontier",
        FRONTIER_SCENARIOS,
        test_seeds(seeds),
        methods,
        out,
        log=log,
        eval_cfg=EvalConfig(ring_types=SHARED_TYPES),
    )


def e4_scale(
    out: Path,
    seeds: int,
    log: Log,
    sizes: tuple[int, ...] = (1_000, 5_000, 20_000, 50_000, 100_000),
) -> Path:
    """
    Detection latency per batch, incremental vs full recomputation, as users grow.
    Measured on the streaming half of each run (the bootstrap is loaded first), on
    the in-memory store; Neo4j numbers come from a separate, smaller run.
    """
    from fraudsim.detectors.engine import DetectionEngine, EngineConfig, build_detectors
    from fraudsim.graph.store import InMemoryGraphStore

    out_dir = Path(out) / "e4_scale"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "results.csv"
    off = {"static_cypher": {"enabled": False}, "community": {"enabled": False}}
    t0 = time.perf_counter()
    for n in sizes:
        for seed in test_seeds(seeds):
            f = n / 1000
            stream = make_stream(
                "scale_sweep",
                seed,
                {
                    "simulation.num_users": n,
                    "simulation.num_devices": int(400 * f),
                    "simulation.num_cards": int(400 * f),
                    "simulation.num_ips": int(300 * f),
                    "simulation.duration_seconds": 20,
                },
            )
            start = stream.generator.ts(0.0)
            head = [e for e in stream.events if (e.ts - start).total_seconds() < 10]
            tail = stream.events[len(head) :]
            for mode, incremental in (("incremental", True), ("full", False)):
                store = InMemoryGraphStore()
                store.apply(head)
                engine = DetectionEngine(
                    store, build_detectors(store, off), config=EngineConfig(incremental=incremental)
                )
                engine.run(tail)
                det = sorted(
                    r.timings_ms.get("dirty", 0)
                    + r.timings_ms["view"]
                    + sum(r.detector_ms.values())
                    for r in engine.reports
                )
                regions = sorted(r.region for r in engine.reports)
                row = {
                    "experiment": "e4_scale",
                    "run_id": f"e4_scale:{n}:{seed}:{mode}",
                    "users": n,
                    "seed": seed,
                    "mode": mode,
                    "stream_digest": stream.generator.digest[:16],
                    "events_total": len(stream.events),
                    "batches": len(engine.reports),
                    "region_p50": regions[len(regions) // 2],
                    "detect_ms_p50": round(det[len(det) // 2], 3),
                    "detect_ms_p95": round(det[int(len(det) * 0.95)], 3),
                    "detect_ms_mean": round(sum(det) / len(det), 3),
                }
                _append(path, row)
                log(
                    f"[e4_scale] users={n} seed={seed} {mode:11s} region={row['region_p50']} "
                    f"p50={row['detect_ms_p50']}ms ({time.perf_counter() - t0:.0f}s)"
                )
    return path


HUB_SIZES = (5, 10, 25, 50, 100, 200)


def e5_methods() -> list[Method]:
    methods = [m for m in baseline_methods() if m.name in ("static_cap10", "static_cap100")]
    methods.append(tuned_proposed())
    methods.append(cap10_no_cycle())
    return methods


def cap10_no_cycle() -> Method:
    """Cap 10 without the 3-cycle rule: shows which rule the static baseline finds rings with."""
    return Method(
        "static_cap10_nocycle",
        "Static Cypher (cap 10, no Q3)",
        _merge(
            STATIC_ONLY, {"static_cypher": {"max_degree_cap": 10, "enable_q3_money_cycle": False}}
        ),
        scored=False,
        family="baseline",
    )


def e5_hub_size(out: Path, seeds: int, log: Log, methods: list[Method] | None = None) -> Path:
    """A shared-IP ring hides behind an office hub of growing size."""
    methods = methods or e5_methods()
    path = None
    for size in HUB_SIZES:
        path = run_grid(
            "e5_hub_size",
            ["hub_heavy"],
            test_seeds(seeds),
            methods,
            out,
            scenario_overrides={
                "simulation.hub_distribution.mega_office_gateway.size": size,
                "simulation.num_users": max(100, size + 50),
            },
            log=log,
            extra_columns={"hub_size": size},
        )
    assert path is not None
    return path


def e6_sweeps(out: Path, seeds: int, log: Log) -> Path:
    methods = [tuned_proposed(), *[m for m in baseline_methods() if m.name == "static_cap10"]]
    path = None
    for rate in (10, 25, 50, 100):
        path = run_grid(
            "e6_sweeps",
            ["adversarial"],
            test_seeds(seeds),
            methods,
            out,
            scenario_overrides={"simulation.target_event_rate": rate},
            log=log,
            extra_columns={"sweep": "event_rate", "value": rate},
        )
    for size in (3, 4, 6, 8):
        path = run_grid(
            "e6_sweeps",
            ["baseline"],
            test_seeds(seeds),
            methods,
            out,
            scenario_overrides={"rings": _rings_of_size(size)},
            log=log,
            extra_columns={"sweep": "ring_size", "value": size},
        )
    for window in (60, 300, 900):
        m = tuned_proposed(f"proposed_w{window}", f"proposed, window {window}s")
        m.engine = {"window_s": window}
        path = run_grid(
            "e6_sweeps",
            ["drift"],
            test_seeds(seeds),
            [m],
            out,
            log=log,
            extra_columns={"sweep": "window_s", "value": window},
        )
    assert path is not None
    return path


def _rings_of_size(size: int) -> list[dict[str, Any]]:
    return [
        {"id": "ring_device", "type": "shared_device", "size": size, "spawn_time_s": 5},
        {"id": "ring_card", "type": "shared_card", "size": size, "spawn_time_s": 15},
        {"id": "ring_ip", "type": "shared_ip", "size": size, "spawn_time_s": 25},
        {
            "id": "ring_cycle",
            "type": "money_cycle",
            "size": min(max(size, 3), 6),
            "length": min(max(size, 3), 6),
            "spawn_time_s": 35,
        },
    ]


def e7_adaptivity(out: Path, seeds: int, log: Log) -> Path:
    """The adaptive attacker with and without learning from flags (closed loop)."""
    from fraudsim.detectors.runner import run_detection
    from fraudsim.scoring.stage import ScoringStage

    tuned = load_tuned() or {}
    model = Path(__file__).resolve().parents[3] / "models" / "ring_scorer_lr.joblib"
    out_dir = Path(out) / "e7_adaptivity"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "results.csv"
    t0 = time.perf_counter()
    for seed in test_seeds(seeds):
        for feedback in (False, True):
            stage = ScoringStage(scorer=RingScorer.load(model))
            stage.policy.default_threshold = tuned.get("alert_threshold", 0.5)
            res = run_detection(
                "adversarial",
                seed,
                None,
                detector_overrides=_merge(
                    PROPOSED, {"hub_aware": {"edge_threshold": tuned.get("edge_threshold", 0.5)}}
                ),
                attacker_feedback=feedback,
                scoring=stage,
            )
            s = res.metrics.summary
            adaptive = [r for r in res.metrics.rings if r["evasion"] == "adaptive"]
            hist = res.generator.attacker.history if res.generator.attacker else []
            row = {
                "experiment": "e7_adaptivity",
                "run_id": f"e7:{seed}:{'feedback' if feedback else 'no_feedback'}",
                "seed": seed,
                "feedback": feedback,
                "stream_digest": res.manifest["stream_digest"][:16],
                "recall": s["ring"]["recall"],
                "precision": s["ring"]["precision"],
                "adaptive_rings": len(adaptive),
                "adaptive_detected": sum(1 for r in adaptive if r["detected"]),
                "strategy_switches": len(hist),
                "final_weights": json.dumps(hist[-1]["weights"]) if hist else "",
            }
            _append(path, row)
            log(
                f"[e7] seed={seed} feedback={feedback} adaptive "
                f"{row['adaptive_detected']}/{row['adaptive_rings']} "
                f"switches={row['strategy_switches']} ({time.perf_counter() - t0:.0f}s)"
            )
    return path


SUITES = {
    "baselines": e1_baselines,
    "ablations": e2_ablations,
    "frontier": e3_frontier,
    "scale": e4_scale,
    "hub_size": e5_hub_size,
    "sweeps": e6_sweeps,
    "adaptivity": e7_adaptivity,
}


def run_suite(name: str, out: Path, seeds: int, log: Log = print) -> Any:
    if name == "tune":
        from fraudsim.experiments.tuning import tune

        return tune(out, log=log)
    if name == "all":
        return {k: fn(out, seeds, log) for k, fn in SUITES.items()}
    return SUITES[name](out, seeds, log)
