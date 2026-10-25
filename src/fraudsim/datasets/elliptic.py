"""
Elliptic Bitcoin dataset (Weber et al., KDD Workshop on Anomaly Detection in Finance, 2019).

Download (free Kaggle account required):
    https://www.kaggle.com/datasets/ellipticco/elliptic-data-set
Pass the folder containing elliptic_txs_features.csv, elliptic_txs_classes.csv and
elliptic_txs_edgelist.csv.

Nodes are Bitcoin *transactions* (not wallets) and an edge is a flow of BTC from one
transaction to the next. We map each node to an account and each edge to a
transfer at the source node's time step (49 steps, about two weeks apart; one step
is `step_seconds` of simulated time). Amounts are not published, so every transfer
has amount 1. Labels: class 1 = illicit, 2 = licit, unknown otherwise.
"""

from __future__ import annotations

import csv
from pathlib import Path

from fraudsim.datasets.base import EventBuilder, PublicDataset, file_digest, sim_time


def load_elliptic(
    path: str | Path, step_seconds: float = 600.0, max_steps: int | None = None
) -> PublicDataset:
    root = Path(path)
    features = root / "elliptic_txs_features.csv"
    classes = root / "elliptic_txs_classes.csv"
    edges = root / "elliptic_txs_edgelist.csv"
    for p in (features, classes, edges):
        if not p.exists():
            raise FileNotFoundError(f"{p} missing (see module docstring for download)")
    step: dict[str, int] = {}
    with open(features, encoding="utf-8", newline="") as f:
        for row in csv.reader(f):  # no header: txId, time step, 165 features
            step[row[0]] = int(row[1])
    illicit: set[str] = set()
    with open(classes, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader)
        for tx, cls in reader:
            if cls == "1":
                illicit.add(f"tx{tx}")
    flows: list[tuple[int, int, str, str]] = []
    with open(edges, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader)
        for i, (a, c) in enumerate(reader):
            s = step.get(a)
            if s is None or (max_steps is not None and s > max_steps):
                continue
            flows.append((s, i, a, c))
    flows.sort()
    b = EventBuilder("elliptic")
    for s, i, a, c in flows:
        # Every edge of a step is stamped at the start of that step.
        b.transaction(f"ell{i}", f"tx{a}", f"tx{c}", 1.0, sim_time((s - 1) * step_seconds))
    return PublicDataset(
        "elliptic",
        b.events,
        illicit & b.accounts,
        b.accounts,
        [],
        {
            "files": [features.name, classes.name, edges.name],
            "sha256": file_digest(str(edges)),
            "rows": len(flows),
        },
    )
