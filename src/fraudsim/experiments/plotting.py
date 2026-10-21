"""
Paper figures, drawn only from experiment results (results/<experiment>/results.csv).

Nothing here contains a number: every point is computed from rows the harness
wrote, and a figure whose results file is missing is skipped, not faked.

    python -m fraudsim.experiments.plotting --results-dir results --output-dir docs/paper/figures
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from fraudsim.experiments.stats import ci95, summarize

plt.rcParams.update(
    {
        "font.size": 8,
        "axes.titlesize": 8,
        "axes.labelsize": 8,
        "legend.fontsize": 7,
        "figure.dpi": 150,
        "savefig.bbox": "tight",
        "pdf.fonttype": 42,
    }
)
COL = (3.5, 2.4)  # one IEEE column
COLORS = {
    "proposed": "#1f6feb",
    "cap": "#d1242f",
    "hub": "#1f6feb",
    "cap_scored": "#d1242f",
    "hub_scored": "#1f6feb",
    "static": "#d1242f",
    "community": "#8250df",
    "ablation": "#6e7781",
}


def _results(results_dir: Path, experiment: str) -> pd.DataFrame | None:
    path = Path(results_dir) / experiment / "results.csv"
    if not path.exists():
        print(f"[figures] skip {experiment}: {path} not found")
        return None
    return pd.read_csv(path)


def _save(fig: plt.Figure, output_dir: Path, name: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = [output_dir / f"{name}.pdf", output_dir / f"{name}.png"]
    for p in paths:
        fig.savefig(p)
    plt.close(fig)
    print(f"[figures] wrote {paths[0]}")
    return paths


def pooled_pr(group: pd.DataFrame) -> tuple[float | None, float | None]:
    """Micro-averaged ring precision/recall over all runs in the group."""
    flagged = group["rings_flagged"].sum()
    fp = group["false_rings"].sum()
    precision = (flagged - fp) / flagged if flagged else None
    true = group["true_rings"].sum()
    recall = group["rings_detected"].sum() / true if true else None
    return precision, recall


def plot_pr_frontier(output_dir: Path, results_dir: Path = Path("results")) -> list[Path] | None:
    """Fig. 1: degree cap vs hub weighting, raw and with the scorer (shared-attribute rings)."""
    df = _results(results_dir, "e3_frontier")
    if df is None:
        return None
    fig, ax = plt.subplots(figsize=(COL[0], 2.8))
    series = (
        ("cap", "s", "-", "Degree cap (label: cap)"),
        ("hub", "o", "-", "Hub weighting (label: edge thr.)"),
        ("cap_scored", "s", "--", "Cap 10 + scorer (label: alert thr.)"),
        ("hub_scored", "o", "--", "Hub weighting + scorer (label: alert thr.)"),
    )
    for family, marker, ls, label in series:
        sub = df[df["family"] == family]
        pts = []
        for method, g in sub.groupby("method", sort=False):
            p, r = pooled_pr(g)
            if p is not None and r is not None:
                pts.append((r, p, method.split("_", 1)[1]))
        if not pts:
            continue
        pts.sort()
        ax.plot(
            [x[0] for x in pts],
            [x[1] for x in pts],
            marker=marker,
            ms=3.5,
            lw=1,
            ls=ls,
            mfc="white" if family.endswith("scored") else None,
            color=COLORS[family],
            label=label,
        )
        for r, p, tag in pts:
            ax.annotate(
                tag,
                (r, p),
                textcoords="offset points",
                xytext=(3, 2),
                fontsize=5.5,
                color=COLORS[family],
            )
    ax.set_xlabel("Recall (shared-attribute rings)")
    ax.set_ylabel("Precision")
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.05)
    ax.grid(alpha=0.3)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2, fontsize=6)
    return _save(fig, output_dir, "fig1_pr_frontier")


def plot_latency_scaling(
    output_dir: Path, results_dir: Path = Path("results")
) -> list[Path] | None:
    """Fig. 2: per-batch detection latency vs graph size, incremental vs full."""
    df = _results(results_dir, "e4_scale")
    if df is None:
        return None
    fig, ax = plt.subplots(figsize=COL)
    for mode, color, marker in (("incremental", "#1f6feb", "o"), ("full", "#d1242f", "s")):
        sub = df[df["mode"] == mode]
        if sub.empty:
            continue
        # Mean over seeds; bars span the per-seed minimum and maximum (3 seeds are too
        # few for a t-interval on a log axis).
        xs, ys, lo, hi = [], [], [], []
        for users, g in sub.groupby("users"):
            v = g["detect_ms_p50"].astype(float)
            xs.append(users)
            ys.append(v.mean())
            lo.append(v.mean() - v.min())
            hi.append(v.max() - v.mean())
        ax.errorbar(
            xs,
            ys,
            yerr=[lo, hi],
            marker=marker,
            ms=3.5,
            lw=1,
            capsize=2,
            color=color,
            label=f"{mode} recomputation" if mode == "full" else "incremental (dirty region)",
        )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Users in graph")
    ax.set_ylabel("Detection latency per batch, p50 (ms)")
    ax.grid(alpha=0.3, which="both")
    ax.legend()
    return _save(fig, output_dir, "fig2_latency_scaling")


def plot_ablation(output_dir: Path, results_dir: Path = Path("results")) -> list[Path] | None:
    """Fig. 3: F1 per ablation (mean and 95% CI over scenario x seed runs)."""
    df = _results(results_dir, "e2_ablations")
    if df is None:
        return None
    s = summarize(df, ["method"], ("f1",))
    labels = df.drop_duplicates("method").set_index("method")["method_label"]
    fig, ax = plt.subplots(figsize=(COL[0], 2.6))
    y = range(len(s))
    colors = [COLORS["proposed"] if m == "proposed" else COLORS["ablation"] for m in s["method"]]
    ax.barh(list(y), s["f1_mean"], xerr=s["f1_ci95"].fillna(0), color=colors, capsize=2)
    ax.set_yticks(list(y), [labels.get(m, m) for m in s["method"]])
    ax.invert_yaxis()
    ax.set_xlabel("Ring F1 (mean, 95% CI)")
    ax.set_xlim(0, 1)
    ax.grid(alpha=0.3, axis="x")
    return _save(fig, output_dir, "fig3_ablation")


def plot_hub_size(output_dir: Path, results_dir: Path = Path("results")) -> list[Path] | None:
    """Fig. 4: hub-hiding recall (left) and ring F1 (right) as the hub grows."""
    df = _results(results_dir, "e5_hub_size")
    if df is None:
        return None
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(COL[0], 2.0), sharex=True)
    labels = df.drop_duplicates("method").set_index("method")["method_label"]
    for method, g in df.groupby("method", sort=False):
        xs, ys, fs, fe = [], [], [], []
        for size, gg in g.groupby("hub_size"):
            hits = tot = 0
            for cell in gg["detected_by_evasion"].dropna():
                d = json.loads(cell).get("hub_hiding")
                if d:
                    hits, tot = hits + d[0], tot + d[1]
            if tot:
                xs.append(size)
                ys.append(hits / tot)
                m, h, _ = ci95(gg["f1"].fillna(0))
                fs.append(m)
                fe.append(0 if pd.isna(h) else h)
        fam = "proposed" if method == "proposed" else "static"
        style = {
            "marker": "o" if fam == "proposed" else "s",
            "ms": 3,
            "lw": 1,
            "ls": {"proposed": "-", "static_cap10": "--", "static_cap100": ":"}.get(method, "-."),
            "color": COLORS[fam],
        }
        ax.plot(xs, ys, label=labels.get(method, method), **style)
        ax2.errorbar(xs, fs, yerr=fe, capsize=2, **style)
    for a_ in (ax, ax2):
        a_.set_xscale("log")
        a_.set_ylim(-0.03, 1.03)
        a_.grid(alpha=0.3)
        a_.set_xlabel("Hub size (accounts)")
    ax.set_ylabel("Recall, hub-hiding rings")
    ax2.set_ylabel("Ring F1")
    fig.legend(
        *ax.get_legend_handles_labels(),
        loc="upper center",
        bbox_to_anchor=(0.5, 0.02),
        ncol=3,
        fontsize=6,
    )
    fig.tight_layout()
    return _save(fig, output_dir, "fig4_hub_size")


def plot_evasion(output_dir: Path, results_dir: Path = Path("results")) -> list[Path] | None:
    """Fig. 5: recall by evasion strategy, pooled over all E1 scenarios and seeds."""
    df = _results(results_dir, "e1_baselines")
    if df is None:
        return None
    labels = df.drop_duplicates("method").set_index("method")["method_label"]
    table: dict[str, dict[str, float]] = {}
    for method, g in df.groupby("method", sort=False):
        acc: dict[str, list[int]] = {}
        for cell in g["detected_by_evasion"].dropna():
            for mode, (hit, tot) in json.loads(cell).items():
                a = acc.setdefault(mode, [0, 0])
                a[0] += hit
                a[1] += tot
        table[method] = {k: v[0] / v[1] for k, v in acc.items() if v[1]}
    modes = sorted({m for v in table.values() for m in v})
    methods = list(table)
    fig, ax = plt.subplots(figsize=(7.16, 2.3))
    width = 0.8 / max(len(methods), 1)
    for i, method in enumerate(methods):
        xs = [j + i * width for j in range(len(modes))]
        ax.bar(
            xs, [table[method].get(m, 0) for m in modes], width, label=labels.get(method, method)
        )
    ax.set_xticks([j + 0.4 - width / 2 for j in range(len(modes))], modes)
    ax.set_ylabel("Ring recall")
    ax.set_ylim(0, 1.05)
    ax.grid(alpha=0.3, axis="y")
    ax.legend(ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.15))
    return _save(fig, output_dir, "fig5_evasion")


def plot_sweeps(output_dir: Path, results_dir: Path = Path("results")) -> list[Path] | None:
    """Fig. 6: F1 of the proposed method under event-rate, ring-size and window sweeps."""
    df = _results(results_dir, "e6_sweeps")
    if df is None:
        return None
    sweeps = [s for s in ("event_rate", "ring_size", "window_s") if s in set(df["sweep"])]
    fig, axes = plt.subplots(1, len(sweeps), figsize=(7.16, 2.0), sharey=True)
    axes = [axes] if len(sweeps) == 1 else list(axes)
    names = {"event_rate": "Events / s", "ring_size": "Ring size", "window_s": "Window (s)"}
    for ax, sweep in zip(axes, sweeps, strict=True):
        sub = df[df["sweep"] == sweep].copy()
        sub["fam"] = sub["method"].str.startswith("proposed")
        for fam, g in sub.groupby("fam"):
            xs, ys, es = [], [], []
            for value, gg in g.groupby("value"):
                m, h, _ = ci95(gg["f1"].fillna(0))
                xs.append(value)
                ys.append(m)
                es.append(0 if pd.isna(h) else h)
            ax.errorbar(
                xs,
                ys,
                yerr=es,
                marker="o" if fam else "s",
                ms=3,
                lw=1,
                capsize=2,
                color=COLORS["proposed" if fam else "static"],
                label="proposed" if fam else "static cap 10",
            )
        ax.set_xlabel(names[sweep])
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("Ring F1")
    axes[0].set_ylim(0, 1.05)
    axes[0].legend()
    return _save(fig, output_dir, "fig6_sweeps")


FIGURES = (
    plot_pr_frontier,
    plot_latency_scaling,
    plot_ablation,
    plot_hub_size,
    plot_evasion,
    plot_sweeps,
)


def plot_all(results_dir: Path, output_dir: Path) -> list[Path]:
    written: list[Path] = []
    for fn in FIGURES:
        written += fn(output_dir, results_dir) or []
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--output-dir", type=Path, default=Path("docs/paper/figures"))
    args = parser.parse_args()
    plot_all(args.results_dir, args.output_dir)


if __name__ == "__main__":
    main()
