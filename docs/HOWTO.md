# How to run the demo, the simulation, and regenerate the paper

Every step lists the `make` target and the plain command it runs. `make` is
not installed by default on Windows, so use the plain commands there.

## 0. Install (once)

Requirements: Python 3.11; Node.js 18 or newer for the dashboard; Docker
Desktop only if you want Neo4j, Redis, or everything in containers.

```bash
pip install -e ".[dev,redis]"
```

## 1. Live dashboard demo

**Option A, on your machine (recommended for presenting):**

```bash
make demo
```

which is:

```bash
cd web && npm ci && npm run build && cd ..        # once, and after web/ changes
python -m fraudsim.cli api --port 8000
```

Open <http://localhost:8000/ui/>. The default backend is the in-memory store, so
nothing else needs to run. For the Neo4j backend, first start the databases
with `docker compose up -d` (Neo4j on :7474/:7687, Redis on :6379).

**Option B, everything in containers:**

```bash
make demo-docker          # docker compose --profile app up --build
```

then open <http://localhost:8000/ui/>.

**A five-minute walkthrough**

1. Pick scenario `demo_story`, seed `7`, press **Start**. The graph grows; a
   busy office hub (grey) appears with many benign users.
2. Rings appear as red circles of transfers, and alerts enter the queue ranked
   by calibrated risk.
3. Click an alert. The drawer shows why the ring was flagged, the feature
   contributions, the counterfactual ("what would have to change") and the
   evidence subgraph. **Confirm fraud** or **Dismiss**, which adapts the threshold and
   is written to the audit trail.
4. Tick **Oracle view** to see the answer key: correct flags, false alarms
   and rings that were missed (some evasive rings are missed on purpose).
5. Use **Inject ring** (for example `shared_ip` with `hub_hiding`) and watch
   it get caught, or switch the hub-aware detector off and watch it get missed.

The one-page offline version, an animated replay comparing the degree-capped
rules with the hub-aware detector on the same events, is:

```bash
make demo-story           # python -m fraudsim.cli demo-story --seed 7 --out demo/ --open
```

## 2. Running the simulation and detection

```bash
python -m fraudsim.cli simulate --scenario adversarial --seed 7      # events + ground truth -> runs/<id>/
python -m fraudsim.cli detect   --scenario adversarial --seed 42     # simulate + detect + score + metrics
python -m fraudsim.cli detect   --scenario scale_sweep --backend neo4j   # same on Neo4j (docker compose up -d)
python -m fraudsim.cli replay   runs/<run_id>                        # must reproduce the run exactly
python -m fraudsim.cli audit-verify runs/<run_id>                    # check the audit hash chain
```

Scenarios are in `configs/scenarios/`: `baseline`, `high_noise`, `hub_heavy`,
`adversarial`, `drift`, `demo_story`, `scale_sweep`. A `detect` run writes
events, detections, metrics (`metrics/summary.json`, CSV/Parquet, Prometheus
text), the audit trail and a manifest with the seed, the config hash and the
event-stream digest.

## 3. Reproducing the experiments

The exact commands, seeds and timings are in [REPRODUCE.md](../REPRODUCE.md).
In short:

```bash
python -m fraudsim.cli experiment --suite tune --output-dir results/   # validation seeds -> configs/tuned.json
python scripts/run_paper_experiments.py                               # all test-seed experiments -> results/
```

`make experiment-all` runs both. On the test laptop, tuning took about 1.5 h
and the experiments about 1 h. The committed `results/` already holds the runs
the paper reports.

## 4. Regenerating the paper's artefacts

| Artefact | Command | Output |
|---|---|---|
| Tables and the numbers quoted in the text | `make tables` (`python -m fraudsim.experiments.latex_export --results-dir results/ --output-dir docs/paper/tables/`) | `docs/paper/tables/*.tex` |
| Plots | `make figures` (`python -m fraudsim.experiments.plotting --results-dir results/ --output-dir docs/paper/figures/`) | `docs/paper/figures/fig*.pdf/.png` |
| Architecture diagram | `python scripts/make_architecture.py` | `docs/paper/figures/architecture.drawio` (+ `.pdf`, `.png`) |
| Paper PDF | `make paper` (pdflatex + bibtex in `docs/paper/`) | `docs/paper/paper.pdf` |

The architecture diagram's source is `architecture.drawio`; open it in draw.io
(desktop app or <https://app.diagrams.net>) to edit it. The script writes the
`.drawio` file and the PDF/PNG from one list of shapes. After editing in
draw.io, export with:

```bash
drawio -x -f pdf -o docs/paper/figures/architecture.pdf docs/paper/figures/architecture.drawio
```

Building the PDF needs a LaTeX distribution with IEEEtran (MiKTeX or TeX Live).
Every table records the CSV it was made from and that file's sha256, and CI
checks that the committed tables match the committed results.

## 5. Public datasets (optional)

See [datasets.md](datasets.md): Elliptic and the IBM AMLSim samples are fetched
by `python scripts/fetch_datasets.py elliptic|amlsim`. IBM AMLworld needs a free
Kaggle account: put `HI-Small_Trans.csv` and `HI-Small_Patterns.txt` in
`data/raw/amlworld/`. Then:

```bash
python -m fraudsim.cli public --dataset amlworld --start-day 0 --days 4 --window-s 3600
```

## 6. Tests and CI

```bash
make test-unit        # pytest -m "not integration and not slow"
make lint             # ruff check + format check
pytest -m integration # needs docker compose up -d (Neo4j with GDS, Redis)
```

GitHub Actions (`.github/workflows/ci.yml`) runs lint, unit tests, the Neo4j and
Redis integration tests, a one-seed smoke experiment that regenerates the tables
and figures, the dashboard build, and the all-in-one Docker image.

## 7. Troubleshooting

- **Port 8000 already in use:** another API is running (for example the
  `app` container). Stop it, or start the API with `--port 8001`.
- **`npm` not found:** install Node.js 18+ and reopen the terminal.
- **Neo4j runs fail:** `docker compose up -d`, wait until <http://localhost:7474>
  answers, then retry. The in-memory backend needs nothing.
- **Scale runs are slow or run out of memory:** the 100,000-user point needs
  about 1 GB of free memory; smaller sizes run on any laptop.
