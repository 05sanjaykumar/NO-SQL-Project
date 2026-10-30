# Screen-recording guide

Click-by-click, in order. Keep this open on a second screen while you record.

**Record with `demo_story` (120 users, 90 s).** It plants exactly 3 rings and adds
no random ones, so nothing unexpected appears on camera. Use `hub_heavy` only for
the degree-cap contrast in segment 6.

Before you hit record: `make demo`, open http://localhost:8000/ui/, start a run
once to confirm everything works, press **Stop**, reload.

---

## Segment 0 — The gap you are closing (45 s)

Say this before you touch anything. It is what makes the rest look like research
rather than a dashboard.

> "A fraud ring is one person running many fake accounts. Each account looks
> normal alone; what gives them away is what they share - a device, a card, an
> IP. So you flag shared attributes. That breaks immediately, because offices,
> carrier NAT and public kiosks legitimately share infrastructure with thousands
> of innocent users."

Then name the four gaps. One sentence each - do not elaborate on camera.

| # | What the literature does | The gap |
|---|---|---|
| 1 | Hard shared-attribute links, or a fixed degree cap, or dropping the links for behavioural similarity (Yin et al. 2022) | Nobody keeps the cheap, explainable link and makes how much it counts *continuous* |
| 2 | Densest-subgraph and core-peeling methods run over the whole static graph; incremental community detection updates communities | A benign hub joins thousands of users into one community - community structure is not ring structure |
| 3 | GNN fraud detectors output per-node scores and need labelled training graphs; interpretability is still an open problem in that line | No ring-level, calibrated, explained alert |
| 4 | Evasion is evaluated against the evasion strategies the authors wrote | You cannot test the attack you never imagined |

> "Those four gaps are exactly what the four contributions answer, and you will
> see each of them on this screen."

---

## Segment 1 — The board (20 s)

Do nothing. Just pan over the page.

> "This is the live dashboard. Controls at the top, the transaction graph on the
> left, a scoreboard and the alert queue on the right, and latency and throughput
> charts underneath."

---

## Segment 2 — Start a run (60 s)

| Control | Set to |
|---|---|
| Scenario | `demo_story (120 users, 90s)` |
| seed | `7` |
| SPEED | `5x` |

Press **Start**.

> "120 simulated customers. The graph fills as events arrive - each dot is an
> account, a device, a card or an IP. Three fraud rings are planted in here, and
> because we generate the data we know exactly which accounts they are."

Point at the header counter: `t = … s · … events`. Point at the alert queue
filling with `candidate` and `confirmed` chips, ranked by risk.

---

## Segment 3 — The answer key (40 s)

Tick **Oracle view**.

> "Oracle view overlays ground truth. Red is fraud we flagged. Orange is a
> legitimate account we flagged - a false alarm. A red outline with no fill is a
> real ring we missed."

Read the **Scoreboard** aloud: rings detected, rings missed, false rings,
precision, recall, F1, time to detect.

> "Every one of these is measured against the answer key, not estimated."

---

## Segment 4 — One alert, explained (60 s)

Click any **confirmed** row in the alert queue. The ring drawer opens.

> "Click an alert and you get why it fired: the members, the features that drove
> the score, their contributions, the evidence subgraph, and a counterfactual -
> what would have to change for it not to be flagged."

⚠️ If the drawer shows a ring scored 1.0 alongside `risk 0` or
`cycle_present=0`, say: *"that's an inconsistency between the detector and the
scorer that we found and haven't fixed yet"* — then move on. Do not defend it.

Close the drawer.

---

## Segment 5 — Plant a ring on camera (60 s)

| Control | Set to |
|---|---|
| INJECT | `shared_device` |
| evasion | `none` |
| size | `4` |

Press **Inject ring**. Watch the alert queue.

> "I've just created a four-account fraud ring sharing one device. No attempt to
> hide. It gets picked up almost immediately."

Now inject again with evasion `low_and_slow`, size `4`.

> "Same ring, but moving slowly and in small amounts. This one is much harder -
> slow rings defeat every method we tested, including ours. We say so in the
> paper rather than hiding it."

---

## Segment 6 — The core result (90 s) — the important one

Press **Stop**. Switch scenario to `hub_heavy (100 users, 60s)`, seed `7`,
speed `5x`, **Start**. This scenario has a `mega_office_gateway` hub.

Let it run ~20 seconds, then in **DETECTORS** untick **hub aware**, leaving
**static cypher** on.

> "This is the old approach: fixed rules with a hard degree cap. The cap throws
> away the busy office gateway - and any ring hiding behind it goes with it."

Point at recall falling in the scoreboard. Now re-tick **hub aware**.

> "Our method replaces the cap with a continuous weight. A shared attribute
> contributes one over log of its degree, with time decay. A busy hub counts for
> almost nothing by itself, but a group that shares that hub *and* trades among
> itself still builds up enough weight to surface."

Say the measured result over 5 scenarios and 10 seeds:

> "F1 0.73, against 0.21 for the degree-capped rules and 0.29 for Louvain."

Optionally drag the **hub edge threshold** slider and let the graph respond.

---

## Segment 7 — Ask the system (90 s) — your interface addition

Scroll to **Ask the system**. Type:

```
Show me the highest-risk confirmed ring on the graph, hiding everything
else, and explain in two sentences why it was flagged.
```

