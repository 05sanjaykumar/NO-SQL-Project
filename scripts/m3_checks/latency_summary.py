import json
import statistics
import sys
from pathlib import Path


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


for run in sys.argv[1:]:
    d = Path("runs") / run
    m = json.loads((d / "manifest.json").read_text())
    rows = [json.loads(line) for line in (d / "batches.jsonl").read_text().splitlines()]
    inc_names = ["HubAwareSharedAttribute", "TemporalCycleDetector", "SmurfingDetector"]
    detect = [
        r["timings_ms"].get("dirty", 0)
        + r["timings_ms"].get("view", 0)
        + sum(r["detector_ms"].get(n, 0) for n in inc_names)
        for r in rows
    ]
    ingest = [r["timings_ms"]["ingest"] for r in rows]
    total = [r["timings_ms"]["total"] for r in rows]
    region = [r["region"] for r in rows]
    full = [r for r in rows if r["full_run"]]
    static = [
        r["detector_ms"]["StaticCypherBaseline"]
        for r in full
        if "StaticCypherBaseline" in r["detector_ms"]
    ]
    comm = [
        r["detector_ms"]["CommunityDetector"]
        for r in full
        if "CommunityDetector" in r["detector_ms"]
    ]
    print(
        f"{run} incremental={m['engine']['incremental']} batches={len(rows)} events={m['events']} wall_s={m['wall_s']}"
    )
    for name, xs in [
        ("detect(dirty+view+3 inc detectors)", detect),
        ("ingest", ingest),
        ("batch total", total),
        ("region users", region),
        ("static cypher (timer)", static),
        ("community GDS (timer)", comm),
    ]:
        print(
            f"  {name:36} p50={pct(xs, 50):9.2f} p95={pct(xs, 95):9.2f} p99={pct(xs, 99):9.2f} mean={statistics.mean(xs) if xs else float('nan'):9.2f} n={len(xs)}"
        )
    last = rows[-1]
    print(
        "  rings:",
        m["detection"]["rings_by_status"],
        "queries/batch(last):",
        last.get("store_queries"),
    )
