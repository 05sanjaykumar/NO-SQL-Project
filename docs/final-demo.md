# Final demo script

Seven minutes, start to finish. Lines in quotes are meant to be said roughly as
written. Everything here has been run and checked.

---

## Before you walk in

```bash
docker compose up -d                 # Neo4j and Redis
cd web && npm run build && cd ..     # web/dist is gitignored; /ui 404s without it
make demo                            # or: python -m fraudsim.cli api --port 8000
```

Open **http://localhost:8000/ui/** and start a run once to check it works, then
press Stop and reload so you begin from a clean screen.

**Use `make demo`, not `./start.sh`.** The Ask panel needs the Claude Code CLI,
which lives on the host; under the container profile it disables itself.

Check before you start:

- the Ask panel appears under the controls and does **not** say unavailable;
- `make demo-story` has been run once, so `demo/story.html` exists as a fallback;
- close other apps - Docker gets OOM-killed on 8 GB when the browser is heavy.

---

## 1. The problem (30 seconds)

> "A fraud ring is one person running many fake accounts. Each account looks
> normal on its own. What gives them away is what they share - a device, a card,
> an IP.
>
> The obvious fix is to flag anything shared by several accounts. That breaks
> immediately, because offices, phone networks and public kiosks legitimately
> share infrastructure with thousands of innocent users. The usual remedy is a
> hard degree cap: ignore anything more than N accounts touch. But then any ring
> hiding behind a busy hub becomes invisible, by construction."

---

## 2. The simulator (1 minute)

Pick `adversarial`, seed 42, speed 5x, press **Start**.

> "This is a simulated bank. Legitimate users, benign hubs - office wifi, carrier
> NAT, public kiosks - and fraud rings that form, grow, mutate and try to evade.
> Because we generate it, we know exactly which accounts are fraudulent, so every
> number you see is measured against ground truth rather than estimated."

Let it run. Point at the graph filling in and the alert queue populating.

Tick **Oracle view**.

> "Oracle view overlays the answer key: correct flags, false alarms, and real
> rings we missed."

---

## 3. The core result (2 minutes) — the part that matters

Use the **Inject** controls: `shared_ip`, evasion `hub_hiding`, size 4, **Inject ring**.

> "I've just planted a four-account ring that deliberately hides behind a busy
> shared IP."

Watch it get caught. Then set **hub edge threshold** to reproduce the cap
behaviour, or switch detectors to `static_cypher` only.

> "With a hard degree cap, that ring is unreachable - the cap throws away the
> hub, and the ring with it.
>
> Our method replaces the cap with a continuous weight. A shared attribute
> contributes one over log of its degree, with time decay. A busy hub counts for
> very little on its own, but a group that shares a hub **and** trades among
> itself still accumulates enough weight to surface."

**The numbers, measured over 5 scenarios and 10 seeds:**

| Method | Precision | Recall | F1 |
|---|---|---|---|
| **Hub-aware (ours)** | 0.76 | 0.74 | **0.73** ±0.05 |
| Degree cap 10 | 0.14 | 0.61 | 0.21 |
| Louvain communities | 0.39 | 0.33 | 0.29 |

> "F1 0.73 against 0.21 for the degree-capped rules and 0.29 for Louvain, and
> every difference is significant under a paired Wilcoxon test."

---

## 4. Incremental detection (1 minute)

> "The second problem is cost. Re-running whole-graph queries on a timer gets
> more expensive as the graph grows. We re-evaluate only the region each
> micro-batch actually touches."

**At 100,000 users: 36 ms per batch, against 22,501 ms for full recomputation.
A 623× speedup, and it flags the same rings.**

---

## 5. Asking the system — our interface addition (1.5 minutes)

Scroll to **Ask the system** and type something you have not rehearsed. Better:
ask the examiner to invent a question.

A good one to fall back on:

```
Show me the highest-risk confirmed ring on the graph, hiding everything
else, and explain in two sentences why it was flagged.
```

The graph narrows to that ring, a bar appears saying what it is focused on, and
the answer explains it.

> "Every panel on this dashboard exists because we decided in advance to build
> it. You cannot pre-build every question an analyst will ask. This answers the
> rest, by querying the live detector through MCP tools and then changing what
> the graph shows.
>
> The chips underneath are the tools it actually called - so you can see it
> queried the system rather than inventing a plausible number."

