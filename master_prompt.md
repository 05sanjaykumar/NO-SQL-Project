# ROLE
You are a senior research engineer and applied-ML researcher. Extend the existing repo (Neo4j fraud-ring detection, course project BCSE406L) into a research-grade, paper-ready system: a live streaming simulation, an incremental fraud-ring detector with explanations, rigorous metrics, and a reproducible experiment harness.

Repo Link: https://github.com/au-revoir777/NO-SQL-Project

# WORKFLOW RULES
1. First read the whole repo (README.md, SETUP.md, docs/PLAN.md, seed.py, queries.cypher, docker-compose.yml). Then write an implementation plan artifact with milestones, and proceed without waiting for me unless blocked. State assumptions explicitly instead of asking questions.
2. Work in the milestones below, in order. After each milestone: run it, run the tests, and fix failures before moving on. Commit per milestone with a clear message.
3. NEVER fabricate results. Every number in docs, tables and figures must come from an actual run and be traceable to a run_id, config and seed.
4. Do not delete existing files. Keep the existing queries (Q1, Q2, Q3, Q4a, Q4b) and wrap them as the "StaticCypherBaseline" detector so they remain a comparison baseline.
5. Everything runs with `docker compose up` plus `make` targets. Python 3.11, type hints, pytest, ruff.

# EXISTING STATE (do not regress)
- Data model: (User)-[:USED_DEVICE]->(Device), (User)-[:HAS_CARD]->(CreditCard), (User)-[:LOGGED_IN_FROM]->(IPAddress), (User)-[:PERFORMED]->(Transaction)-[:SENT_TO]->(User).
- Planted patterns: shared device ring, shared card ring, 3-cycle money flow, shared IP ring, and a legitimate hub (12 users on one office IP) that must NOT be flagged.
- Existing finding to build on: a hard degree cap (100 vs 10) moved precision 20% to 100%, kept recall at 100% on that dataset, and cut query time 302 ms to 74 ms, but it would drop real rings hiding behind busy nodes.

# RESEARCH CONTRIBUTION (design everything to support these claims)
Working title: "Hub-Aware Incremental Fraud Ring Detection on Streaming Transaction Graphs."
- C1: Replace the hard degree cap with continuous hub-aware edge weighting (inverse-degree / IDF / Adamic-Adar style, with time decay), so rings behind busy nodes can still be recovered while office-wifi-style hubs are suppressed.
- C2: Incremental detection that re-evaluates only the neighbourhood touched by each event, with measured latency against full recomputation.
- C3: Explainable, auditable ring flags: additive per-feature contribution scores, evidence subgraph, and counterfactuals.
- C4: Adversarial evaluation: an adaptive attacker that places rings behind hubs, goes low-and-slow, and mixes attribute types.
Research questions: RQ1 accuracy vs baselines; RQ2 latency/throughput vs graph size; RQ3 robustness to evasion; RQ4 explanation quality; RQ5 ablation of each component.

