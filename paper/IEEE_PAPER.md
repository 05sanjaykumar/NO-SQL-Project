# Degree-Bounded Cypher Pattern Matching for Fraud Ring Detection in Property Graph Databases

**Sanjay Kumar S** (23BCE1248), **Roshan Parveen** (23BCE1256)
School of Computer Science and Engineering
Vellore Institute of Technology, Chennai, India

*Course: BCSE406L - NoSQL Databases*

---

## Abstract

Financial fraud rings operate by distributing activity across many synthetic
identities that individually appear legitimate, while sharing underlying
physical and digital artefacts such as devices, payment instruments and network
addresses. Detecting such rings requires reasoning over the relationships
between accounts rather than the attributes of any single account. Relational
schemas express these relationships through join tables, so multi-hop
relationship queries incur compounding join cost as traversal depth increases.
This work implements a property graph model of a financial ecosystem in Neo4j
and evaluates Cypher pattern-matching queries for three fraud indicators:
shared-device clusters, shared-credential clusters and circular monetary flows.
We further evaluate a degree-bounded variant of shared-attribute matching, in
which nodes whose degree exceeds a configured threshold are excluded from
traversal, to address the known supernode problem in which legitimately
high-degree entities generate large volumes of false positives. On a controlled
dataset of 126 nodes and 185 relationships containing deliberately planted
ground-truth rings, degree bounding improved detection precision from 20% to
100% while leaving recall unchanged at 100%. Repeating the experiment at scales
up to 12,590 nodes, with both the legitimate hub and the number of planted rings
growing with the population, shows that the unbounded query accumulates false
positives in proportion to the user base, from 12 to 997, while the bounded
query returns exactly the planted rings at every scale. A separate measurement distinguishes anchored traversal, whose
latency is flat across a hundredfold increase in graph size, from label scans,
whose latency is not. We characterise the precision-recall trade-off inherent to
degree bounding and identify adaptive threshold selection as the principal
direction for further work.

**Keywords** - NoSQL, graph database, Neo4j, Cypher, fraud detection,
property graph, index-free adjacency, supernode problem

---

## I. Introduction

Modern digital payment platforms process transactions across large populations
of accounts, devices and network endpoints. Organised financial fraud
increasingly takes the form of *fraud rings*: a single operator controls a
collection of synthetic identities, each presenting as an independent customer.
Examined in isolation, an individual account within such a ring exhibits no
anomalous attributes. The signal that distinguishes the ring is structural - the
accounts converge on a small number of shared artefacts, because the operator
controls a limited set of physical devices, payment credentials and network
connections.

Detecting this pattern is therefore a relationship query rather than an
attribute query. In a normalised relational schema, the association between
users and devices is stored in a junction table, and identifying groups of users
sharing a device requires a self-join across that table. Extending the query to
additional hops - for example, users who share a device with users who share a
payment card with a third group - requires a further join per hop. The
intermediate result set grows multiplicatively with depth, which constrains the
feasible traversal depth under the latency budget of an online authorisation
decision.

Property graph databases store relationships as direct references between
records, a representation commonly termed *index-free adjacency* [4]. Traversing
a relationship is a pointer dereference whose cost is independent of total
database cardinality, making deep traversal tractable.

This paper reports an implementation and evaluation of graph-based fraud ring
detection in Neo4j. The contributions are:

1. A property graph schema modelling users, devices, payment credentials,
   network addresses and transactions, with Cypher pattern queries for three
   distinct fraud indicators.
2. An empirical evaluation of degree-bounded pattern matching as a mitigation
   for false positives arising from legitimately high-degree nodes, measured
   across four dataset scales, showing that the review burden imposed by
   omitting the bound grows in proportion to the user population.
3. A measurement separating anchored traversal from label scanning, clarifying
   which detection queries actually inherit the size-independence that
   index-free adjacency provides.
4. A characterisation of the precision-recall trade-off introduced by a fixed
   degree threshold, and identification of adaptive thresholding as the
   principal limitation to be addressed.

We note explicitly that degree-based pruning is an established technique in
graph query processing. The contribution here is an applied evaluation of its
effect on detection quality in a fraud-ring context, not the introduction of a
new algorithm.

---

## II. Related Work

Kazi and Patil [1] construct a directed property graph of financial entities in
Neo4j and apply centrality and community detection to identify money-laundering
rings across institutions. Their evaluation reports query execution time and
traversal depth, and identifies memory consumption during full-graph centrality
computation as a limiting factor on large static snapshots.

