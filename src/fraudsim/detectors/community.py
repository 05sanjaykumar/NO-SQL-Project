"""
CommunityDetector: community detection over a rolling user-user projection
(an alternative baseline).

The projection links two active users with weight = number of shared
attributes + number of transactions between them inside the rolling window.
No hub weighting is applied; that is the point of comparison with C1.

- Neo4j store: Neo4j GDS (`gds.louvain.stream` or `gds.wcc.stream`) on a Cypher
  aggregation projection, run full-graph on a timer.
- In-memory store: networkx (Louvain with a fixed seed, or connected
  components). The implementations differ, so community ids and boundaries
  may differ slightly between backends.

Communities with size in [min_community_size, max_community_size] and edge
density >= min_density become candidates. With algorithm="wcc" this is the
"plain connected components" baseline.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from fraudsim.detectors.base import BaseDetector, DetectorConfig
from fraudsim.graph.store import GraphStore, GraphView
from fraudsim.types import DetectorType, RingCandidate, RingType

_ATTR_RELS = "USED_DEVICE|HAS_CARD|LOGGED_IN_FROM"

_PROJECT = f"""
CALL () {{
    MATCH (u:User)-[:{_ATTR_RELS}]->(a)<-[:{_ATTR_RELS}]-(v:User)
    WHERE u.active AND v.active AND u.user_id < v.user_id
    RETURN u, v, 1.0 AS w
    UNION ALL
    MATCH (s:User)-[:PERFORMED]->(t:Transaction)-[:SENT_TO]->(r:User)
    WHERE s.active AND r.active AND t.t >= $since AND s <> r
    RETURN CASE WHEN s.user_id < r.user_id THEN s ELSE r END AS u,
           CASE WHEN s.user_id < r.user_id THEN r ELSE s END AS v,
           1.0 AS w
}}
WITH u, v, sum(w) AS weight
WITH gds.graph.project($name, u, v,
        {{relationshipProperties: {{weight: weight}}}},
        {{undirectedRelationshipTypes: ['*']}}) AS g
RETURN g.nodeCount AS nodes, g.relationshipCount AS rels
"""


@dataclass(slots=True)
class CommunityConfig(DetectorConfig):
    """Configuration for the CommunityDetector."""

    detector_type: DetectorType = DetectorType.COMMUNITY
    name: str = "CommunityDetector"
    algorithm: str = "louvain"  # or "wcc"
    min_community_size: int = 3
    max_community_size: int = 25
    min_density: float = 0.5
    seed: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


class CommunityDetector(BaseDetector):
    """Louvain / WCC communities over a rolling projection."""

    incremental = False

    def __init__(self, config: CommunityConfig | None = None, store: GraphStore | None = None):
        super().__init__(config or CommunityConfig(), store)
        self.cfg: CommunityConfig = self._config  # type: ignore[assignment]
        if self.cfg.algorithm not in ("louvain", "wcc"):
            raise ValueError("algorithm must be 'louvain' or 'wcc'")

    def detect(self, view: GraphView) -> list[RingCandidate]:
        if self.store is not None and self.store.backend == "neo4j":
            communities = self._gds(view.since_t)
        else:
            communities = self._networkx(view)
        edges = self._edge_index(view)
        out = []
        for members in communities:
            n = len(members)
            if not self.cfg.min_community_size <= n <= self.cfg.max_community_size:
                continue
            ordered = sorted(members)
            present = sum(
                1 for i, u in enumerate(ordered) for v in ordered[i + 1 :] if (u, v) in edges
            )
            density = present / (n * (n - 1) / 2)
            if density < self.cfg.min_density:
                continue
            evidence = {
                "algorithm": self.cfg.algorithm,
                "backend": self.store.backend if self.store else "memory",
                "size": n,
                "edge_density": round(density, 4),
            }
            out.append(
                self._candidate(RingType.MIXED_ATTRIBUTE, set(members), view, density, evidence)
            )
        return out

    @staticmethod
    def _edge_index(view: GraphView) -> dict[tuple[str, str], float]:
        weights: dict[tuple[str, str], float] = defaultdict(float)
        for links in view.links_by_attr().values():
            users = sorted({lk.user for lk in links})
            for i, u in enumerate(users):
                for v in users[i + 1 :]:
                    weights[(u, v)] += 1.0
        for t in view.txns:
            if t.sender != t.receiver:
                weights[(min(t.sender, t.receiver), max(t.sender, t.receiver))] += 1.0
        return weights

    def _networkx(self, view: GraphView) -> list[set[str]]:
        graph = nx.Graph()
        for (u, v), w in sorted(self._edge_index(view).items()):
            graph.add_edge(u, v, weight=w)
        if graph.number_of_edges() == 0:
            return []
        if self.cfg.algorithm == "wcc":
            return [set(c) for c in nx.connected_components(graph)]
        return [
            set(c)
            for c in nx.community.louvain_communities(graph, weight="weight", seed=self.cfg.seed)
        ]

    def _gds(self, since_t: float) -> list[set[str]]:
        assert self.store is not None
        client = self.store.client  # type: ignore[attr-defined]
        name = f"fraudsim_comm_{uuid.uuid4().hex[:8]}"
        projected = client.run_query(_PROJECT, name=name, since=since_t)
        if not projected or projected[0]["nodes"] is None:
            return []  # no user-user pairs yet: the aggregation produced no graph
        try:
            proc = "gds.louvain.stream" if self.cfg.algorithm == "louvain" else "gds.wcc.stream"
            config: dict[str, Any] = {"concurrency": 1}
            if self.cfg.algorithm == "louvain":
                config["relationshipWeightProperty"] = "weight"
            rows = client.run_query(
                f"CALL {proc}($name, $config) YIELD nodeId, communityId "
                f"RETURN gds.util.asNode(nodeId).user_id AS u, communityId AS c",
                name=name,
                config=config,
            )
        finally:
            client.run_query("CALL gds.graph.drop($name, false) YIELD graphName", name=name)
        groups: dict[int, set[str]] = defaultdict(set)
        for r in rows:
            groups[int(r["c"])].add(str(r["u"]))
        return list(groups.values())
