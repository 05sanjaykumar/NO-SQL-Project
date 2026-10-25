# Live dashboard (Milestone 7)

The dashboard is React + Vite + TypeScript, with Cytoscape.js for the graph and
Recharts for the charts. FastAPI serves the built app at `/ui`, and live data
arrives over the API's WebSocket `/ws`.

```bash
make demo            # npm ci + build, then the API on :8000 -> open http://localhost:8000/ui/
make web-dev         # hot-reload dev server on :5173 (run `make api` in another terminal)
```

Node 18+ is needed to build. The production build (`web/dist`) is not committed.

| Live view | Oracle view (answer key) |
|---|---|
| ![Live](images/dashboard_live.jpg) | ![Oracle](images/dashboard_oracle.jpg) |

![Ring drawer](images/dashboard_oracle_drawer.jpg)

These screenshots come from the `demo_story` scenario at seed 7, taken in a
real browser against the running API.

## Panels

| Panel | Contents |
|---|---|
| Controls | Scenario, seed, start/pause/resume/stop, speed (1×–25×, max); inject a ring of any type and evasion mode; per-detector on/off switches; alert threshold, hub edge threshold and hub-weighting method (applied live, from the next micro-batch); Oracle toggle; export PNG/SVG |
| Transaction graph | Windowed graph with level of detail (see below). Users, devices, cards and IPs have their own shapes; busy hubs are large and labelled; transfers inside a ring are red arrows that flash when they happen. Nodes fade in and glide to their slot as rings form, grow and dissolve. Click a flagged user to open its ring |
| Scoreboard | Rings detected, missed and false; precision, recall and F1; time-to-detect p50; graph size. The numbers come from `evaluate_run` against ground truth and are pushed once a second. In Oracle view it also lists each true ring as caught (with its delay) or missed (with the reason) |
| Alert queue | Live rings ranked by risk, alerts first; in Oracle view each has a true-ring or false-alarm badge |
| Latency chart | Rolling p50, p95 and p99 of detection time per batch (wall clock) |
| Throughput chart | Events per second, active users, and users re-evaluated per batch (the dirty region) |
| Event log | Latest events, plus ring notices (alerts, early warnings, confirmations, injections) |
| Ring drawer | Calibrated risk, threshold and calibration bucket; plain-English reason; counterfactual; feature-contribution bars; evidence subgraph; status timeline; confirm/dismiss. Confirm/dismiss adapts the threshold and is written to the audit trail |

### Graph level of detail

The graph is fetched from `GET /graph` every 1.2 seconds. It draws, in this
priority:
1. members of live tracked rings (and, in Oracle view, of true rings);
2. up to 25 users per busy hub (≥ 10 users); the hub node shows its full degree;
3. recent benign users, only while the picture is still sparse.

Attributes are drawn only when they link two drawn users or are hubs. The
header shows how many active users are drawn out of the total.

The layout is deterministic. Groups (hubs, then rings) sit on a spiral, and
members sit on a circle around their group's centre.

## API used by the dashboard

| Endpoint | Purpose |
|---|---|
| `GET /scenarios`, `GET /catalog` | Scenario list; ring types and evasion modes |
| `POST /runs/start` · `pause` · `resume` · `stop` · `speed` · `rate` | Run control |
| `POST /runs/inject {ring_type, evasion, size}` | Spawn a ring now (logged in the generator's control log) |
| `GET /detectors`, `POST /detectors/{type} {enabled}` | Detector switches (audited as `CONFIG_CHANGED`) |
| `GET/POST /config {alert_threshold, edge_threshold, hub_weight_method}` | Live tuning (audited) |
| `GET /graph?max_users&window_s&oracle` | Windowed snapshot, plus the answer key when `oracle=true` |
| `GET /rings`, `GET /rings/{id}`, `POST /rings/{id}/feedback` | Ring list and detail; analyst feedback |
| `GET /metrics/summary`, `GET /metrics` | Live metrics (JSON, Prometheus) |
| `WS /ws` | `BATCH` (timings, throughput, graph size, latest events), `RING_UPDATE`, `METRICS`, `RING_INJECTED`, `CONFIG`, run lifecycle |

The Oracle view uses the simulator's ground truth, so it is a demonstration
aid. It is off by default.

Changing the configuration during a run, or injecting rings, changes the run.
Replay still reproduces such a run, because injected rings are part of the
logged event stream and config changes are in the audit trail. The one
limitation is described in `docs/logging.md`: replay does not yet re-apply
threshold or config changes made mid-run.