# MILESTONE 1: Architecture and repo layout
Create:
  src/fraudsim/
    simulator/   (event generator, scenario loader, ground-truth ledger)
    stream/      (async event bus, pluggable: in-process asyncio by default, Redis Streams optional)
    graph/       (Neo4j access layer, batched writes with UNWIND, indexes/constraints, TTL pruning)
    detectors/   (base interface, static_cypher, hubaware, cycle, smurfing, community, ensemble)
    scoring/     (features, ring scorer, explanations)
    metrics/     (latency, matching, rolling and cumulative accuracy, exporters)
    logging/     (structured JSON logging, audit log)
    api/         (FastAPI + WebSocket)
    experiments/ (scenario configs, runner, baselines, ablations, plotting, LaTeX table export)
  web/           (React + Vite + TypeScript + Cytoscape.js + Recharts dashboard)
  configs/scenarios/*.yaml, tests/, docs/paper/, Makefile
Add the Neo4j GDS plugin to docker-compose (pin versions). Add timestamps (ts, event_id) to all events and relationships. Every run gets a run_id, a config hash and an RNG seed, and all runs are fully reproducible from those.

# MILESTONE 2: Real-time graph simulator
- Continuous event stream with controllable rate (events/s), pause/resume and speed multiplier.
- Legitimate behaviour: users join and leave, transactions follow a heavy-tailed distribution, benign shared infrastructure (offices, family devices, public wifi, carrier-NAT IPs) with a configurable hub-size distribution.
- Fraud ring lifecycle: SPAWN, GROW (members join over time), ACTIVE, MUTATE (swap a device or card), DISSOLVE. Rings appear and vanish at random during the run.
- Ring types (each configurable by size, speed, intensity): shared device, shared card, shared IP, directed money cycle (length 3 to 6), fan-in/fan-out smurfing, layered chain, and mixed-attribute rings (partial overlaps across device, card and IP).
- Evasion modes: hub-hiding (ring uses a high-degree IP/device), low-and-slow (activity below naive thresholds), identity rotation, decoy edges to legitimate users, and an adaptive attacker that observes which of its rings were flagged and shifts strategy.
- Ground-truth ledger: ring_id, type, member set over time, spawn/activation/dissolve timestamps, evasion mode. Ground truth is NEVER visible to the detectors; replace the old `ring` node property with this separate ledger.
- Scenario YAMLs: baseline, high_noise, hub_heavy, adversarial, scale_sweep, drift (benign behaviour changes mid-run).

# MILESTONE 3: Detection engine (incremental)
- Process events in micro-batches. For each batch compute the "dirty set" (touched nodes plus a k-hop neighbourhood) and re-evaluate only that region.
- Detectors:
  a) StaticCypherBaseline: the original Q1 to Q4b, run full-graph on a timer.
  b) HubAwareSharedAttribute: weighted user-user projection where an edge via a shared attribute has weight w = 1/log(1 + degree(attribute)) times a time decay; candidate rings come from weighted connected components or weighted-density thresholds. This replaces the hard cap.
  c) TemporalCycleDetector: bounded-length, time-windowed directed cycles with amount-consistency checks.
  d) SmurfingDetector: fan-in/fan-out within a time window.
  e) CommunityDetector: Neo4j GDS Louvain/WCC over a rolling projection (an alternative baseline).
  f) Ensemble: merge candidates from all detectors into unified ring objects.
- Ring identity: stable ring IDs across time using Jaccard matching of member sets. Handle merge, split, grow, shrink. Ring status: CANDIDATE, CONFIRMED, DISSOLVED, with hysteresis to avoid flapping.
- Sliding window / TTL pruning so the graph stays bounded during long runs.
- Every detector exposes the same interface and a config object so ablations are one flag.

# MILESTONE 4: Scoring and explainability ("why was this ring flagged")
- Ring features: size, density, attribute-sharing type, hub-adjusted overlap weight, cycle presence and length, temporal burstiness, account-age similarity, amount statistics, growth rate, fraction of members with other risk signals, hub exposure.
- Two-stage design: rules generate candidates, then a calibrated model (logistic regression as the interpretable default, gradient boosting as comparison) produces a risk score in [0,1]. Train on simulator ground truth with a strict time-based train/test split, and never train and evaluate on the same seed.
- Explanations for every flag: (1) the ranked per-feature contribution (coefficients or SHAP values); (2) a templated plain-English reason, e.g. "5 users share device_007 (degree 5, low hub weight); 3 accounts created within 2 hours; money returns to origin in 3 hops"; (3) the evidence subgraph as JSON; (4) a counterfactual such as "would not be flagged if shared-attribute degree > N or score < threshold"; (5) the confidence and calibration bucket.
- Early-warning prediction: score partially formed rings (growth trend) and report a "predicted to become a ring" probability with lead time, then evaluate lead time against ground truth.
- Analyst feedback loop: confirm/dismiss actions update the thresholds; log the adaptation.

# MILESTONE 5: Metrics (live and offline)
Ring-level matching: a detected ring matches a true ring if member Jaccard >= 0.5 (configurable; also report 0.3 and 0.8 sensitivity). Report:
- Rings detected, rings missed, false-positive rings, ring-level precision/recall/F1; user-level precision/recall/F1.
- Missed-ring reasons (categorised: hidden behind hub, below threshold, aged out of window, detector blind spot, late).
- Time-to-detect per ring (ring activation to first flag), distribution (p50/p90/p99), and detection lead for early warnings.
- Latency: per-event ingest-to-graph-write, per-batch detection latency, end-to-end event-to-flag, p50/p95/p99, plus a comparison of incremental vs full recompute.
- Throughput (events/s sustained), graph size over time, memory, Neo4j query counts and DB hits.
- Triage quality: precision@k and PR-AUC of the ranked alert list; alert volume per hour; false-alert cost proxy.
- Rolling-window and cumulative versions of everything. Export as CSV/Parquet and Prometheus-style /metrics.

# MILESTONE 6: Logging and audit
- structlog JSON logs with run_id, event_id, component and timestamp. Separate streams: events.jsonl, detections.jsonl (each with full explanation), metrics.jsonl, errors.log.
- Immutable audit trail of every flag, status change and analyst action, replayable. A replay tool re-runs a logged event stream and must produce identical detections (determinism test).

# MILESTONE 7: Live dashboard (React + FastAPI WebSocket)
- Live force-directed graph with nodes/edges animating in and out as events arrive; users, devices, cards, IPs and transactions styled differently; large graphs degrade gracefully (windowed view, level-of-detail).
- Flagged rings highlighted and colour-coded by status. An "Oracle view" toggle overlays ground truth (true positive, false positive, missed ring) for demos and screenshots; off by default.
- Panels: live metric cards (rings detected / missed / false, precision, recall, F1), latency time series with p50/p95/p99, throughput, alert queue ranked by risk, event log tail, graph size.
- Ring detail drawer: score, feature-contribution bar chart, plain-English reason, counterfactual, evidence subgraph, timeline, analyst confirm/dismiss.
- Controls: start/pause/speed, scenario picker, seed, inject a specific ring type on demand, enable/disable each detector, threshold and hub-weight sliders that apply live, snapshot and export the current view as PNG/SVG.

# MILESTONE 8: Experiment harness and paper artefacts
- Headless runner: `make experiment SCENARIO=adversarial SEEDS=20` with identical code paths as the live system.
- Baselines: StaticCypherBaseline with cap=100 and cap=10, Louvain/WCC community detection, and plain connected components. Optional stretch baseline: a GraphSAGE GNN (PyTorch Geometric) on the same data.
- Ablations: remove each of {hub weighting, time decay, incremental scheduling, each detector, scoring model, hysteresis}.
- Sweeps: graph size (10^3 to 10^6 nodes), event rate, hub size, ring size, evasion strength, window length, adversary adaptivity.
- Statistics: mean and 95% CI over seeds, paired tests where comparing methods, effect sizes. Include a degree-cap vs hub-weighting precision-recall frontier figure; it is the paper's headline plot.
- External validity: add loaders for public datasets (IBM AMLworld synthetic AML data, Elliptic Bitcoin, PaySim) where ring labels allow, and clearly mark what is and isn't comparable.
- Auto-generate: figures (matplotlib, vector PDF), LaTeX tables, a results.csv per experiment, and a REPRODUCE.md listing exact commands.
- docs/paper/: LaTeX skeleton (IEEE conference style) with sections Introduction, Related Work (leave TODO citations, do not invent references), Method, Experimental Setup, Results, Threats to Validity, Conclusion; plus a section on limitations covering simulator realism, synthetic-label bias and the hub-hiding recall limit.

# MILESTONE 9: Quality bar
- Unit tests for each detector against hand-built micro-graphs; property tests for the ring-matching metric; determinism and replay tests; an integration test running a 60-second scenario headless and asserting metrics are produced.
- GitHub Actions CI: lint, tests, a short smoke experiment.
- Update README with architecture diagram (Mermaid), quick start, screenshots, and a "how to reproduce paper results" section.

# OTHER ADVANCED ADDITIONS (implement if time allows, in this order)
1. Node2vec/FastRP embeddings as an extra ring feature (GDS).
2. Concept-drift detection on benign hub-size distribution with automatic hub-weight re-fit.
3. Privacy-preserving identifiers (salted hashing of device/card/IP) with a test showing detection is unchanged.
4. Cost-sensitive threshold tuning (cost of a missed ring vs analyst review time).
5. Multi-tenant / sharded scalability experiment.

# DEFINITION OF DONE
`docker compose up && make demo` opens the dashboard with a live, self-evolving graph; rings appear, get flagged with explanations, and some deliberately evasive ones are missed and shown as missed in Oracle view. `make experiment` reproduces every figure and table in docs/paper from scratch, and each reported number traces to a logged run.

# PROGRESSION
follow the global rules for each milestone
this is a multi-turn conversation
- do not skip milestones
- do not skip any steps within a milestone
- do not jump ahead
- do not skip any output formats (test reports, api docs, etc)
- do not summarize
- do not skip any validation or checkpoint steps
- for each milestone, after you implement the deliverables and run all tests, pause and wait for my go-ahead
