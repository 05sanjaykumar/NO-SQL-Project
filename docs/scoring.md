# Scoring and explainability (Milestone 4)

## Two-stage design

1. **Candidates.** The detectors and the ensemble (Milestone 3) propose rings.
   Each ring has a stable id and a status (CANDIDATE, CONFIRMED or DISSOLVED).
2. **Risk.** `ScoringStage` runs inside `DetectionEngine` after tracking. Every
   ring that changed in a batch gets a feature vector, a calibrated risk score
   in [0, 1] and a full explanation.

| Event | When it is raised |
|---|---|
| `early_warning` | The first time a still-forming (CANDIDATE) ring scores at or above its threshold |
| `alert` | The first time a CONFIRMED ring scores at or above its threshold |

The closed loop (`fraudsim detect`, live API) tells the adaptive attacker
about alerts.

## Features (`scoring/features.py`)

Features are computed from a view of the ring's members (their links, the
global degree of each linked attribute, the transfers among them inside the
window), the members' account creation times, and the ring's tracking
history. No ground truth is used.

| Feature | Meaning |
|---|---|
| `size` | members |
| `density` | share of member pairs linked by a shared attribute or a transfer |
| `share_device` / `share_card` / `share_ip` | share of members on the most shared device / card / IP (attribute-sharing type) |
| `hub_adjusted_weight` | Σ over shared attributes of 1/ln(1+degree), per member pair |
| `cycle_present`, `cycle_length` | amount-consistent, time-ordered cycle among members |
| `temporal_burstiness` | 1 − (time span over which members linked their main shared attribute) / 300 s |
| `account_age_similarity`, `account_age_min` | how alike, and how old, the members' accounts are |
| `internal_txns_per_member`, `amount_mean_log`, `amount_cv`, `near_threshold_frac` | transfer statistics among members |
| `growth_rate` | members added per minute since first seen |
| `risk_signal_fraction` | share of members that also belong to another live ring |
| `hub_exposure` | ln(1 + users of the busiest shared attribute) |
| `n_detectors` | detectors that found it |

## Model (`scoring/scorer.py`)

| Model | Role | Contributions |
|---|---|---|
| `logistic_regression` (default) | Interpretable scorer: standardised features, class-balanced logistic regression | Exact: coef_i · z_i to the log-odds |
| `gradient_boosting` | Comparison: `HistGradientBoostingClassifier` | Occlusion (log-odds change when a feature is set to its training mean), an approximation |

Calibration is an isotonic regression on the model's raw probabilities. The
reliability table (predicted vs observed rate per 0.1 bucket on the test
seeds) is stored with the model.

**Training protocol** (`fraudsim train-scorer`, `make train-scorer`):
- Train seeds and test seeds are disjoint. The default is train 101–110,
  test 201–205, over all six scenarios.
- Within the training runs the split is by time. Rows from the first 70% of
  each run fit the model; rows from the remaining 30% fit the calibrator.
- One row is one scored snapshot of a tracked ring.
- Label = 1 if the ring's members match some ground-truth ring's members (all
  members it ever had) with Jaccard ≥ 0.5.
- Training runs give the adaptive attacker no feedback, so the data does not
  depend on any model.
- The model is saved as `models/ring_scorer_<lr|gb>.joblib`, with a `.json`
  file beside it holding the seeds, scenario hashes, the run ids of every
  training and test run, the metrics, the reliability table and the
  coefficients.
- The rows used are in `runs/training/<training_id>/`.

## Explanations (`scoring/explanations.py`)

Every scored ring carries:

1. `feature_contributions`, ranked by magnitude.
2. `reason_text`, assembled only from its evidence. For example: "4 members
   share a busy ip used by 44 accounts in total (ip_00061, hub weight 0.26),
   so sharing it alone is weak evidence; 30 transfers between members within
   53 seconds; money returns to its origin in 3 hops; 4 accounts created
   within 6 seconds; found by hub_aware, temporal_cycle."
