"""
LaTeX tables and number macros for the paper, generated only from experiment
results (results/<experiment>/results.csv). Each file records the results it was
built from (path, sha256, row count) so a number in the paper can be traced to
its runs. A table whose results are missing is skipped, never filled in.

    python -m fraudsim.experiments.latex_export --results-dir results --output-dir docs/paper/tables
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from fraudsim.experiments.stats import fmt_ci, paired_vs, summarize


def _results(results_dir: Path, experiment: str) -> tuple[pd.DataFrame, str] | None:
    path = Path(results_dir) / experiment / "results.csv"
    if not path.exists():
        print(f"[tables] skip {experiment}: {path} not found")
        return None
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    df = pd.read_csv(path)
    return df, f"% generated from {path.as_posix()} (sha256 {digest}, {len(df)} rows)\n"


def _write(output_dir: Path, name: str, body: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / name
    path.write_text(body, encoding="utf-8")
    print(f"[tables] wrote {path}")
    return path


def _esc(s: str) -> str:
    return str(s).replace("_", r"\_").replace("%", r"\%").replace("&", r"\&")


def _p(p: float) -> str:
    if p is None or math.isnan(p):
        return "--"
    return "$<$0.001" if p < 0.001 else f"{p:.3f}"


def _num(x: float, digits: int = 1) -> str:
    return "--" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}"


def _latency(r: pd.Series, family: str | None) -> str:
    """Per-batch latency for incremental methods; one full-graph pass for full-graph baselines."""
    if family == "baseline":
        return _num(r.get("full_graph_ms_p50_mean")) + r"$^\dagger$"
    return _num(r["detect_ms_p50_mean"])


def generate_baseline_table(output_dir: Path, results_dir: Path = Path("results")) -> Path | None:
    """Table 1: every method over all E1 scenario x seed runs."""
    got = _results(results_dir, "e1_baselines")
    if got is None:
        return None
    df, header = got
    labels = df.drop_duplicates("method").set_index("method")["method_label"]
    families = df.drop_duplicates("method").set_index("method")["family"].to_dict()
    s = summarize(df, ["method"])
    tests = (
        paired_vs(df, "proposed", "f1").set_index("method")
        if "proposed" in set(df["method"])
        else None
    )
    best = s["f1_mean"].max()
    lines = []
    for _, r in s.iterrows():
        f1 = fmt_ci(r["f1_mean"], r["f1_ci95"])
        if r["f1_mean"] == best:
            f1 = r"\textbf{" + f1 + "}"
        p = (
            _p(tests.loc[r["method"], "wilcoxon_p"])
            if tests is not None and r["method"] in tests.index
            else "--"
        )
        lines.append(
            f"{_esc(labels.get(r['method'], r['method']))} & {fmt_ci(r['precision_mean'], r['precision_ci95'])}"
            f" & {fmt_ci(r['recall_mean'], r['recall_ci95'])} & {f1}"
            f" & {_num(r['ttd_p50_s_mean'])} & {_latency(r, families.get(r['method']))} & {p} \\\\"
        )
    n_scen, n_seed = df["scenario"].nunique(), df["seed"].nunique()
    body = (
        header
        + rf"""\begin{{table*}}[t]
\centering
\caption{{Ring-level detection on {n_scen} scenarios $\times$ {n_seed} test seeds (mean $\pm$ 95\% CI over runs;
a run with no flags has undefined precision and counts as F1 $=0$). TTD: median time from ring activation to
first flag. Latency: median per-batch detection time on the in-memory store; $^\dagger$full-graph
methods run on a 5\,s timer, not per batch, so their entry is the median time of one full-graph pass. $p$: paired Wilcoxon signed-rank
test of F1 against the proposed method on identical streams.}}
\label{{tab:baselines}}
\begin{{tabular}}{{lcccccc}}
\toprule
Method & Precision & Recall & F1 & TTD (s) & Latency (ms) & $p$ \\
\midrule
{chr(10).join(lines)}
\bottomrule
\end{{tabular}}
\end{{table*}}
"""
    )
    return _write(output_dir, "tab1_baselines.tex", body)


def generate_scenario_table(output_dir: Path, results_dir: Path = Path("results")) -> Path | None:
    """Table 1b: F1 per scenario and method."""
    got = _results(results_dir, "e1_baselines")
    if got is None:
        return None
    df, header = got
    labels = df.drop_duplicates("method").set_index("method")["method_label"]
    s = summarize(df, ["method", "scenario"], ("f1",))
    scenarios = list(dict.fromkeys(df["scenario"]))
    lines = []
    for method in dict.fromkeys(df["method"]):
        cells = []
        for sc in scenarios:
            row = s[(s["method"] == method) & (s["scenario"] == sc)]
            cells.append(
                fmt_ci(row["f1_mean"].iloc[0], row["f1_ci95"].iloc[0]) if len(row) else "--"
            )
        lines.append(f"{_esc(labels.get(method, method))} & " + " & ".join(cells) + r" \\")
    body = (
        header
        + rf"""\begin{{table*}}[t]