Press **show everything** to restore the view.

⚠️ Each question costs about 15-25 cents and takes 15-30 seconds. Ask two at
most. If it fails, move on - do not retry live.

---

## 6. Adversarial evaluation — our evaluation addition (1 minute)

No live run; show the numbers.

> "The evaluation tests five evasion strategies we wrote ourselves. That is a
> blind spot: you cannot test the attack you never imagined.
>
> So we built a red-team harness that searches the space those five are points
> in - ring type, size, recruitment rate, action rate, lifetime - and looks for
> behaviours the detector misses. Anything it finds is a hole a real fraudster
> would eventually find too, except we find it first."

Be straight about the status: the harness runs and produces results, but the
isolated single-ring setup still needs validating against the full scenarios
before the evasion rate means anything. It is honest work in progress, not a
headline number.

---

## 7. Limitations (30 seconds) — say these before you are asked

> "Three things we would want to be clear about.
>
> All our positive results are on synthetic data. There is no public dataset
> with real shared-attribute links, and we say in the paper what the public AML
> datasets can and cannot test.
>
> Rings that rotate identities or act very slowly evade every method we tested,
> including ours.
>
> And the hub weighting extends reachable recall on shared-attribute rings from
> 0.55 to 0.86, but not at higher precision everywhere - the calibrated scorer
> supplies most of the precision, not the weighting."

---

## Questions you will get

**"Why a graph database?"**
> "Relationships are stored as direct references, so a hop is a pointer follow
> rather than a join. Cost per hop does not grow with the size of the database.
> That is what makes multi-hop traversal affordable during authorisation."

**"Isn't one-over-log-degree just Adamic-Adar?"**
> "Yes - it is Adamic-Adar over the attribute graph, with exponential time decay
> and intra-pair transfer weight added. We cite it. The contribution is applying
> it to streaming ring detection and measuring what it does, not the weight
> function itself."

Say this plainly. It is the question most likely to catch you out, and the
honest answer is stronger than a vague one.

**"What does it cost you?"**
> "Precision, at high recall. And we miss slow and identity-rotating rings
> entirely."

**"Is the chat part novel?"**
> "No - MCP servers for databases exist and so does querying a graph in natural
> language. We claim it as an interface contribution. What is particular is that
> the tools, the live simulator and the answer key sit behind one question box,
> so a question can be asked, answered from live data and checked against ground
> truth in one exchange."

**"What did each of you do?"** / **"What changed recently?"**
> "The four contributions - hub-aware weighting, incremental detection,
> calibrated ring-level alerts and the evasion evaluation - are the detector and
> the dashboard, and that is Roshan's build. The last five days added two things
> on top of it: the question box, which queries the live detector through MCP
> tools and re-draws the graph, and the red-team search, which looks for evasions
> we did not write by hand. Fourteen hundred lines of new modules; ninety-four
> lines added and five removed in the existing code. The detector, the simulator
> and the scorer are untouched."

Then the honest status line on the second one: the search runs, but its isolated
single-ring setup has not been validated against the full scenarios, so there is
no quotable evasion rate from it yet.

⚠️ Resist the temptation to let the four contributions sound like last week's
work. An examiner who looks at the commit history will see the dates, and
"ours sits on top of hers" is a better answer than one that does not survive
being checked.

---

## Known rough edge

If you open a ring explanation live, you may see a ring scored 1.0 whose
explanation reports risk 0, `cycle_present=0` for a ring the detector described
as a cycle, and a counterfactual in the order of 10^46. We found this while
testing and have not yet traced it.

If it appears, do not bluff:

> "That is an inconsistency between the detector and the scorer that we found
> and have not yet fixed - the features look wrong for that ring."

Admitting a known bug reads far better than defending a number you do not trust.

---

## If something breaks

| Problem | Do this |
|---|---|
| `/ui` is blank or 404 | `cd web && npm run build`, restart the API |
| Ask panel says unavailable | API is in Docker; use `make demo` |
| Ask panel errors or hangs | Skip it. Do not retry live |
| Docker died | `docker compose up -d`, wait ~60s for Neo4j |
| Everything is broken | `make demo-story` output at `demo/story.html` is offline and self-contained |

Have `demo/story.html` open in a second tab before you start. It replays a real
run with the answer key and a detector switch, needs no server, and cannot fail.
