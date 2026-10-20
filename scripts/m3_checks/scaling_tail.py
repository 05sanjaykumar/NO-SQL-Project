"""Same measurement as scaling.py, but events before duration/2 are loaded into the
store directly (no detection), and detection runs only on the measured streaming half."""

import logging
import statistics as st
import sys
import time

import structlog

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
from fraudsim.detectors.engine import DetectionEngine, EngineConfig, build_detectors
from fraudsim.graph.store import InMemoryGraphStore
from fraudsim.simulator.event_generator import EventGenerator
from fraudsim.simulator.scenario_loader import load_scenario

n, inc, duration = int(sys.argv[1]), sys.argv[2] == "inc", float(sys.argv[3])
cfg = load_scenario("scale_sweep")
sim = cfg["simulation"]
f = n / 1000
sim.update(
    num_users=n,
    num_devices=int(400 * f),
    num_cards=int(400 * f),
    num_ips=int(300 * f),
    duration_seconds=duration,
)
gen = EventGenerator(seed=42, run_id="scal", config=cfg)
events = gen.generate()
start = gen.ts(0.0)
head = [e for e in events if (e.ts - start).total_seconds() < duration / 2]
tail = events[len(head) :]
off = {"static_cypher": {"enabled": False}, "community": {"enabled": False}}
store = InMemoryGraphStore()
store.apply(head)
engine = DetectionEngine(store, build_detectors(store, off), config=EngineConfig(incremental=inc))
t = time.perf_counter()
engine.run(tail)
wall = time.perf_counter() - t
rep = engine.reports
det = sorted(
    r.timings_ms.get("dirty", 0) + r.timings_ms["view"] + sum(r.detector_ms.values()) for r in rep
)
print(
    f"users={n} mode={'incremental' if inc else 'full'} measured={len(rep)} digest={gen.digest[:12]} "
    f"region_p50={st.median(r.region for r in rep):.0f} detect_ms_p50={det[len(det) // 2]:.1f} "
    f"detect_ms_p95={det[int(len(det) * 0.95)]:.1f} wall_s={wall:.1f}",
    flush=True,
)