\centering
\caption{{Ring F1 per scenario (mean $\pm$ 95\% CI over {df["seed"].nunique()} test seeds).}}
\label{{tab:scenarios}}
\begin{{tabular}}{{l{"c" * len(scenarios)}}}
\toprule
Method & {" & ".join(_esc(s) for s in scenarios)} \\
\midrule
{chr(10).join(lines)}
\bottomrule
\end{{tabular}}
\end{{table*}}
"""
    )
    return _write(output_dir, "tab1b_scenarios.tex", body)


def generate_ablation_table(output_dir: Path, results_dir: Path = Path("results")) -> Path | None:
    """Table 2: ablations with the paired difference to the full system."""
    got = _results(results_dir, "e2_ablations")
    if got is None:
        return None
    df, header = got
    labels = df.drop_duplicates("method").set_index("method")["method_label"]
    s = summarize(df, ["method"])
    tests = paired_vs(df, "proposed", "f1").set_index("method")
    lines = []
    for _, r in s.iterrows():
        m = r["method"]
        if m in tests.index:
            t = tests.loc[m]
            delta = f"{-t['mean_diff']:+.2f}"
            dz = _num(t["cohens_dz"], 2)
            p = _p(t["wilcoxon_p"])
        else:
            delta = dz = p = "--"
        lines.append(
            f"{_esc(labels.get(m, m))} & {fmt_ci(r['precision_mean'], r['precision_ci95'])}"
            f" & {fmt_ci(r['recall_mean'], r['recall_ci95'])} & {fmt_ci(r['f1_mean'], r['f1_ci95'])}"
            f" & {delta} & {dz} & {p} & {_num(r['detect_ms_p50_mean'])} \\\\"
        )
    body = (
        header
        + rf"""\begin{{table*}}[t]
\centering
\caption{{Ablations on {", ".join(_esc(x) for x in dict.fromkeys(df["scenario"]))}
($\times$ {df["seed"].nunique()} test seeds). $\Delta$F1: paired mean difference to the full system;
$d_z$: paired effect size of the full system over the variant; $p$: Wilcoxon signed-rank.}}
\label{{tab:ablation}}
\begin{{tabular}}{{lccccccc}}
\toprule
Variant & Precision & Recall & F1 & $\Delta$F1 & $d_z$ & $p$ & Latency (ms) \\
\midrule
{chr(10).join(lines)}
\bottomrule
\end{{tabular}}
\end{{table*}}
"""
    )
    return _write(output_dir, "tab2_ablation.tex", body)


def generate_scale_table(output_dir: Path, results_dir: Path = Path("results")) -> Path | None:
    """Table 3: incremental vs full recomputation as the graph grows."""
    got = _results(results_dir, "e4_scale")
    if got is None:
        return None
    df, header = got
    lines = []
    for users, g in df.groupby("users"):
        inc = summarize(g[g["mode"] == "incremental"], ["mode"], ("detect_ms_p50", "region_p50"))
        full = summarize(g[g["mode"] == "full"], ["mode"], ("detect_ms_p50", "region_p50"))
        if inc.empty or full.empty:
            continue
        i, f = inc.iloc[0], full.iloc[0]
        speed = (
            f["detect_ms_p50_mean"] / i["detect_ms_p50_mean"]
            if i["detect_ms_p50_mean"]
            else math.nan
        )
        lines.append(
            f"{int(users):,} & {_num(i['region_p50_mean'], 0)} & {fmt_ci(i['detect_ms_p50_mean'], i['detect_ms_p50_ci95'], 1)}"
            f" & {_num(f['region_p50_mean'], 0)} & {fmt_ci(f['detect_ms_p50_mean'], f['detect_ms_p50_ci95'], 1)}"
            f" & {_num(speed, 1)}$\\times$ \\\\"
        )
    body = (
        header
        + rf"""\begin{{table*}}[t]
