"""
Statistics over experiment results: mean and 95% CI over seeds, paired tests
against the proposed method, and effect sizes.

All inputs are results.csv files written by the harness; nothing here invents
or smooths numbers.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

METRICS = (
    "precision",
    "recall",
    "f1",
    "user_f1",
    "ttd_p50_s",
    "detect_ms_p50",
    "full_graph_ms_p50",
)
PAIRED_COLUMNS = [
    "method",
    "reference",
    "metric",
    "pairs",
    "mean_diff",
    "cohens_dz",
    "wilcoxon_p",
    "ttest_p",
]


def load(paths: list[Path] | Path) -> pd.DataFrame:
    paths = [paths] if isinstance(paths, Path) else paths
    frames = [pd.read_csv(p) for p in paths if Path(p).exists()]
    if not frames:
        raise FileNotFoundError(f"no results found in {paths}")
    return pd.concat(frames, ignore_index=True)


def ci95(values: pd.Series) -> tuple[float, float, int]:
    """Mean and half-width of the 95% t-interval (NaNs dropped)."""
    x = values.dropna().astype(float).to_numpy()
    n = len(x)
    if n == 0:
        return math.nan, math.nan, 0
    m = float(np.mean(x))
    if n == 1:
        return m, math.nan, 1
    half = float(stats.t.ppf(0.975, n - 1) * np.std(x, ddof=1) / math.sqrt(n))
    return m, half, n


def summarize(df: pd.DataFrame, by: list[str], metrics: tuple[str, ...] = METRICS) -> pd.DataFrame:
    """One row per group with mean, CI half-width and n for each metric.

    A run where nothing was flagged has undefined precision/F1 (empty cell). For
    F1 we count that as 0 (no correct detections), and say so in the table captions.
    """
    work = df.copy()
    if "f1" in work:
        work["f1"] = work["f1"].fillna(0.0)
    if "user_f1" in work:
        work["user_f1"] = work["user_f1"].fillna(0.0)
    rows = []
    for key, group in work.groupby(by, sort=False):
        key = key if isinstance(key, tuple) else (key,)
        row = dict(zip(by, key, strict=True))
        row["runs"] = len(group)
        for m in metrics:
            if m not in group:
                continue
            mean, half, n = ci95(group[m])
            row[f"{m}_mean"] = mean
            row[f"{m}_ci95"] = half
            row[f"{m}_n"] = n
        rows.append(row)
    return pd.DataFrame(rows)


def paired_vs(
    df: pd.DataFrame, reference: str, metric: str = "f1", by: tuple[str, ...] = ("scenario", "seed")
) -> pd.DataFrame:
    """Paired comparison of every method against `reference` on identical streams."""
    work = df.copy()
    work[metric] = work[metric].fillna(0.0) if metric in ("f1", "user_f1") else work[metric]
    pivot = work.pivot_table(index=list(by), columns="method", values=metric, aggfunc="first")
    out = []
    if reference not in pivot:
        return pd.DataFrame(columns=PAIRED_COLUMNS)
    for method in pivot.columns:
        if method == reference:
            continue
        pair = pivot[[reference, method]].dropna()
        if len(pair) < 2:
            continue
        diff = (pair[reference] - pair[method]).to_numpy(dtype=float)
        sd = float(np.std(diff, ddof=1))
        try:
            w = stats.wilcoxon(diff, zero_method="zsplit") if np.any(diff != 0) else None
        except ValueError:
            w = None
        t = stats.ttest_rel(pair[reference], pair[method]) if sd > 0 else None
        out.append(
            {
                "method": method,
                "reference": reference,
                "metric": metric,
                "pairs": len(pair),
                "mean_diff": float(np.mean(diff)),
                "cohens_dz": float(np.mean(diff) / sd) if sd > 0 else math.nan,
                "wilcoxon_p": float(w.pvalue) if w is not None else math.nan,
                "ttest_p": float(t.pvalue) if t is not None else math.nan,
            }
        )
    return pd.DataFrame(out, columns=PAIRED_COLUMNS)


def fmt_ci(mean: float, half: float, digits: int = 2) -> str:
    if mean is None or (isinstance(mean, float) and math.isnan(mean)):
        return "--"
    if half is None or (isinstance(half, float) and math.isnan(half)):
        return f"{mean:.{digits}f}"
    return f"{mean:.{digits}f} $\\pm$ {half:.{digits}f}"
