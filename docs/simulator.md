# Real-time graph simulator (Milestone 2)

The simulator produces a continuous, self-evolving stream of graph events:
legitimate users with heavy-tailed activity, benign shared infrastructure, and
fraud rings that spawn, grow, act, mutate and dissolve. It is a deterministic
discrete-event simulation. A `(scenario, seed)` pair always yields the same
stream, byte for byte, at any playback speed.

## Components

| Module | Role |
|---|---|
| `simulator/config.py` | Parses and validates scenario YAML into typed dataclasses |
| `simulator/world.py` | Observable state: users, devices, cards, IPs, links. Opaque sequential ids |
| `simulator/event_generator.py` | Discrete-event engine: simulated clock, benign behaviour, hubs, drift, rate control |
| `simulator/rings.py` | `RingAgent` (one fraud ring's lifecycle and behaviour) and `AdaptiveAttacker` |
| `simulator/ground_truth.py` | Ground-truth ledger, the only place ring labels exist |
| `simulator/player.py` | `StreamPlayer`: wall-clock pacing onto an `EventBus` (rate, pause/resume, speed) |
| `simulator/recorder.py` | Headless runs that write `runs/<run_id>/` artefacts |
| `stream/event_bus.py` | `AsyncQueueBus` (default) and `RedisStreamBus` (Redis Streams, optional) |

## Event stream

Observable event types and their payloads (nothing else is ever emitted):

| Type | Payload |
|---|---|
| `user_join` | `user_id`, `name` |
| `user_leave` | `user_id` |
| `device_link` / `card_link` / `ip_link` | `user_id`, `device_id` / `card_id` / `ip` |
| `attribute_unlink` | `user_id`, `attr_type`, `attr_id` |
| `transaction` | `txn_id`, `sender`, `receiver`, `amount` |

Every event has `event_id`, `ts` (simulated time on a fixed epoch,
2026-01-01T00:00Z by default), `run_id` and a monotonic `seq`. Event ids are
drawn from a seeded RNG, so they are reproducible too. A re-emitted link
(for example a re-login from a known IP) refreshes the relationship's `ts` in Neo4j, which
the time-decay weighting in Milestone 3 relies on.

**Ground truth never enters the stream.** The `ring_*` event types are
ledger-only (`GROUND_TRUTH_ONLY_EVENTS`), and the Neo4j writer refuses them.
User and attribute ids are opaque (`u000123`, `device_00042`), so an id never
reveals ring or hub membership. The legacy `User.ring` property and its
index are gone from the new pipeline. Only the original `seed.py` course demo
still writes it.

## Legitimate behaviour

- **Population and churn.** `num_users` join at t=0. Further joins and leaves
  follow Poisson processes (`user_join_rate`, `user_leave_rate`).
- **Heavy tails.** Each user has a Pareto(`heavy_tail_alpha`) activity weight
  that decides who transacts. Amounts are log-normal
  (`amount_median`, `amount_sigma`). Users mostly pay remembered contacts.
- **Event mix.** `benign_mix` weights transactions, IP logins (re-login,
  public hub or new IP), new devices and new cards.
- **Personal attributes.** At most two benign users share a personal device,
  card or IP, so accidental 3+ clusters never appear (the same rule seed.py
  used).
- **Benign hubs.** Named hubs (`hub_distribution`) and random hubs with
  Pareto-distributed sizes (`hub_size_distribution`). Categories: office,
  public_wifi, carrier_nat (IP); family_device, kiosk (device);
  corporate_card (card).
- **Drift.** At `drift.trigger_time_s` the benign rate and amounts scale by
  `shift_multiplier` and a new hub of `new_hub_size` fills up.

## Fraud rings

Lifecycle: **SPAWN** (initial members join) → **GROW** (a member joins every
~`grow_interval_s`) → **ACTIVE** (fraudulent actions every
~`action_interval_s`) → **MUTATE** (with probability `mutate_prob` after an
action: the shared device or card is swapped via unlink + link) →
**DISSOLVE** (after `active_duration_s`: accounts close). Every transition
goes to the ledger.

| Type | Behaviour |
|---|---|
| `shared_device` / `shared_card` / `shared_ip` | All members link to one anchor attribute and transact internally |
| `money_cycle` (length 3–6) | Money goes round the cycle. Each hop keeps a 1–5% cut, and hops are spaced in time |
| `smurfing_fan_in` / `smurfing_fan_out` | Bursts of transfers just under the 10,000 reporting threshold into, or out of, one account |
| `layered_chain` | Money hops down a chain of accounts with a small haircut per hop |
| `mixed_attribute` | Three anchors (device, card, IP). Each member links to two, so overlaps are partial and no single attribute covers the ring |

Rounds of a cycle, chain or burst never overlap, which keeps the
amount-consistency signal clean.

### Evasion modes

| Mode | Effect |
|---|---|
| `hub_hiding` | Anchor on a busy benign hub (`shared_attribute` names it). When no hub of that kind exists, every member also uses the largest hub as cover |
| `low_and_slow` | 6× slower growth, 8× fewer actions, amounts ×0.15, hops spaced 30–120 s |
| `identity_rotation` | Every 3 actions, one account closes and a fresh one takes its role |
| `decoy_edges` | Members share an attribute with legitimate users and make 2 small payments to them per action |
| `adaptive` | `AdaptiveAttacker` picks a strategy by weight. `EventGenerator.notify_flagged(user_ids)` tells it which of its accounts were flagged. It then abandons that ring, multiplies that strategy's weight by `penalty`, and respawns a replacement (`<id>~N`) |

`random_rings` spawns rings by a Poisson process (`rate_per_min`) with random
type, size, evasion and lifetime, so rings appear and vanish throughout a run.

## Ground-truth ledger

Each ring records `ring_id`, `ring_type`, `evasion_mode`, lifecycle, member set
over time (`member_history`, used by `get_members_at(t)`), `spawn_ts`,
`activate_ts`, `dissolve_ts`, shared attributes, and `details`. The details
include the concrete strategy an adaptive ring used, mutation count, and
attacker observations. The append-only log records spawn, grow, activate,
mutate, resume, rotate, attacker_observed_flag and dissolve.

## Pacing and controls

`StreamPlayer` maps simulated time to wall time:
`wall = anchor + (sim − sim_anchor) / speed`.

- `speed` 1 = real time, N = N× faster, 0 = as fast as possible.
- `set_rate(eps)` changes the benign event rate (events per simulated second).
- `pause()` / `resume()` hold the stream. Simulated time does not advance
  while paused.

Pacing never changes content. In the M2 check, the baseline stream for seed
42 had the same digest at speeds 1, 4 and max.

Control calls (`set_rate`, `notify_flagged`, drift) go to
`generator.control_log` with their simulated time, so a live run can be
replayed exactly (Milestone 6).

## Running it

```bash
fraudsim simulate --scenario adversarial --seed 7 --out runs/   # headless, writes artefacts
fraudsim run --scenario baseline --seed 42 --speed 10 --duration 30   # live JSON stream
make simulate SCENARIO=hub_heavy SEED=3
make simulate-all SEED=42
```

API (FastAPI): `POST /runs/start {scenario, seed, speed_multiplier}`,
`/runs/pause`, `/runs/resume`, `/runs/stop`,
`POST /runs/speed {speed_multiplier}`, `POST /runs/rate {events_per_second}`,
`GET /status`, `GET /events/recent?limit=N`, and WebSocket `/ws`, which pushes
`EVENT` messages.

`runs/<run_id>/` holds `events.jsonl`, `ground_truth.jsonl`,
`ground_truth_log.jsonl`, `hubs.jsonl` and `manifest.json` (scenario, seed,
config hash, counts, stream digest, timings).

## Milestone 2 check runs

These are the outputs of `fraudsim simulate --seed 42` for every scenario on
2026-10-20. Digests are reproducible from the scenario files at this commit.
Run directories are not committed (`runs/` is git-ignored); rerun the same
command to regenerate them.

| Scenario | Seed | run_id | config_hash | Events | Users | Hubs | Rings | Evasion mix | Stream digest (prefix) |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 42 | `fc73ea8c46cb` | `f902fc8c349f37df` | 889 | 46 | 2 | 4 | none: 4 | `3cb5eb14be8f72a1` |
| high_noise | 42 | `6633e08bc823` | `fb23737a87f98a89` | 4054 | 213 | 12 | 6 | decoy_edges: 1, none: 5 | `254e2c8dfe960665` |
| hub_heavy | 42 | `fd50e0ae6d6e` | `407d82544ce2223f` | 2304 | 130 | 9 | 4 | hub_hiding: 3, none: 1 | `093c09a22e5f2ca1` |
| adversarial | 42 | `dd7569285a2d` | `6f5f87b371b8df51` | 3700 | 192 | 2 | 7 | adaptive: 1, decoy_edges: 1, hub_hiding: 2, identity_rotation: 1, low_and_slow: 1, none: 1 | `e1eeebf727d46a5a` |
| scale_sweep | 42 | `9ad6e4c6482d` | `8cf2e3e4218d4393` | 17660 | 1189 | 32 | 10 | hub_hiding: 3, none: 7 | `ccd2541227ddf77e` |
| drift | 42 | `9fbd96ad6186` | `5363f2c3960c7004` | 7403 | 237 | 2 | 3 | hub_hiding: 1, none: 2 | `7c0e4763bb13da64` |

These are simulator statistics only. No detection numbers exist yet
(detectors arrive in Milestone 3).

Test report: [`docs/reports/m2_test_report.txt`](reports/m2_test_report.txt).
