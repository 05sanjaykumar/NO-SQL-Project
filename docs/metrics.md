# Metrics (Milestone 5)

`fraudsim.metrics.evaluation.evaluate_run` scores one detection run against
the ground-truth ledger. Every `fraudsim detect` run writes the results to
`runs/<run_id>/metrics/`, and the live API serves them for the current run.

## Definitions

| Term | Definition |
|---|---|
| Flag | What an analyst would see. With a risk model, the `alert` event (CONFIRMED and risk ≥ threshold); without one, the `confirmed` status change. The member set is the one shown at that moment |
| True ring | A ground-truth ring that became ACTIVE during the run. Its members are every account it ever had |
| Match | Jaccard(flag members, true ring members) ≥ 0.5. Matching is one-to-one and greedy, using each tracked ring's first flag. Results at 0.3 and 0.8 are reported as `sensitivity` |
| Ring precision | Flagged rings that match some true ring, over all flagged rings |
| Ring recall | True rings matched in time (before they dissolved), over all true rings |
| User precision / recall | Accounts shown in any flag vs every member of any true ring |
| Time-to-detect | First matching flag − ring activation, in simulated seconds. Negative means flagged while still forming. Reported as p50/p90/p95/p99 |
| Early-warning lead | Activation − first early warning or alert on a tracked ring that holds at least two members and is ≥ 66% fraud (`scoring/early_warning.py`) |

### Missed-ring reasons

The first matching category applies:

| Reason | Meaning |
|---|---|
| `late` | Flagged only after the ring dissolved |
| `below_threshold` | A tracked ring matched it but never alerted, and was still live at the end |
| `aged_out` | A tracked ring matched it but was dissolved (evidence decayed or was pruned) without a flag |
| `hidden_behind_hub` | Nothing matched it, and it used hub hiding or its members share an attribute with ≥ 20 users |
| `blind_spot` | No detector produced anything matching it |

### Triage

Every scored tracked ring is ranked by its peak risk. The triage metrics are:
- precision@5, @10 and @20;
- PR-AUC, as average precision normalised by the number of true rings, so
  missed rings cost;
- alerts per simulated hour;
- a false-alert cost proxy of 15 analyst minutes per false alert;
- a missed-ring cost proxy of 240 minutes per missed ring.

The proxies are configurable in `EvalConfig`.

### Latency, throughput and resources

These come from each batch's `BatchReport`, measured in wall-clock time.

| Metric | Definition |
|---|---|
| `ingest_per_event` | Graph-write time ÷ events in the batch |
| `detect_per_batch` | dirty set + view + detectors + tracking + scoring |
| `batch_total` | Everything one micro-batch takes |
| `event_to_flag_processing` | Total processing time of the batches that raised a flag. This is the processing part of event-to-flag latency; batching wait is excluded in headless runs |
| `throughput.events_per_s` | Events ÷ total engine processing time |
| `store_queries` | Cypher round trips per batch (Neo4j) |
| `db_hits_sampled` | Database hits from queries run under PROFILE (`Neo4jGraphStore(profile_every=N)`) |
| Graph size and memory | Graph size (`graph_*`) and process RSS (`rss_mb`), sampled every `stats_interval_s` (10 simulated seconds) |

`fraudsim compare-latency --scenario S --seed N [--backend neo4j]` runs the same
stream incrementally and with full recomputation. Attacker feedback is off,
so the event streams are identical; the command reports both sides.

### Rolling and cumulative

`timeseries.csv` has one row every 10 simulated seconds:
- cumulative true-active rings, detected rings, recall, false rings and
  precision;
- the same counts over a rolling 30-second window.

## Outputs

`runs/<run_id>/metrics/` contains:

| File | Contents |
|---|---|
| `summary.json` | Every number above |
| `rings.csv` / `.parquet` | One row per true ring: detected, flag time, Jaccard, time-to-detect, miss reason |
| `flags.csv` / `.parquet` | One row per flag |
| `timeseries.csv` / `.parquet` | Cumulative and rolling series |
| `batches.csv` / `.parquet` | Per-batch timings, region size, queries, DB hits, graph size, RSS |
| `metrics.prom` | The summary in Prometheus text format |

The API serves:
- `GET /metrics/summary`: the headline numbers of the live run, plus the full
  summary. Values are `null` when nothing is measurable yet.
- `GET /metrics`: Prometheus format. Every numeric leaf of the summary appears
  as `fraudsim_run_*{run_id="..."}`.

Live accuracy uses the simulator's ground truth (an oracle). That is
appropriate for a simulation dashboard but would not exist in production.

## Milestone 5 check runs (single seed, engineering checks only)

**Adversarial, seed 42.** Logistic model, closed loop, in-memory store.
- Ring level: precision 0.71, recall 0.56 (5 of 9 rings caught, 2 false
  rings).
- User level: precision 0.86, recall 0.50.
- Misses: below_threshold 1 (the hub-hiding ring was tracked but never
  alerted), hidden_behind_hub 1, blind_spot 2 (low-and-slow,
  identity-rotation).
- Time-to-detect: p50 7.9 s.
- Early warnings: none before activation (lead p50 −2.1 s).

**`compare-latency`, hub_heavy, seed 42** (no risk model; the static and
community baselines are included). Runs `c0ff649109f2` (incremental) and
`21ccb9d2be92` (full), on stream digest `093c09a22e5f`.
- Detection per batch: incremental p50 53 ms vs full 41 ms.
- Median region: 112 users either way. With 100 users the dirty region is the
  whole graph (see `docs/detection.md` for the scaling behaviour).
- Accuracy is identical in both modes.

The multi-seed results with confidence intervals for the paper come from
Milestone 8.
