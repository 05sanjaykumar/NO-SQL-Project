"""
Runs every paper experiment on the test seeds, one after another (sequential,
so latency numbers are not distorted by runs competing for cores).

    python scripts/run_paper_experiments.py            # all, default seed counts
    python scripts/run_paper_experiments.py frontier   # one experiment

Seed counts per experiment are fixed here so the paper can state them.
Requires configs/tuned.json (python -m fraudsim.cli experiment --suite tune).
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

from fraudsim.experiments.suite import SUITES

SEEDS = {
    "baselines": 10,
    "ablations": 5,
    "frontier": 5,
    "hub_size": 5,
    "sweeps": 5,
    "adaptivity": 10,
    "scale": 3,
}
ORDER = ["baselines", "ablations", "frontier", "hub_size", "sweeps", "adaptivity", "scale"]


def main() -> None:
    names = sys.argv[1:] or ORDER
    out = Path("results")
    for name in names:
        t0 = time.perf_counter()
        print(f"=== {name} ({SEEDS[name]} seeds) ===", flush=True)
        try:
            SUITES[name](out, SEEDS[name], lambda m: print(m, flush=True))
            print(f"=== {name} done in {time.perf_counter() - t0:.0f}s ===", flush=True)
        except Exception:
            traceback.print_exc()
            print(f"=== {name} FAILED after {time.perf_counter() - t0:.0f}s ===", flush=True)


if __name__ == "__main__":
    main()
