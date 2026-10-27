# Adversarial search

## What this is

The evaluation in the paper tests the detector against five evasion strategies —
`hub_hiding`, `low_and_slow`, `identity_rotation`, `decoy_edges`, `adaptive`.
Those five are hand-chosen points in a much larger space. A ring also has a type,
a size, a recruitment rate, an action rate, a lifetime, a mutation rate and an
intensity, and a scenario can set all of them.

This module searches that space for ring behaviours the detector misses, instead
of only testing the ones we thought of. It is a red-team harness: the point is to
find weaknesses while they are cheap to fix.

## How it works

One round is: a strategist proposes an attack plan, the simulator runs a world
containing exactly that one ring, the detector scores it, and the outcome goes
back to the strategist.

Running **one** planted ring is what makes the measurement readable — with a
single piece of ground truth, `rings_detected` answers the question directly.

Each plan is run on several seeds and only counts as an evasion if it goes
undetected on **all** of them. Slipping past one seed out of three is luck.

### What stops the search cheating

A ring that never acts is trivially undetected, and so is one that never finishes
forming. Neither is an attack. `AttackPlan.is_valid()` rejects both:

- at least `MIN_ACTIONS` (6) actions over its lifetime;
- recruitment finished within `MAX_FORMATION_FRACTION` (0.5) of its lifetime;
- at least 3 members.

So an evasion means the detector missed a ring that really assembled and really
operated.

### Strategists

| Name | What it does |
|---|---|
| `random` | independent samples. The control arm — any adaptive method must beat it |
| `evolutionary` | keeps the best plans so far and perturbs them |
| `llm` | asks a model to read the history and propose the next plan |

`llm` needs `ANTHROPIC_API_KEY` and the `anthropic` package. Without either it
says so and falls back to `evolutionary` rather than failing midway.

## Running it

```bash
python -m fraudsim.adversary --rounds 40 --strategist evolutionary
python -m fraudsim.adversary --rounds 40 --strategist random     # control
python -m fraudsim.adversary --rounds 20 --strategist llm
```

Useful options: `--seeds 42,43,44`, `--scenario adversarial`, `--duration 120`,
`--backend memory|neo4j`, `--model models/ring_scorer_lr.joblib`, `--out PATH`.

A report lands in `results/adversary/` with every plan tried, which evaded, and a
summary.

## Preliminary findings — not yet a result

From two short runs, 12 rounds each, 2 seeds, 120 simulated seconds, on the
`adversarial` scenario with the logistic-regression scorer:

| Strategist | Evasions found | Rate | First evasion | Novel |
|---|---|---|---|---|
| `random` | 8 / 12 | 67% | round 2 | 1 |
| `evolutionary` | 2 / 12 | 17% | round 8 | 0 |

"Novel" means the evading plan did not use `low_and_slow` or `identity_rotation`,
the two modes the paper already reports as defeating every method tested.

Two things stand out, and both need checking before either is believed:

**Random beat the adaptive search.** At a 12-round budget the evolutionary
strategist spends most of its moves exploiting a small region while random covers
the space. This may simply mean the budget is far too small for adaptation to pay
off, or that the fitness signal is too coarse. Needs a much longer run.

**The evasion rate looks too high.** A 67% evasion rate is not consistent with the
accuracy the paper reports, which suggests the harness differs from the paper's
evaluation in a way that matters, rather than that the detector is weak. The
isolated single-ring world is the obvious suspect: it has no other rings, so the
detector sees a very different graph from the one it is evaluated on, and ring
precision there is meaningless (every other detection is a false positive by
construction).

**Do not quote these numbers.** They are a working signal that the harness runs
end to end, not a measured result.

## What to check next

1. Re-run with a budget of hundreds of rounds before comparing strategists.
2. Validate the isolated-ring harness against the paper's own scenarios — does a
   plan that evades here also evade in a normally populated world?
3. Decide whether "evaded" should mean undetected, or detected too late to matter;
   time-to-detection is already measured and is probably the better signal.
4. The scorers in `models/` were trained on scikit-learn 1.7.2 and unpickle with a
   version warning on 1.9.1. Pin the version or retrain before trusting any score.
