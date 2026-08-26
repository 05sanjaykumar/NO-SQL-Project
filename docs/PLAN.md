# Project Plan — Fraud Ring Detection with Neo4j

**Course:** BCSE406L — NoSQL Databases
**Team:** Sanjay Kumar S (23BCE1248), Roshan Parveen (23BCE1256)
**Approach:** Small working model demonstrating the core concept.
Advanced pieces documented as future scope.

---

## Tier 1 — The actual project (one evening)

Three files. This alone is a complete, defensible submission.

```
docker-compose.yml    Neo4j, one service, 12 lines
seed.py               Builds the demo graph
queries.cypher        The detection queries
```

**Visualization = Neo4j Browser.** It renders graphs natively, colours by label,
lets you click nodes to inspect them. No frontend needed. "We used Neo4j's built-in
graph visualization" is a real answer, and the screenshots look good in the report.

### The graph (~100 nodes, fits on one screen)

| Node | Count |
|---|---|
| `:User` | 30 |
| `:Device` | 10 |
| `:CreditCard` | 12 |
| `:IPAddress` | 8 |
| `:Transaction` | ~40 |

**Relationships** (names taken from the DA-1 report):
```
(:User)-[:USED_DEVICE]->(:Device)
(:User)-[:HAS_CARD]->(:CreditCard)
(:User)-[:LOGGED_IN_FROM]->(:IPAddress)
(:User)-[:PERFORMED]->(:Transaction)-[:SENT_TO]->(:User)
```

**Planted, hardcoded so you know what should come back:**
- Ring A — 4 users sharing `device_007`
- Ring B — 3 users sharing `card_005`
- Cycle  — `u11 → u19 → u26 → u11`
- Hub    — `ip_001` shared by 12 users (office wifi, *not* fraud)

### Three queries

**Q1 — Shared device ring.** Users converging on one device, count >= 3.
**Q2 — Money cycle.** Three transfers that return to the sender.
**Q3 — Shared IP with a degree cap.** Same shape as Q1 over `:LOGGED_IN_FROM`, plus:

```cypher
WHERE COUNT { (:User)-[:LOGGED_IN_FROM]->(ip) } < $maxDegree
```

Q3 is one extra `WHERE` line and it earns its keep: run it at `maxDegree = 100` and
the office wifi hub floods the output with false positives; run it at `10` and only
real rings return. Two screenshots side by side and your "degree-bounded traversal"
claim is demonstrated. Cheapest win in the project.

### Done when
All three queries return the planted rings in Neo4j Browser, and you have
screenshots of each.

---

## Tier 2 — Optional polish (half a day)

Only if you want it to look nicer than Neo4j Browser.

```
app.py           FastAPI, ~50 lines, one endpoint
static/index.html  Cytoscape from a <script> tag, dropdown + slider
```

Adds a dropdown to pick the query and a slider for `maxDegree`. Genuinely nicer for
a live demo, but Tier 1 already satisfies the requirement.

---

## Tier 3 — Future scope (write, don't build)

Paste-ready for the report:

> **Future Scope**
>
> The present implementation demonstrates the core graph-based detection model on a
> controlled dataset. The following extensions are identified for future work:
>
> 1. **Large-scale evaluation** — Extending the dataset to millions of transactions
>    to empirically measure the latency advantage of index-free adjacency over
>    relational multi-join queries at production scale.
> 2. **Real-time streaming ingestion** — Replacing batch seeding with a continuous
>    event stream (Kafka or similar) to support live authorization-time scoring,
>    addressing the ETL batch-sync limitation identified in Kowshalya et al.
> 3. **Adaptive degree thresholds** — Learning per-node-type degree bounds from
>    graph topology instead of using a fixed cap, to reduce the recall loss the
>    current static threshold introduces.
> 4. **Graph algorithm integration** — Applying Neo4j GDS community detection
>    (Louvain) and centrality measures to surface rings that pure pattern matching
>    cannot express.
> 5. **Hybrid ML scoring** — Combining topological features with gradient-boosted
>    classifiers, following the approach of Kowshalya et al., to move from binary
>    rule matching to a continuous risk score.
> 6. **Comparative RDBMS benchmarking** — A controlled study against an equivalent
>    normalized relational schema across increasing traversal depths.

Six items, each traceable to a limitation in your literature survey. This section
does more for the marks than half of it would if you built it badly.

---

## Split

**Sanjay:** Docker + `seed.py` + queries (all of Tier 1)
**Roshan:** Screenshots, report sections, future scope writeup — and Tier 2 if you
do it

If you skip Tier 2, Roshan owns the report and demo script so the workload stays even.

---

## Viva prep

Five answers, both of you:

1. **Why graph over SQL?** Relationships are stored as direct pointers. Each hop is
   a pointer follow, not a join. Cost per hop stays flat as the DB grows.
2. **Index-free adjacency?** That — named.
3. **What does the degree cap do?** Skips hyper-connected nodes like a shared office
   IP that would otherwise link everyone to everyone.
4. **What does the cap cost?** If a real ring sits behind a busy node, you miss it.
   Precision traded for recall.
5. **Why NoSQL specifically?** No fixed schema. New entity type = new label, not a
   migration.

If asked why the dataset is small: it's a controlled demonstration dataset with
known ground-truth rings, so detection correctness can be verified directly. That's
true and it's a good answer.

---

## Open
- Submission date?
- Live demo or screenshots?
