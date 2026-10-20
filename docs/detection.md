# Incremental detection engine (Milestone 3)

## Pipeline

For every micro-batch of events (`EngineConfig.batch_size`, default 50):

1. **ingest**: write the batch to the graph store. On Neo4j this is batched
   UNWIND in one transaction, with event order preserved.
2. **dirty**: the users the batch touched, expanded `k_hop` user-hops (default 2).
   One hop means sharing an attribute or a transaction inside the window.
   Attributes with more than `max_fanout` active users (default 250) are not
   traversed.
3. **view**: a snapshot of that region. It holds every link of the region's
   users, each linked attribute's *global* active degree, and the transactions
   inside the region within `window_s` (default 900 s).
4. **detect**: every enabled incremental detector runs on the view.
5. **track**: candidates are merged across detectors and folded into the
   `RingTracker`, which assigns stable ring ids and applies hysteresis.

Full-graph detectors (the static Cypher baseline and GDS community detection)
run every `full_interval_s` (default 30 simulated seconds) on the whole graph,
the way they would be deployed. Every `sweep_interval_s` the members of live
tracked rings are added to the dirty set, so time decay and pruning are
re-evaluated. With `ttl_s`, links and transactions older than the TTL are pruned
every `prune_interval_s`, which keeps the graph bounded on long runs.

`EngineConfig(incremental=False)` is the "full recomputation" ablation: the
incremental detectors evaluate the whole graph on every batch. Each stage is
timed per batch (`BatchReport.timings_ms`, `detector_ms`, `store_queries`).

## Graph stores

| Store | Use |
|---|---|
| `Neo4jGraphStore` | Production path. Expansion, views and pruning run in Cypher |
| `InMemoryGraphStore` | Same semantics in Python. Used for unit tests, large sweeps, and as the reference |

`tests/test_integration_m3.py` checks against a live Neo4j that both stores
return identical expansions, views, stats and pruning results. It also checks
that the whole engine produces identical rings on both stores.

## Detectors

Every detector has a config dataclass with `enabled`, so each one is a single
ablation flag (`build_detectors(store, {"community": {"enabled": False}})`, or
`fraudsim detect --disable community`).

| Detector | Mode | What it flags |
|---|---|---|
| `StaticCypherBaseline` | full, timer | Original Q1 (device ≥3), Q2 (card ≥3), Q3 (3-cycle) and Q4b (IP ≥3 and < cap). `max_degree_cap` 10 or 100 reproduces Q4b/Q4a. On Neo4j it runs the original Cypher; departed users are excluded |
| `HubAwareSharedAttribute` (C1) | incremental | Dense groups in a hub-weighted user-user projection (below) |
| `TemporalCycleDetector` | incremental | Directed cycles of length 3–6, time-ordered within `time_window_s`, each hop passing on 90–102% of what it received |
| `SmurfingDetector` | incremental | ≥ `min_fan_count` distinct counterparties sending to (fan-in) or receiving from (fan-out) one account within `time_window_s`, each transfer in [0.8 × threshold, threshold) |
| `CommunityDetector` | full, timer | GDS Louvain or WCC (`algorithm="wcc"` is the plain connected-components baseline) on an unweighted shared-attribute + transaction projection. Communities of size 3–25 with edge density ≥ 0.5 are flagged. The in-memory path uses networkx |

### Hub-aware weighting (C1)

For users *u*, *v* in the region:

```
W(u,v) = Σ_a h(deg a) · decay(now − min(t_u,a, t_v,a))
       + min(0.6, β · Σ_txn(u↔v) decay(now − t_txn))        (only if they share an attribute)

h(d)      = 1 / ln(1 + d)    (alternatives: adamic_adar, inverse_degree; "none" = ablation)
decay(Δ)  = 2^(−Δ / half_life)   (half_life 300 s; None = ablation)
β         = 0.15
```

A device shared by 3 users weighs 0.72, while an office IP shared by 50 weighs
0.25. Edges with W ≥ 0.5 form the projection. Each connected component is
peeled, by repeatedly removing its weakest member, until its edge density is
≥ 0.75 and its size is in [3, 25]. The cores become candidates. A ring hiding
on a busy hub is therefore not flagged for sharing the hub alone. It surfaces
when it also moves money internally, so its pairs accumulate weight that
benign hub users do not. A hard degree cap can never recover such a ring.

## Ring identity and status

`merge_candidates` unions candidates from different detectors when their
Jaccard similarity is ≥ 0.5, or when ≥ 80% of the smaller set lies inside the
larger one.

The `RingTracker` then matches unified candidates to live rings by greedy
best Jaccard (≥ 0.5) and records each change:

