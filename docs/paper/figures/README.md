# Figures

Generated, never drawn by hand. Each figure is computed from a committed
results file; rows there carry `run_id`, scenario hash, method hash and
event-stream digest.

| Figure | Source | Command |
|---|---|---|
| `fig1_pr_frontier` | `results/e3_frontier/results.csv` | `make figures` |
| `fig2_latency_scaling` | `results/e4_scale/results.csv` | `make figures` |
| `fig3_ablation` | `results/e2_ablations/results.csv` | `make figures` |
| `fig4_hub_size` | `results/e5_hub_size/results.csv` | `make figures` |
| `fig5_evasion` | `results/e1_baselines/results.csv` | `make figures` |
| `fig6_sweeps` | `results/e6_sweeps/results.csv` | `make figures` |
| `architecture` | `architecture.drawio` (edit in draw.io / diagrams.net) | `python scripts/make_architecture.py` |

`make figures` is `python -m fraudsim.experiments.plotting --results-dir results/ --output-dir docs/paper/figures/`.
With the draw.io desktop app, an exact export of the diagram is
`drawio -x -f pdf -o architecture.pdf architecture.drawio`.