Wait. It takes 15-30 seconds - say the next line while it runs.

> "Every panel here exists because we decided in advance to build it. You can't
> pre-build every question an analyst might ask. This one takes the question,
> queries the live detector through MCP tools, and then changes what the graph
> shows."

When it lands: point at the **focus bar** above the graph, the narrowed graph,
and the **tool chips** under the answer.

> "Those chips are the tools it actually called. That's how you know it queried
> the system rather than inventing a plausible number."

Press **show everything** to restore the view.

⚠️ One question per recording. Each costs 15-25 cents. If it errors, cut and
move on - don't retry on camera.

---

## Segment 8 — Scale (30 s)

Point at the **Detection latency per batch** chart.

> "Detection is incremental - we re-evaluate only the part of the graph each
> batch touches, so per-batch latency stays flat as the graph grows. At a hundred
> thousand users that's 36 milliseconds a batch, against 22.5 seconds for full
> recomputation. A 623-fold difference, flagging the same rings."

If you want it live, run `scale_sweep (1000 users, 120s)` - but it is slower and
busier on screen, so only if you have time.

---

## Segment 9 — What is new, against what was there before (75 s)

Press **Stop**. This segment is talking over a still screen - no clicking.

First the four contributions, pointed at the thing on screen that shows each one.

| Gap | What we did | Where it is on screen | Measured |
|---|---|---|---|
| 1 | **Hub-aware weighting** - the degree cap is replaced by a continuous weight: a shared attribute contributes `1/ln(1+d)`, with exponential time decay and intra-pair transfer weight | the **hub aware** tickbox you toggled in segment 6 | F1 **0.73** vs **0.21** for cap-10 and **0.29** for Louvain; reachable recall on shared-attribute rings **0.55 → 0.86** |
| 2 | **Incremental detection** - peel only the region each micro-batch touched, instead of re-running whole-graph queries on a timer | the **Detection latency per batch** chart staying flat | **36 ms** per batch vs **22,501 ms** full recomputation at 100,000 users - **623×**, same rings flagged |
| 3 | **Calibrated, explained alerts** - ring-level, not per-node, with feature contributions, an evidence subgraph and a counterfactual | the ring drawer from segment 4 | precision **0.76** at recall **0.74** |
| 4 | **Evaluation under evasion** - five evasion strategies with ground truth, plus a red-team search over the space those five are points in | the **evasion** dropdown you used in segment 5 | ring recall by strategy (rings found / rings planted): `none` 91/110, `decoy_edges` 22/27, `hub_hiding` 35/52, `low_and_slow` 3/13, `identity_rotation` 4/14 |

Then the honest line about the two you added on top:

> "Two of these are ours on top of the detector. The question box queries the
> live detector through MCP tools and re-draws the graph - that closes the gap
> that no dashboard can pre-build every question an analyst will ask. And the
> red-team search looks for evasions we did not write ourselves, which closes
> gap four properly. The search runs and produces results, but the isolated
> single-ring setup still needs validating against the full scenarios, so we are
> not quoting an evasion rate from it yet."

### If you want the before/after on the previous code

Useful if the examiner asks what the project looked like earlier. Say it as
project history, not as a claim.

| First version | Now |
|---|---|
| `seed.py` - one fixed 126-node snapshot | streaming simulator, 100 to 100,000 users, rings that form, grow, mutate and evade |
| `queries.cypher` - six queries pasted into Neo4j Browser by hand | continuous micro-batch engine, incremental peeling |
| degree cap `< 10`, a hard cut (Q4b, comment: *"precision bought with recall"*) | continuous `1/ln(1+d)` weight, no cut |
| a row either matched or it did not | calibrated risk score, feature contributions, counterfactual |
| you eyeballed whether a row was a real ring | planted rings as an answer key; precision, recall, F1 and time-to-detect on screen |
| `static/index.html` running those six queries | React dashboard: live graph, alert queue, oracle overlay, PNG/SVG export |
| - | **Ask the system** - MCP-backed question box that also drives the graph |
| - | **adversarial search** for evasions nobody wrote by hand |

⚠️ Do not say the old version was "wrong". Q4b is a correct demonstration of
degree-bounded traversal; the point is that the cap costs recall, and that cost
is what contribution 1 removes.

---

## Segment 10 — Close honestly (30 s)

> "Three limitations. All our positive results are synthetic - there's no public
> dataset with real shared-attribute links. Rings that rotate identities or move
> very slowly evade everything we tested. And the hub weighting extends reachable
> recall from 0.55 to 0.86, but not at higher precision everywhere - the
> calibrated scorer supplies most of the precision, not the weighting."

---

## For report figures

**PNG** and **SVG** buttons next to Oracle view export the current graph. Take
them with Oracle view on - the answer-key colours make a far better figure than
plain alert colours.

---

## Don't do these on camera

- Don't switch scenarios mid-sentence; press **Stop** first or the graph lurches
- Don't ask the panel more than one question
- Don't open the ring drawer on a ring you haven't checked, unless you're ready
  to name the scorer inconsistency
- Don't run at `max` speed while talking - the graph becomes unreadable
- Don't use `./start.sh` - the Ask panel needs `make demo`

## If it breaks mid-recording

`demo/story.html` (from `make demo-story`) is an offline replay with the answer
key and a detector switch. It needs no server and cannot fail. Have it open in a
second tab before you record.