| Change | Meaning |
|---|---|
| `created` | A new ring id (`R000001`, ...) |
| `grew` / `shrank` | The member set changed. The id is kept |
| `merged_into` | A live ring is now mostly inside another ring |
| `split_from` | A new ring that overlaps a live ring (Jaccard ≥ 0.2) |
| `confirmed` | CANDIDATE → CONFIRMED after `hysteresis_confirm` (2) consecutive detections |
| `dissolved` | → DISSOLVED after `hysteresis_dissolve` (3) consecutive misses |

A miss only counts when a detector that previously found the ring evaluated a
region containing its members.

Confirmed members are reported back to the simulator
(`EventGenerator.notify_flagged`), which closes the loop with the adaptive
attacker.

## Running it

```bash
fraudsim detect --scenario adversarial --seed 42                  # in-memory store
fraudsim detect --scenario scale_sweep --seed 42 --backend neo4j  # live Neo4j
fraudsim detect --scenario hub_heavy --full                       # full-recompute ablation
fraudsim detect --scenario adversarial --disable community --disable static_cypher
```

Each run writes `runs/<run_id>/` containing `events.jsonl`, `ground_truth*.jsonl`,
`batches.jsonl` (per-batch timings and region sizes), `ring_updates.jsonl`,
`rings.jsonl` and `manifest.json` (config hash, engine and detector config).

The live API runs the same engine on the stream. It sends `RING_UPDATE` and
`BATCH` WebSocket messages, and `GET /rings` and `/rings/{id}` serve the
tracked rings.

## Milestone 3 latency checks (2026-10-20)

These are preliminary engineering checks on one seed and one machine (Windows,
Neo4j 5.26 community + GDS in Docker Desktop). The paper's RQ2 numbers come
from the Milestone 8 harness with multiple seeds and confidence intervals.
"Detect" means dirty-set expansion + region view + the three incremental
detectors (or the full view + the same detectors in full mode). It excludes
ingest and the timer-driven baselines.

**Neo4j, `scale_sweep` (1,000 users, 100 events/s), seed 42, batch 50**, first
60 simulated seconds (222 batches each, same event stream):

| Mode | run_id | Region users (p50) | Detect p50 | Detect p95 |
|---|---|---|---|---|
| incremental | `8e2de49873f5` (first 222 batches) | 966 | 731 ms | 5,758 ms |
| full recompute | `0a3f22bd2960` | 1,011 | 881 ms | 2,491 ms |

At this size, one hop from the ~80 users a batch touches (shared hubs, plus
the payment partners of heavy-tailed users) already reaches nearly the whole
graph. So incremental evaluation has no advantage, and its extra Cypher round
trips worsen the tail.

Over the full incremental run (`8e2de49873f5`, 354 batches), ingest (the Neo4j
write) took p50 109 ms and p95 1,296 ms per 50 events.

**In-memory store, same scenario scaled to N users (devices, cards and IPs
scaled proportionally, 20 simulated seconds), seed 42.** Measured on the
streaming batches in the second half of the run:

| N users | Mode | Region users (p50) | Detect p50 | Detect p95 |
|---|---|---|---|---|
| 1,000 | incremental | 980 | 417.6 ms | 724.6 ms |
| 1,000 | full | 1,021 | 148.5 ms | 205.2 ms |
| 5,000 | incremental | 1,864 | 263.8 ms | 467.9 ms |
| 5,000 | full | 5,012 | 579.5 ms | 1,079.2 ms |
| 20,000 | incremental | 1,343 | 48.1 ms | 168.8 ms |
| 20,000 | full | 20,005 | 2,113.5 ms | 4,376.0 ms |

The dirty region stays at one to two thousand users while the graph grows, so
incremental detection overtakes full recomputation somewhere between 1k and 5k
users. At 20k users it is about 44× faster at the median.

How these were produced:
- The 1k and 5k rows came from `scripts/m3_checks/scaling.py N inc|full 20`.
- The 20k rows came from `scripts/m3_checks/scaling_tail.py 20000 inc|full 20`.
  It loads the first 10 s straight into the store, because running full
  recompute over the 20k-user bootstrap did not finish within 15 minutes.
- Stream digests: 1k `2b4ff3ae834d`, 5k `51711e6572b5`, 20k `1ee99a30e174`.
- The Neo4j tables came from `scripts/m3_checks/latency_summary.py <run_id>`
  over `runs/<run_id>/batches.jsonl`. The run directories are not committed;
  `fraudsim detect --scenario scale_sweep --seed 42 --backend neo4j [--full --duration 60]`
  regenerates them.

Accuracy metrics (precision, recall, time-to-detect) are Milestone 5 and are
not reported here.
