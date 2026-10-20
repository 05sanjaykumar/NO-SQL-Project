# Logging, audit and replay (Milestone 6)

## Log streams

Every `fraudsim detect` run, and every live API run (under
`runs/live/<run_id>/`), writes these files into its run directory:

| File | Contents |
|---|---|
| `events.jsonl` | Every observable event (the replay input): `event_id`, `event_type`, simulated `ts`, `seq`, `payload`, `run_id`, `component` |
| `detections.jsonl` | Every tracked-ring change: `batch`, `first_seq`/`last_seq`, `ts`, `ring_id`, `change`, `status`, `members`, `detectors`, `score`, plus the event_id of the last event in the batch. Flags (`alert`, `early_warning`, `confirmed`) carry the full explanation and risk |
| `metrics.jsonl` | Per-batch stage timings, region size, store queries, graph size and RSS; the last line is the run's evaluation summary |
| `errors.log` | Errors as JSON lines (also mirrored to the structlog console) |
| `batches.jsonl` | Batch boundaries and timings. Replay uses them to reproduce the exact micro-batches, including live batches cut by wall-clock idle timeouts |

Simulated time (`ts`) and wall-clock time (`wall_ts`) are kept apart. Only
simulated time is part of what replay compares.

## Audit trail (`audit.jsonl`)

The trail is append-only and hash-chained. Each entry has:
- `seq` and `audit_id` (`<run_id>-<seq>`);
- `ts`: simulated time for system actions, wall-clock time for analyst
  actions;
- `action_type`, `ring_id`, `details` and `actor`;
- `prev_hash` and `hash`, where `hash = SHA-256(canonical JSON of all other
  fields)`.

| action_type | Written when |
|---|---|
| `RING_CREATED` | A ring is created or split off |
| `MEMBERS_CHANGED` | A ring grows or shrinks |
| `STATUS_CHANGED` | A ring is confirmed, dissolved or merged into another |
| `EARLY_WARNING` / `FLAG_RAISED` | The scoring stage raises an early warning or an alert |
| `ANALYST_CONFIRM` / `ANALYST_DISMISS` | An analyst confirms or dismisses a ring (`POST /rings/{id}/feedback`); the actor is the analyst id |
| `THRESHOLD_UPDATED` | The alert threshold changes as a result of that feedback |

- `fraudsim audit-verify runs/<id>` fails on any edited, deleted or reordered
  entry.
- `replay_audit(entries)` rebuilds every ring's status, members, flag and
  analyst verdict from the trail alone.
- A test checks that the rebuilt state matches the engine's final state.

## Replay (`fraudsim replay runs/<id>`)

Replay re-runs detection on `events.jsonl`, using:
- the logged batch boundaries;
- the logged engine and detector configuration;
- the same risk model, checked against the SHA-256 recorded in the manifest.

It then compares the result with the logs:
- every detection record must be identical;
- every system audit entry must be identical, including chain hashes.

The command exits non-zero on any difference and names the first records
that differ.

Detection is a pure function of (events, batch boundaries, configuration,
model). The tests that enforce this are:
- `test_detection_identical_across_hash_seeds` (M4);
- `test_replay_is_identical_and_audit_rebuilds_state`;
- `test_replay_detects_changed_logs`;
- `test_live_run_is_logged_audited_and_replayable`. This one covers a live API
  run with wall-clock batching and an analyst dismissal recorded after the
  stream ended.

Limitation: analyst feedback that changes a threshold *during* a live stream
alters later detections. Replay does not yet re-apply those threshold changes
at their batch. Such a run reports differences after the feedback point; the
audit trail shows exactly where.

## M6 check run

**adversarial, seed 42, logistic model, closed loop** (run `9ad2a23c6305`).
Replay re-ran all 3,732 events in the same 75 batches. All 245 detection
records and all 245 system audit entries were identical, and the hash chain
verified.