Kowshalya et al. [2] combine graph-derived topological features with gradient
boosted classifiers, extracting degree centrality and PageRank from a Neo4j
store and supplying them alongside tabular transaction attributes to an XGBoost
model. They report a ROC-AUC of 95.4% with sub-100 ms response time, but require
periodic batch synchronisation between the transactional relational store and
the graph store.

Al-Quraishi et al. [3] apply index-free adjacency and recursive Cypher
path-finding to detect closed-cycle transfers across inter-bank networks,
reporting an 88% reduction in query latency relative to an equivalent relational
implementation. They identify performance degradation when traversals encounter
high-degree hub nodes such as major merchant accounts.

Across these works, two recurring limitations emerge: the computational cost of
whole-graph analytics, and performance and precision degradation at high-degree
nodes. The present work addresses the second of these.

---

## III. Proposed Methodology

### A. Graph Data Model

Entities are modelled as labelled nodes and their associations as typed directed
relationships:

**Nodes:** `:User`, `:Device`, `:CreditCard`, `:IPAddress`, `:Transaction`

**Relationships:**

```
(:User)-[:USED_DEVICE]->(:Device)
(:User)-[:HAS_CARD]->(:CreditCard)
(:User)-[:LOGGED_IN_FROM]->(:IPAddress)
(:User)-[:PERFORMED]->(:Transaction)-[:SENT_TO]->(:User)
```

Uniqueness constraints are declared on all identifier properties, which in
Neo4j additionally provisions a supporting index.

### B. Detection Patterns

Three fraud indicators are expressed as Cypher patterns.

**P1 - Shared-attribute clustering.** Three or more distinct users associated
with a single device or payment credential:

```cypher
MATCH p = (u:User)-[:USED_DEVICE]->(d:Device)
WHERE COUNT { (:User)-[:USED_DEVICE]->(d) } >= 3
RETURN p
```

**P2 - Circular monetary flow.** A closed cycle of transfers returning to the
originating account. The ordering predicate on user identifiers suppresses the
rotational duplicates that would otherwise return each cycle once per member:

```cypher
MATCH p = (a:User)-[:PERFORMED]->(:Transaction)-[:SENT_TO]->(b:User)
          -[:PERFORMED]->(:Transaction)-[:SENT_TO]->(c:User)
          -[:PERFORMED]->(:Transaction)-[:SENT_TO]->(a)
WHERE a.user_id < b.user_id AND a.user_id < c.user_id
RETURN p
```

**P3 - Shared network endpoint.** Structurally identical to P1 over the
`:LOGGED_IN_FROM` relationship.

### C. Degree-Bounded Pattern Matching

P3 as stated is unsound in practice. Network endpoints are shared legitimately
at scale - corporate gateways, mobile carrier NAT pools and public wireless
networks associate large numbers of unrelated users with a single address.
Applying a shared-attribute rule to such a node produces a false positive
proportional to its degree.

The informal principle is that the discriminative value of a shared artefact
decreases as the number of entities sharing it increases. Four users on one
device is strong evidence of common control; ten thousand users on one public
address is evidence of nothing.

We therefore constrain the pattern to exclude nodes whose degree exceeds a
threshold `t`:

```cypher
MATCH p = (u:User)-[:LOGGED_IN_FROM]->(ip:IPAddress)
WHERE COUNT { (:User)-[:LOGGED_IN_FROM]->(ip) } >= 3
  AND COUNT { (:User)-[:LOGGED_IN_FROM]->(ip) } < $t
RETURN p
```

Besides suppressing false positives, the bound prevents the query planner from
expanding the full neighbourhood of a high-degree node, which is the dominant
cost term when such nodes are present.

---

## IV. Implementation

The system comprises a Neo4j 5.26 Community instance in a Docker container and
a Python data generator using the official Neo4j driver. The generator operates
in two phases. It first produces background activity in which each user is
assigned a device, a payment credential and a network address. It then
deliberately plants known fraud structures by overwriting a subset of these
assignments.

Background sharing is capped at two users per artefact. Without this constraint,
random assignment over a finite artefact pool produces incidental clusters of
three or more users that are structurally indistinguishable from planted rings,
confounding the evaluation. Candidate background transfers that would close a
cycle are likewise rejected at generation time.

Dataset size is a parameter. Artefact pools and the size of the legitimate hub
scale with the user count, so the planted structures remain detectable and the
degree bound remains meaningful at any scale. The default configuration produces
126 nodes and 185 relationships: 30 users, 18 devices, 18 credentials, 20
network addresses and 40 transactions.

A browser-based control surface was implemented over a FastAPI service to
execute the detection patterns, adjust the degree bound interactively, regenerate
the dataset at arbitrary scale and render matched subgraphs. The interface is an
operational convenience rather than a contribution; it is reported here because
the scaling measurements in Section V-C were collected through it.

