# Reproducing the paper

Every table and figure in `docs/paper/` is generated from `results/*/results.csv`;
nothing is typed in by hand. This page lists the commands, in order.
(`make` targets are given where they exist; on Windows without `make`, run
the command shown next to it.)

## 0. Setup

```bash
python -m pip install -e ".[dev]"
```

Python 3.11, no database needed (all experiments use the in-memory graph store).
Neo4j (Docker) is only needed for `--backend neo4j` runs and the live demo's Neo4j mode.

## 1. Scorer (already trained, in `models/`)

```bash
python -m fraudsim.cli train-scorer --model logistic_regression --train-seeds 101-110 --test-seeds 201-205
```

## 2. Threshold selection on validation seeds 301-303

```bash
python -m fraudsim.cli experiment --suite tune --output-dir results/
```

Writes `results/tuning/results.csv` and `configs/tuned.json` (the selected
alert and edge thresholds, the whole grid of validation F1 scores, the seeds
and scenarios). The committed `configs/tuned.json` was produced this way.

## 3. Experiments on test seeds (1001+)

```bash
python scripts/run_paper_experiments.py          # all of them, in order
python scripts/run_paper_experiments.py frontier # or one
```

| Experiment | Output | Seeds | What it answers |
|---|---|---|---|
| `baselines` (E1) | `results/e1_baselines/` | 10 | accuracy and latency vs. static Cypher, Louvain, connected components |
| `ablations` (E2) | `results/e2_ablations/` | 5 | contribution of each component |
| `frontier` (E3) | `results/e3_frontier/` | 5 | degree cap vs. hub weighting on shared-attribute rings |
| `hub_size` (E5) | `results/e5_hub_size/` | 5 | recall of a ring hiding behind a growing hub |
| `sweeps` (E6) | `results/e6_sweeps/` | 5 | event rate, ring size, window length |
| `adaptivity` (E7) | `results/e7_adaptivity/` | 10 | adaptive attacker with vs. without detection feedback |
| `scale` (E4) | `results/e4_scale/` | 3 | per-batch latency, incremental vs. full, as the graph grows |

Every row carries `run_id`, `scenario_hash`, `method_hash` and `stream_digest`
(the digest of the exact event stream), so any number can be traced to a run
and re-run. `manifest.json` next to each CSV lists the methods' full configurations.
On the test laptop (Core i3-1115G4, 7.7 GB) the tuning grid took about 1.5 h (interrupted once and completed), the suite about 35 min, and the 100,000-user scale point another 30 min.

## 4. Tables, figures, paper

```bash
make tables     # python -m fraudsim.experiments.latex_export --results-dir results/ --output-dir docs/paper/tables/
make figures    # python -m fraudsim.experiments.plotting --results-dir results/ --output-dir docs/paper/figures/
python scripts/make_architecture.py   # architecture.drawio -> architecture.pdf/.png
make paper      # pdflatex + bibtex in docs/paper/ -> paper.pdf
```

Each generated table starts with a comment naming the CSV it came from and that
file's sha256. `docs/paper/tables/numbers.tex` holds every number quoted in the
text as a LaTeX macro.

## 5. Public datasets (optional)

See [docs/datasets.md](docs/datasets.md).

```bash
python -m fraudsim.cli public --dataset amlworld
```

## Determinism

The simulator and the detectors are deterministic for a given scenario file and
seed: the same `stream_digest` and the same flagged rings come out on every run
(`tests/test_simulator.py` and `tests/test_detectors.py` check this, including across separate processes). Latency columns are
wall-clock measurements and vary with the machine and its load.
