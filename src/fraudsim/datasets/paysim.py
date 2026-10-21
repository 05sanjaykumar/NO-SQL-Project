"""
PaySim synthetic mobile-money log (Lopez-Rojas et al., EMSS 2016).

Download (free Kaggle account required):
    https://www.kaggle.com/datasets/ealaxi/paysim1
Pass the CSV (PS_20174392719_1491204439457_log.csv) or its folder.

Columns: step (hour), type, amount, nameOrig, oldbalanceOrg, newbalanceOrig,
nameDest, oldbalanceDest, newbalanceDest, isFraud, isFlaggedFraud. Fraud in
PaySim is account takeover followed by TRANSFER then CASH_OUT, so it is not a
ring dataset: we report it to show what the transaction detectors do (and do not)
pick up outside the ring setting. Rows within one step are spread uniformly over
that hour in file order.
"""

from __future__ import annotations

import csv
from pathlib import Path

from fraudsim.datasets.base import EventBuilder, PublicDataset, file_digest, sim_time


def load_paysim(path: str | Path, max_rows: int | None = None) -> PublicDataset:
    p = Path(path)
    if p.is_dir():
        found = sorted(p.glob("PS_*_log.csv")) or sorted(p.glob("*.csv"))
        if not found:
            raise FileNotFoundError(f"no PaySim CSV in {p} (see module docstring for download)")
        p = found[0]
    rows: list[list[str]] = []
    with open(p, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader)
        for i, row in enumerate(reader):
            if max_rows is not None and i >= max_rows:
                break
            rows.append(row)
    per_step: dict[int, int] = {}
    for row in rows:
        per_step[int(row[0])] = per_step.get(int(row[0]), 0) + 1
    b = EventBuilder("paysim")
    illicit: set[str] = set()
    seen: dict[int, int] = {}
    for i, row in enumerate(rows):
        step = int(row[0])
        k = seen.get(step, 0)
        seen[step] = k + 1
        ts = sim_time((step - 1) * 3600 + 3600 * k / per_step[step])
        b.transaction(f"ps{i}", row[3], row[6], float(row[2]), ts)
        if row[9] == "1":
            # Both the taken-over origin and the receiving (mule) account are involved.
            illicit.update((row[3], row[6]))
    return PublicDataset(
        "paysim",
        b.events,
        illicit,
        b.accounts,
        [],
        {"file": p.name, "sha256": file_digest(str(p)), "rows": len(rows)},
    )