**Planted ground truth at the default size:**

| Structure | Composition |
|---|---|
| Device ring | 4 users sharing one device |
| Card ring | 3 users sharing one credential |
| IP ring | 3 users sharing one network address |
| Cycle | 3 users in a closed transfer loop, with decreasing amounts |
| Hub | 12 users sharing one network address - legitimate, not fraud |

The number of planted rings scales with the user count, holding the fraudulent
fraction near 8%, while the structures above remain at fixed positions so the
default configuration is reproducible. At 3000 users the generator plants 20
rings of each type, with sizes drawn between three and five members.

The hub is the adversarial case for P3. It is structurally identical to Ring C
and distinguishable only by degree.

---

## V. Results and Discussion

### A. Detection Accuracy

Each pattern query was executed against the seeded graph and compared to the
planted ground truth.

| Pattern | Target | Returned | Correct |
|---|---|---|---|
| P1 (device) | Ring A | 4 users | Yes |
| P1 (credential) | Ring B | 3 users | Yes |
| P2 (cycle) | Cycle | 1 cycle | Yes |

All three recovered the planted structure exactly, with no spurious results.

### B. Effect of Degree Bounding

P3 was executed with the degree threshold effectively disabled (`t = 100`) and
with it active (`t = 10`).

| Metric | t = 100 | t = 10 |
|---|---|---|
| Nodes returned | 17 | 4 |
| Users flagged | 15 | 3 |
| True positives | 3 | 3 |
| False positives | 12 | 0 |
| **Precision** | **20.0%** | **100.0%** |
| **Recall** | **100.0%** | **100.0%** |
| **Latency** | **302 ms** | **74 ms** |

With the bound disabled, the twelve users of the legitimate hub are returned as
a detected ring, yielding 20% precision. Enabling the bound excludes the hub,
eliminating all twelve false positives. Latency falls by approximately 75%, as
the planner no longer expands the hub neighbourhood.

### C. Scaling Behaviour

To establish whether the result depends on dataset size, the graph was
regenerated at four scales. Both the legitimate hub and the number of planted
rings scale with the population, so the proportion of fraudulent accounts stays
roughly constant rather than thinning out as the graph grows.

TABLE III. DEGREE BOUNDING ACROSS DATASET SCALE

| Users | Nodes | IP rings | Bound | Flagged | TP | FP | Precision | Recall |
|---|---|---|---|---|---|---|---|---|
| 30 | 126 | 1 | none | 15 | 3 | 12 | 20.00% | 100% |
| 30 | 126 | 1 | t=10 | 3 | 3 | 0 | 100% | 100% |
| 200 | 840 | 1 | none | 66 | 3 | 63 | 4.55% | 100% |
| 200 | 840 | 1 | t=10 | 3 | 3 | 0 | 100% | 100% |
| 1000 | 4197 | 6 | none | 356 | 26 | 330 | 7.30% | 100% |
| 1000 | 4197 | 6 | t=10 | 26 | 26 | 0 | 100% | 100% |
| 3000 | 12590 | 20 | none | 1072 | 75 | 997 | 7.00% | 100% |
| 3000 | 12590 | 20 | t=10 | 75 | 75 | 0 | 100% | 100% |

The quantity that scales cleanly is the false positive count, which rises from
12 to 997 across the range. It is governed by the degree of the legitimate hub,
and that degree grows with the user population. In operational terms the
unbounded query hands an analyst a review queue proportional to the size of the
customer base, irrespective of how much fraud is actually present.

Unbounded precision does not decay monotonically; it falls from 20.00% to 4.55%
and then settles near 7%. This is because true positives grow with the
population as well, once the number of planted rings scales. Precision is
therefore a ratio between two quantities that both increase, and its value
depends on the assumed fraud prevalence. The bounded variant avoids this
dependence entirely: it returns exactly the planted rings at every scale, so its
precision is 100% regardless of prevalence.

Recall is 100% in every configuration. At the largest scale this is measured
across 20 rings totalling 75 users rather than a single three-member cluster, so
it is a more substantive result than a single-ring experiment would give.
It nonetheless remains a property of the construction: every planted ring sits
behind a low-degree artefact by design, and a ring sharing a high-degree one
would be excluded by the same predicate that removes the false positives.

A second experiment distinguishes two query shapes. A *scanning* query matches
every `(:User)-[:USED_DEVICE]->(:Device)` pair before filtering. An *anchored*
query begins at one named user and expands outward through its relationships.