\centering
\caption{{Per-batch detection latency (median, ms; mean $\pm$ 95\% CI over {df["seed"].nunique()} seeds)
and users examined per batch, incremental vs full recomputation, in-memory store.}}
\label{{tab:scale}}
\begin{{tabular}}{{rccccc}}
\toprule
& \multicolumn{{2}}{{c}}{{Incremental}} & \multicolumn{{2}}{{c}}{{Full}} & \\
Users & Region & ms & Region & ms & Speed-up \\
\midrule
{chr(10).join(lines)}
\bottomrule
\end{{tabular}}
\end{{table*}}
"""
    )
    return _write(output_dir, "tab3_scale.tex", body)


def _macro(name: str, value: str) -> str:
    return f"\\newcommand{{\\{name}}}{{{value}}}\n"


def _evasion_numbers(df: pd.DataFrame, names: dict[str, str]) -> list[str]:
    """Detected/total rings per evasion strategy, e.g. \\EvPropHubHiding = 35/52."""
    out = []
    for m, tag in names.items():
        acc: dict[str, list[int]] = {}
        for cell in df[df["method"] == m]["detected_by_evasion"].dropna():
            for mode, (hit, tot) in json.loads(cell).items():
                a = acc.setdefault(mode, [0, 0])
                a[0] += hit
                a[1] += tot
        for mode, (hit, tot) in sorted(acc.items()):
            mt = "".join(w.title() for w in mode.split("_"))
            out.append(_macro(f"Ev{tag}{mt}", f"{hit}/{tot}"))
    return out


ABLATION_TAGS = {
    "proposed": "Full",
    "no_hub_weight": "NoHubWeight",
    "no_time_decay": "NoTimeDecay",
    "no_incremental": "NoIncremental",
    "no_scoring": "NoScoring",
    "no_hysteresis": "NoHysteresis",
    "no_hub_aware": "NoHubAware",
    "no_cycle": "NoCycle",
    "no_smurfing": "NoSmurfing",
}


def _ablation_numbers(results_dir: Path, f2) -> list[str]:  # type: ignore[no-untyped-def]
    got = _results(results_dir, "e2_ablations")
    if not got:
        return []
    df, header = got
    out = [header]
    s = summarize(df, ["method"]).set_index("method")
    tests = paired_vs(df, "proposed", "f1").set_index("method")
    per = summarize(df, ["method", "scenario"], ("f1",))
    for m, tag in ABLATION_TAGS.items():
        if m not in s.index:
            continue
        out.append(_macro(f"Abl{tag}F", f2(s.loc[m, "f1_mean"])))
        out.append(_macro(f"Abl{tag}P", f2(s.loc[m, "precision_mean"])))
        out.append(_macro(f"Abl{tag}R", f2(s.loc[m, "recall_mean"])))
        if m in tests.index:
            out.append(_macro(f"Abl{tag}Pval", _p(tests.loc[m, "wilcoxon_p"])))
        for sc in ("hub_heavy", "high_noise", "adversarial", "baseline"):
            row = per[(per["method"] == m) & (per["scenario"] == sc)]
            if len(row):
                sc_tag = "".join(w.title() for w in sc.split("_"))
                out.append(_macro(f"Abl{tag}F{sc_tag}", f2(row["f1_mean"].iloc[0])))
    out.append(_macro("AblSeeds", str(df["seed"].nunique())))
    out.append(_macro("AblPairs", str(df.groupby(["scenario", "seed"]).ngroups)))
    return out


def _frontier_numbers(results_dir: Path, f2) -> list[str]:  # type: ignore[no-untyped-def]
    from fraudsim.experiments.plotting import pooled_pr

    got = _results(results_dir, "e3_frontier")
    if not got:
        return []
    df, header = got
    out = [header]
    tags = {"cap": "Cap", "hub": "Hub", "cap_scored": "CapS", "hub_scored": "HubS"}
    for fam, tag in tags.items():
        pts = []
        for method, g in df[df["family"] == fam].groupby("method", sort=False):
            pr, rc = pooled_pr(g)
            if pr is not None and rc is not None:
                f1 = 2 * pr * rc / (pr + rc) if pr + rc else 0.0
                pts.append((rc, pr, f1, method.split("_", 1)[1]))
        if not pts:
            continue
        top_r = max(pts, key=lambda x: (x[0], x[1]))
        best = max(pts, key=lambda x: x[2])
        out.append(_macro(f"Fr{tag}MaxR", f2(top_r[0])))
        out.append(_macro(f"Fr{tag}PAtMaxR", f2(top_r[1])))
        out.append(_macro(f"Fr{tag}BestF", f2(best[2])))
        out.append(_macro(f"Fr{tag}BestFP", f2(best[1])))
        out.append(_macro(f"Fr{tag}BestFR", f2(best[0])))
        out.append(_macro(f"Fr{tag}BestFAt", best[3]))
    out.append(
        _macro(
            "FrTrueRings",
            str(int(df.groupby("run_id")["true_rings"].first().sum() / df["method"].nunique())),
        )
    )
    return out


def _hub_size_numbers(results_dir: Path, f2) -> list[str]:  # type: ignore[no-untyped-def]
    got = _results(results_dir, "e5_hub_size")
    if not got:
        return []
    df, header = got
    out = [
        header,
        _macro("HubSizeMin", str(int(df["hub_size"].min()))),
        _macro("HubSizeMax", str(int(df["hub_size"].max()))),
    ]
    for m, tag in (
        ("proposed", "Prop"),
        ("static_cap10", "CapTen"),
        ("static_cap100", "CapHundred"),
        ("static_cap10_nocycle", "CapTenNoCycle"),
    ):
        g = df[df["method"] == m]
        if g.empty:
            continue
        hit = tot = 0
        for cell in g["detected_by_evasion"].dropna():
            d = json.loads(cell).get("hub_hiding")
            if d:
                hit, tot = hit + d[0], tot + d[1]
        out.append(_macro(f"HubSize{tag}Hidden", f"{hit}/{tot}"))
        out.append(_macro(f"HubSize{tag}F", f2(g["f1"].fillna(0).mean())))
    return out


def _sweep_numbers(results_dir: Path, f2) -> list[str]:  # type: ignore[no-untyped-def]
    got = _results(results_dir, "e6_sweeps")
    if not got:
        return []
    df, header = got
    df = df.assign(f1=df["f1"].fillna(0))
    out = [header]
    prop = df[df["method"].str.startswith("proposed")]
    for sweep, tag in (("event_rate", "Rate"), ("ring_size", "RingSize"), ("window_s", "Window")):
        g = prop[prop["sweep"] == sweep]
        if g.empty:
            continue
        lo, hi = g["value"].min(), g["value"].max()
        out.append(_macro(f"Sw{tag}Lo", f"{lo:g}"))
        out.append(_macro(f"Sw{tag}Hi", f"{hi:g}"))
        for v, vt in ((lo, "Lo"), (hi, "Hi")):
            gg = g[g["value"] == v]
            out.append(_macro(f"Sw{tag}{vt}F", f2(gg["f1"].mean())))
            out.append(_macro(f"Sw{tag}{vt}Lat", _num(gg["detect_ms_p50"].mean())))
    return out


def _adaptivity_numbers(results_dir: Path) -> list[str]:
    got = _results(results_dir, "e7_adaptivity")
    if not got:
        return []
    df, header = got
    out = [header, _macro("AdaptSeeds", str(df["seed"].nunique()))]
    for fb, tag in ((False, "Off"), (True, "On")):
        g = df[df["feedback"] == fb]
        out.append(_macro(f"Adapt{tag}Rings", str(int(g["adaptive_rings"].sum()))))
        out.append(_macro(f"Adapt{tag}Found", str(int(g["adaptive_detected"].sum()))))
        out.append(_macro(f"Adapt{tag}Switches", str(int(g["strategy_switches"].sum()))))
        out.append(_macro(f"Adapt{tag}R", f"{g['recall'].mean():.2f}"))
    return out


def generate_numbers(output_dir: Path, results_dir: Path = Path("results")) -> Path | None:
    """numbers.tex: every number quoted in the prose, as a macro."""
    out = ["% Numbers quoted in the paper text, generated from results/*/results.csv.\n"]

    def f2(x: float) -> str:
        return "--" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.2f}"

    got = _results(results_dir, "e1_baselines")
    if got:
        df, header = got
        out.append(header)
        s = summarize(df, ["method"]).set_index("method")
        names = {
            "proposed": "Prop",
            "proposed_default": "PropDefault",
            "static_cap10": "CapTen",
            "static_cap100": "CapHundred",
            "louvain": "Louvain",
            "plain_cc": "PlainCC",
        }
        for m, tag in names.items():
            if m in s.index:
                for metric, mt in (("precision", "P"), ("recall", "R"), ("f1", "F")):
                    out.append(_macro(f"E{tag}{mt}", f2(s.loc[m, f"{metric}_mean"])))
                out.append(_macro(f"E{tag}FCI", f2(s.loc[m, "f1_ci95"])))
                out.append(_macro(f"E{tag}Lat", _num(s.loc[m, "detect_ms_p50_mean"])))
                out.append(_macro(f"E{tag}FullLat", _num(s.loc[m, "full_graph_ms_p50_mean"])))
                out.append(_macro(f"E{tag}TTD", _num(s.loc[m, "ttd_p50_s_mean"])))
        out += _evasion_numbers(df, names)
        out.append(_macro("ESeeds", str(df["seed"].nunique())))
        out.append(_macro("EScenarios", str(df["scenario"].nunique())))
        out.append(_macro("ERuns", str(len(df))))
    got = _results(results_dir, "e4_scale")
    if got:
        df, header = got
        out.append(header)
        top = df["users"].max()
        g = df[df["users"] == top]
        inc = g[g["mode"] == "incremental"]["detect_ms_p50"].mean()
        full = g[g["mode"] == "full"]["detect_ms_p50"].mean()
        out.append(_macro("ScaleMaxUsers", f"{int(top):,}"))
        out.append(_macro("ScaleIncMs", _num(inc)))
        out.append(_macro("ScaleFullMs", f"{full:,.0f}"))
        out.append(_macro("ScaleSpeedup", f"{full / inc:,.0f}" if inc else "--"))
    out += _ablation_numbers(results_dir, f2)
    out += _frontier_numbers(results_dir, f2)
    out += _hub_size_numbers(results_dir, f2)
    out += _sweep_numbers(results_dir, f2)
    out += _adaptivity_numbers(results_dir)
    tuned = Path("configs/tuned.json")
    if tuned.exists():
        t = json.loads(tuned.read_text(encoding="utf-8"))
        out.append(f"% from {tuned.as_posix()}\n")
        out.append(_macro("TunedAlert", str(t["alert_threshold"])))
        out.append(_macro("TunedEdge", str(t["edge_threshold"])))
        out.append(_macro("TunedValF", f2(t["mean_f1"])))
        out.append(_macro("TunedDefaultValF", f2(t["grid"].get("tune_a0.5_e0.5", math.nan))))
        out.append(_macro("TunedGridSize", str(len(t["grid"]))))
    return _write(output_dir, "numbers.tex", "".join(out))


TABLES = (
    generate_baseline_table,
    generate_scenario_table,
    generate_ablation_table,
    generate_scale_table,
    generate_numbers,
)


def export_all(results_dir: Path, output_dir: Path) -> list[Path]:
    return [p for fn in TABLES if (p := fn(output_dir, results_dir)) is not None]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--output-dir", type=Path, default=Path("docs/paper/tables"))
    args = parser.parse_args()
    export_all(args.results_dir, args.output_dir)


if __name__ == "__main__":
    main()
