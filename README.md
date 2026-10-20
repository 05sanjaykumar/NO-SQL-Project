# Real-Time Fraud Ring Detection using Graph Database Architecture

BCSE406L - NoSQL Databases
Sanjay Kumar S (23BCE1248) · Roshan Parveen (23BCE1256)

Detects fraud rings in a financial transaction graph using Neo4j and Cypher
pattern matching, on a small controlled dataset with known planted rings.

## Run it

```bash
./start.sh
```

Or straight from Docker, which is all `start.sh` does:

```bash
docker compose up -d --build
```

Both containers come up together, the dashboard waits for Neo4j to actually
accept queries, and the graph is seeded automatically the first time. About
20 seconds from a clean checkout. **Python is not needed on the host** - it
runs inside the container.

Stop with `./stop.sh` or `docker compose down`. The graph survives in the
Docker volume; add `-v` to wipe it and start fresh.

Then open **http://localhost:8010** for the dashboard - buttons for each query,
a slider for the degree threshold, and a control to regenerate the dataset at
any size.

Neo4j's own browser is still available at http://localhost:7474 (user `neo4j`,
password `fraudring123`) if you want to paste Cypher directly from
`queries.cypher`.

Shut down with `docker compose down` (add `-v` to also wipe the data volume).

## Files

| File | What it does |
|---|---|
| `docker-compose.yml` | Neo4j 5.26, one service |
| `seed.py` | Builds the graph and plants the fraud rings |
| `queries.cypher` | The detection queries |
| `start.sh` / `stop.sh` | Bring the whole project up or down in one command |
| `Dockerfile` | Image for the dashboard service |
| `app.py` | FastAPI service: runs queries, reseeds, scaling benchmark |
| `static/index.html` | Dashboard - Cytoscape graph, query buttons, degree slider |
| `paper/IEEE_PAPER.md` | Write-up in IEEE format |
| `SETUP.md` | Docker and Neo4j setup, commands, troubleshooting |
| `docs/PLAN.md` | Project plan and future scope |

## Data model

```
(:User)-[:USED_DEVICE]->(:Device)
(:User)-[:HAS_CARD]->(:CreditCard)
(:User)-[:LOGGED_IN_FROM]->(:IPAddress)
(:User)-[:PERFORMED]->(:Transaction)-[:SENT_TO]->(:User)
```

30 users, 18 devices, 18 cards, 20 IPs, 40 transactions - 126 nodes. Small on
purpose: the whole graph fits on one screen, and because the rings are planted we
know exactly what each query should return.

The pools are wider than the user count on purpose. Sharing in the background
noise is capped at two users per device, card and IP, and noise transfers that
would close a loop are rejected. Without those caps, chance alone puts three
users on the same device and the accidental clusters look identical to a planted
ring. Every group of three or more in the results is deliberate.

## What is planted

| | Pattern | Expected result |
|---|---|---|
| Device ring | u01, u02, u03, u04 share `device_007` | found by Q1 |
| Card ring | u10, u11, u12 share `card_005` | found by Q2 |
| Cycle | u21 → u22 → u23 → u21 | found by Q3 |
| IP ring | u27, u28, u29 share `ip_004` | found by Q4a and Q4b |
| Hub | 12 users share `ip_001` | **not fraud** - office wifi |

Above about 300 users the generator plants additional rings of each type, with
three to five members each, so the fraudulent fraction stays near 8% instead of
thinning out. At 600 users there are four rings of each type and two cycles; the
structures listed above stay at fixed positions so the default dataset is always
reproducible.

Every user carries a `ring` property recording which planted ring it belongs to,
`null` for legitimate users. No detection query reads it - it exists only so the
results can be checked.

## The degree cap

`ip_001` is shared by twelve ordinary users. Any naive shared-attribute query
treats that as a twelve-member fraud ring.

Run Q4a (cap 100) and Q4b (cap 10) back to back. Same query, one number changed.
Q4a returns ring C plus twelve false positives; Q4b returns ring C alone.

The cost: a genuine ring hiding behind a busy node gets skipped too. Precision
bought with recall.

## Results

Save screenshots into `images/` with these names and they appear below.
Crop to the result panel, and keep the timing line ("completed after N ms")
visible - it is measured evidence for the performance claim in the report.

### The full graph (Q0)

126 nodes, 185 relationships.

![Full graph](images/q0-full-graph.png)

### Shared device ring (Q1)

Four users on `device_007`.

![Shared device ring](images/q1-device-ring.png)

### Shared card ring (Q2)

Three users on `card_005`.

![Shared card ring](images/q2-card-ring.png)

### Circular money flow (Q3)

u21 to u22 to u23 and back.

![Money cycle](images/q3-money-cycle.png)

### Degree cap off (Q4a)

Ring C found, plus twelve false positives from the shared office IP.

![No degree cap](images/q4a-no-cap.png)

### Degree cap on (Q4b)

Same query, threshold changed from 100 to 10. False positives gone.

![Degree cap applied](images/q4b-with-cap.png)

### Summary (Q5)

![Summary table](images/q5-summary.png)

---

## Measured result: the degree cap

Q4a and Q4b are the same query. The only difference is the degree threshold,
changed from 100 to 10. Measured in the Neo4j browser on the seeded graph:

| | Q4a (no cap) | Q4b (cap = 10) |
|---|---|---|
| Nodes returned | 17 | 4 |
| Users flagged | 15 | 3 |
| Of those, actually fraud | 3 | 3 |
| **Precision** | **20%** | **100%** |
| **Recall** | **100%** | **100%** |
| Query time | 302 ms | 74 ms |

Twelve of the fifteen users Q4a flags are innocent - they share `ip_001`, the
office wifi. The cap removes all twelve, and the query also runs about four
times faster because it stops expanding the hub node at all.

### The same experiment across dataset sizes

Type a bigger number in the dataset box and hit **Generate**. Both the office
hub and the number of planted rings scale with the population, so the fraudulent
fraction stays near 8%:

| Users | Nodes | IP rings | False positives without bound | With bound |
|---|---|---|---|---|
| 30 | 126 | 1 | 12 | 0 |
| 200 | 840 | 1 | 63 | 0 |
| 1000 | 4197 | 6 | 330 | 0 |
| 3000 | 12590 | 20 | 997 | 0 |

Without the bound you hand an analyst a review queue that grows with your
customer base rather than with the amount of fraud. With it, precision is 100%
at every size.

Recall stays at 100% in every row - at 3000 users that is 75 ring members across
20 rings, not a single cluster. But it holds only because every planted ring sits
behind a low-degree artefact by construction. A ring sharing a busy node would be
skipped by the same rule that removes the false positives.

Recall stays at 100% here only because ring C sits behind a low-degree IP. That
is a property of this dataset, not a guarantee: a real ring hiding behind a busy
node would be skipped by the same rule that removes the false positives. The cap
buys precision and speed with recall, and the trade is the honest finding.
