"""
Metric exporters — CSV/Parquet and Prometheus-style /metrics.

Stub for Milestone 5.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def export_csv(data: list[dict[str, Any]], path: Path) -> None:
    """Write a list of dicts to a CSV file."""
    if not data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)


def export_jsonl(data: list[dict[str, Any]], path: Path) -> None:
    """Write a list of dicts to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for item in data:
            f.write(json.dumps(item, default=str) + "\n")


def export_parquet(data: list[dict[str, Any]], path: Path) -> None:
    """Write a list of dicts to a Parquet file."""
    import pandas as pd

    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(data)
    df.to_parquet(path, index=False)
