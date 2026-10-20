"""
Metric exporters: CSV, JSONL, Parquet, and Prometheus text exposition format.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def export_csv(data: list[dict[str, Any]], path: Path) -> None:
    """Write a list of dicts to a CSV file (union of keys as columns)."""
    if not data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in data:
        fields += [k for k in row if k not in fields]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(data)


def export_jsonl(data: list[dict[str, Any]], path: Path) -> None:
    """Write a list of dicts to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for item in data:
            f.write(json.dumps(item, default=str) + "\n")


def export_parquet(data: list[dict[str, Any]], path: Path) -> None:
    """Write a list of dicts to a Parquet file."""
    import pandas as pd

    if not data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(data).to_parquet(path, index=False)


def write_run_metrics(metrics: Any, out_dir: Path) -> dict[str, str]:
    """Write a RunMetrics bundle: summary.json + CSV and Parquet tables."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(metrics.summary, indent=2, default=str), encoding="utf-8"
    )
    written = {"summary": str(out_dir / "summary.json")}
    for name in ("rings", "flags", "timeseries", "batches"):
        rows = getattr(metrics, name)
        export_csv(rows, out_dir / f"{name}.csv")
        export_parquet(rows, out_dir / f"{name}.parquet")
        written[name] = str(out_dir / f"{name}.csv")
    (out_dir / "metrics.prom").write_text(prometheus_text(metrics.summary), encoding="utf-8")
    return written


def _flatten(prefix: str, value: Any, out: list[tuple[str, float]]) -> None:
    if isinstance(value, bool):
        out.append((prefix, 1.0 if value else 0.0))
    elif isinstance(value, int | float):
        out.append((prefix, float(value)))
    elif isinstance(value, dict):
        for k, v in value.items():
            key = str(k).replace(".", "_").replace("-", "_")
            _flatten(f"{prefix}_{key}" if prefix else key, v, out)


def prometheus_text(summary: dict[str, Any], prefix: str = "fraudsim", labels: str = "") -> str:
    """Every numeric leaf of the summary as a Prometheus gauge (None values are skipped)."""
    flat: list[tuple[str, float]] = []
    _flatten("", summary, flat)
    lab = f"{{{labels}}}" if labels else ""
    lines = []
    for key, value in flat:
        name = f"{prefix}_{key}"
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name}{lab} {value:g}")
    return "\n".join(lines) + "\n"
