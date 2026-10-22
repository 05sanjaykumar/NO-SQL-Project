"""
IBM AMLSim sample datasets (Suzumura & Kanezashi, github.com/IBM/AMLSim, Apache-2.0).

The AMLSim repository ships pre-generated samples under sample/*.tgz
(20K_cycle200, 20K_fanin200, 20K_fanin200cycle200): 20,000 accounts, about
120,000 transfers and 200 planted cycle and/or fan-in patterns each.
`python scripts/fetch_datasets.py amlsim` clones the repository and unpacks them
to data/raw/amlsim/samples/<name>/.

Files: nodes.csv (nodeid, isFraud, init_balance, fraudStep) and transactions.csv
(sourceNodeId, targetNodeId, value, time). Labels are per account; the samples do
not say which accounts form which pattern, so only account-level precision and
recall can be computed (running the AMLSim generator itself would also give
alert groups). One time step is `step_seconds` of simulated time; transfers within
a step are spread evenly over it in file order.
"""

from __future__ import annotations

import csv
from pathlib import Path

from fraudsim.datasets.base import LazyEvents, PublicDataset, file_digest


def load_amlsim(path: str | Path, step_seconds: float = 3600.0) -> PublicDataset:
    root = Path(path)
    nodes, trans = root / "nodes.csv", root / "transactions.csv"
    for p in (nodes, trans):
        if not p.exists():
            raise FileNotFoundError(f"{p} missing (python scripts/fetch_datasets.py amlsim)")
    illicit: set[str] = set()
    with open(nodes, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["isFraud"].strip() == "1":
                illicit.add(f"a{row['nodeid']}")
    raw: list[tuple[int, int, str, str, float]] = []
    with open(trans, encoding="utf-8", newline="") as f:
        for i, row in enumerate(csv.DictReader(f)):
            raw.append(
                (
                    int(row["time"]),
                    i,
                    f"a{row['sourceNodeId']}",
                    f"a{row['targetNodeId']}",
                    float(row["value"]),
                )
            )
    raw.sort(key=lambda r: (r[0], r[1]))
    per_step: dict[int, int] = {}
    for step, *_ in raw:
        per_step[step] = per_step.get(step, 0) + 1
    seen: dict[int, int] = {}
    rows = []
    t0 = raw[0][0] if raw else 0
    for step, i, a, b, amt in raw:
        k = seen.get(step, 0)
        seen[step] = k + 1
        rows.append(
            ((step - t0) * step_seconds + step_seconds * k / per_step[step], f"sim{i}", a, b, amt)
        )
    events = LazyEvents("amlsim", rows)
    return PublicDataset(
        f"amlsim_{root.name}",
        events,
        illicit & events.accounts,
        events.accounts,
        [],
        {
            "file": f"{root.name}/transactions.csv",
            "sha256": file_digest(str(trans)),
            "rows": len(rows),
            "slice": "all",
            "step_seconds": step_seconds,
        },
    )