3. `evidence_subgraph`: the members, the attributes at least two of them share
   (with degree and hub weight), their links, and the transfers among them.
4. `counterfactual`:
   - *Model:* the value of the strongest actionable feature at which the
     calibrated score would drop below the alert threshold. This is exact for
     logistic regression, and a unit test checks it.
   - *Rule:* how many accounts the shared hub would need before the hub-aware
     rule stopped linking the members.
5. `confidence` and `calibration_bucket`, with that bucket's observed fraud rate
   on held-out seeds.

## Early warning (`scoring/early_warning.py`)

For each ground-truth ring, the warning time is the earliest early warning or
alert raised by a tracked ring that holds at least two of its members (with
≥ 66% of the tracked members inside the true ring). Lead time is the ring's
activation time minus the warning time; positive means the warning came while
the ring was still forming.

## Analyst feedback (`AlertPolicy`)

Thresholds are kept per ring type (default 0.5).
- **Dismiss:** the threshold moves halfway toward score + 0.02, so similar
  rings stop alerting.
- **Confirm** of a ring scoring at or below the threshold + 0.02: the
  threshold moves halfway toward score − 0.02.

Every change is logged with the before and after values. The API's
`POST /rings/{id}/feedback` applies it and returns the adaptation.

## Demo: `make demo-story`

`fraudsim demo-story --seed 7` runs one fixed stream (scenario `demo_story`)
through three detectors:
- the original degree-capped rules with cap 10;
- the same rules with cap 100;
- the full hub-aware system with the calibrated model.

It writes `demo/story.html`, a self-contained page with no external scripts.
The page shows:
- what each approach caught, missed and wrongly flagged;
- the hidden ring's explanation (risk, reason, contribution bars,
  counterfactual);
- the graph of the office network with ground-truth and per-approach views;
- a timeline of when each ring became active and when each approach flagged it.

Every number on the page comes from that run. The page shows the run_id,
seed, scenario hash and stream digest. Seed 7 is used neither for training
nor for testing the model.

## Milestone 4 results (2026-10-20)

Both models were trained by `fraudsim train-scorer` on the same data:
- Six scenarios; training seeds 101–110, test seeds 201–205.
- 5,812 rows for fitting, 3,133 for calibration, 4,516 for testing (984
  positive).
- Both training logs list identical per-run row counts and stream digests,
  which confirms the pipeline is reproducible across processes.

Each `.json` next to a model lists every run_id it used.

| Model | training_id | Test ROC AUC | Test PR AUC | Brier | ECE |
|---|---|---|---|---|---|
| logistic regression (default) | `dd98f740ea8c` | 0.982 | 0.908 | 0.046 | 0.029 |
| gradient boosting | `4d7c1774b78f` | 0.998 | 0.988 | 0.015 | 0.011 |

**Caveats:**
- These are snapshot-level scores. One ring contributes a row every time it
  changes, so rows are correlated. Ring-level precision and recall come in
  Milestone 5.
- The logistic model is well calibrated near 0 and 1 but overconfident in the
  middle buckets. On the test seeds, its 0.8–0.9 bucket was fraud 67% of the
  time. Every explanation shows the observed rate of its bucket.
- Gradient boosting separates better. Logistic regression stays the default
  because its contributions and counterfactuals are exact.

**Demo run** `00434da7845b` (seed 7, logistic model). The same 2,512 events
went through each approach:

| Approach | Rings caught | False rings | Innocent users flagged |
|---|---|---|---|
| Rules, cap 10 | 2 / 3 | 23 | 52 |
| Rules, cap 100 | 2 / 3 | 24 | 74 |
| Hub-aware + risk model | 3 / 3 | 2 | 8 |

The cap rules catch the office ring only through Q3: three of its members
happen to form a 3-cycle of transfers. The shared-IP query (Q4b) skips it
because of the cap.