TABLE IV. QUERY LATENCY BY SHAPE AND SCALE (MEDIAN OF 5)

| Users | Nodes | Scanning | Anchored |
|---|---|---|---|
| 30 | 126 | 2.9 ms | 5.6 ms |
| 200 | 840 | 5.4 ms | 4.7 ms |
| 1000 | 4197 | 6.4 ms | 2.2 ms |
| 3000 | 12590 | 10.0 ms | 4.7 ms |

Across a hundredfold increase in graph size, anchored traversal latency shows no
upward trend, varying between 2.2 ms and 5.6 ms with no relation to cardinality.
Scanning latency rises steadily from 2.9 ms to 10.0 ms. This is the expected
consequence of index-free adjacency: the cost of an anchored traversal is bound
to the local neighbourhood, whereas a label scan is proportional to the number
of matching records.

We note that this distinction is often elided in informal descriptions of graph
databases. Index-free adjacency makes *local traversal* independent of database
size; it does not make whole-label scans free. The detection queries in Section
III-B are scanning queries, and their cost therefore does grow with the user
population, as Table IV shows.

### D. Discussion

Recall is unchanged in this experiment, but this is a property of the dataset
rather than a guarantee of the method. Ring C was planted behind a low-degree
node and therefore survives the bound. A ring whose shared artefact also carried
legitimate high-degree traffic would be excluded by the same predicate that
removes the false positives.

The technique therefore exchanges recall for precision and latency. The
magnitude of that exchange is governed entirely by the threshold, which in this
implementation is a fixed value selected by inspection of the degree
distribution. This is the principal methodological weakness of the approach as
implemented.

---

## VI. Limitations

1. **Dataset provenance.** The evaluation uses synthetic data with
   self-planted ground truth across sizes from 126 to 12,590 nodes. The scaling
   trend in Tables III and IV is therefore established within that range only,
   on a single machine with a warm page cache. The data is not drawn from a real
   financial system, so detection quality against genuine fraud is unevaluated.

2. **No relational baseline.** The motivating argument regarding join cost is
   drawn from the literature and from the structural properties of the two data
   models. No equivalent relational implementation was constructed and measured
   in this work.

3. **Fixed threshold.** The degree bound is a hand-selected constant, uniform
   across node types. No principled selection procedure is proposed.

4. **Scan-bound detection queries.** Table IV demonstrates size-independent
   latency for anchored traversal, but the detection patterns of Section III-B
   are label scans and remain proportional to the user population. Reformulating
   them as anchored traversals from incoming events, rather than periodic
   whole-graph scans, is necessary before the real-time claim holds at
   production scale.

---

## VII. Conclusion and Future Work

We implemented a property graph model of a financial ecosystem in Neo4j and
evaluated Cypher pattern matching for shared-attribute fraud rings and circular
monetary flows. All planted structures were recovered exactly. Applying a degree
bound to shared-attribute matching raised precision from 20% to 100% and reduced
latency by approximately 75% on the evaluation dataset, at a recall cost that
was zero for this data but is not guaranteed in general.

Future work follows directly from the limitations. **Adaptive thresholding** -
deriving the degree bound per node type from the observed degree distribution
rather than fixing it by inspection - addresses the principal weakness and is
the most substantive open question. **Evaluation at scale** on a public fraud
dataset would establish whether the latency characteristics hold beyond this
controlled setting. **A controlled relational comparison** would substantiate
the join-cost argument empirically. **Streaming ingestion** would permit
evaluation under continuous update rather than static snapshot. Finally,
**integration with topological feature learning**, following Kowshalya et al.
[2], would extend the system from binary rule matching to continuous risk
scoring.

---

## References

[1] E. S. Kazi and A. R. Patil, "Financial Crime Analysis using Complex Network
Analysis and Graph Databases," in *2024 International Conference on Circuit,
Power and Computing Technologies (ICCPCT)*, IEEE, 2024, pp. 1102-1107.

[2] R. A. Kowshalya, V. S. Kumar, and M. R. Selvan, "Detection of Financial
Fraud Using Graph Databases and Machine Learning," *IEEE Access*, vol. 12,
pp. 45210-45222, 2024.

[3] M. S. Al-Quraishi, K. H. Ahmed, and T. M. Rahal, "Graph-Based Anti-Money
Laundering and Fraud Ring Detection in Banking Systems," *Springer Journal of
Banking and Financial Technology*, vol. 8, no. 1, pp. 45-59, 2024.

[4] I. Robinson, J. Webber, and E. Eifrem, *Graph Databases: New Opportunities
for Connected Data*, 2nd ed. Sebastopol, CA, USA: O'Reilly Media, 2023.
