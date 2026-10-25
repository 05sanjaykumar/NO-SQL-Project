"""
StaticCypherBaseline detector.

Wraps the original Q1, Q2, Q3 and Q4b queries from queries.cypher as a
detector. It always evaluates the whole graph and is run on a timer
(`incremental = False`). It is the comparison baseline for every
experiment, and its full-graph latency against incremental detection is a
key result (C2).

On a Neo4j store it runs the original Cypher (one change for streaming:
departed users, `active = false`, are excluded). On the in-memory store
it evaluates the same rules in Python, so accuracy sweeps can run without
a database. Latency numbers for the paper come from the Neo4j path only.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import structlog

from fraudsim.detectors.base import BaseDetector, DetectorConfig
from fraudsim.graph.store import GraphStore, GraphView
from fraudsim.types import DetectorType, RingCandidate, RingType

logger = structlog.get_logger(__name__)

# Original queries (queries.cypher), with `WHERE u.active` added for streaming.
Q1_SHARED_DEVICE = """
MATCH (u:User)-[:USED_DEVICE]->(d:Device)
WHERE u.active
WITH d, collect(u.user_id) AS members, count(*) AS ring_size
WHERE ring_size >= $min_size
RETURN d.device_id AS shared, ring_size, members
ORDER BY ring_size DESC
"""

Q2_SHARED_CARD = """
MATCH (u:User)-[:HAS_CARD]->(c:CreditCard)
WHERE u.active
WITH c, collect(u.user_id) AS members, count(*) AS ring_size
WHERE ring_size >= $min_size
RETURN c.card_id AS shared, ring_size, members
ORDER BY ring_size DESC
"""

Q3_MONEY_CYCLE = """
MATCH (a:User)-[:PERFORMED]->(:Transaction)-[:SENT_TO]->(b:User)
      -[:PERFORMED]->(:Transaction)-[:SENT_TO]->(c:User)
      -[:PERFORMED]->(:Transaction)-[:SENT_TO]->(a)
WHERE a.user_id < b.user_id AND a.user_id < c.user_id
  AND a.active AND b.active AND c.active
RETURN DISTINCT a.user_id AS a, b.user_id AS b, c.user_id AS c
"""

Q4_SHARED_IP_CAPPED = """
MATCH (u:User)-[:LOGGED_IN_FROM]->(ip:IPAddress)
WHERE u.active
WITH ip, collect(u.user_id) AS members, count(*) AS ring_size
WHERE ring_size >= $min_size AND ring_size < $max_degree
RETURN ip.ip AS shared, ring_size, members
ORDER BY ring_size DESC
"""


@dataclass(slots=True)
class StaticCypherConfig(DetectorConfig):
    """Configuration for the StaticCypherBaseline detector."""

    detector_type: DetectorType = DetectorType.STATIC_CYPHER
    name: str = "StaticCypherBaseline"
    min_ring_size: int = 3
    max_degree_cap: int = 10  # the degree cap from Q4b (100 reproduces Q4a)
    enable_q1_shared_device: bool = True
    enable_q2_shared_card: bool = True
    enable_q3_money_cycle: bool = True
    enable_q4_shared_ip: bool = True


class StaticCypherBaseline(BaseDetector):
    """The original course-project queries, wrapped as a full-graph detector."""

    incremental = False

    def __init__(
        self,
        client: Any = None,
        config: StaticCypherConfig | None = None,
        store: GraphStore | None = None,
    ) -> None:
        super().__init__(config or StaticCypherConfig(), store)
        self.cfg: StaticCypherConfig = self._config  # type: ignore[assignment]
        self._client = client if client is not None else getattr(store, "client", None)

    def detect(self, view: GraphView) -> list[RingCandidate]:
        """Run every enabled query against the whole graph. The view only supplies `now`."""
        if self.store is not None and self.store.backend == "memory":
            rows = self._rows_in_memory(view.now_t)
        elif self._client is not None:
            rows = self._rows_cypher()
        else:
            raise RuntimeError("StaticCypherBaseline needs a Neo4j client or an in-memory store")
        return [self._to_candidate(kind, row, view) for kind, row in rows]

    # ─── Neo4j path ───────────────────────────────────────────────────

    def _rows_cypher(self) -> list[tuple[str, dict[str, Any]]]:
        cfg, run = self.cfg, self._client.run_query
        rows: list[tuple[str, dict[str, Any]]] = []
        if cfg.enable_q1_shared_device:
            rows += [("Q1", r) for r in run(Q1_SHARED_DEVICE, min_size=cfg.min_ring_size)]
        if cfg.enable_q2_shared_card:
            rows += [("Q2", r) for r in run(Q2_SHARED_CARD, min_size=cfg.min_ring_size)]
        if cfg.enable_q3_money_cycle:
            rows += [("Q3", r) for r in run(Q3_MONEY_CYCLE)]
        if cfg.enable_q4_shared_ip:
            rows += [
                ("Q4b", r)
                for r in run(
                    Q4_SHARED_IP_CAPPED,
                    min_size=cfg.min_ring_size,
                    max_degree=cfg.max_degree_cap,
                )
            ]
        return rows

    # ─── In-memory path (same rules) ──────────────────────────────────

    def _rows_in_memory(self, now_t: float) -> list[tuple[str, dict[str, Any]]]:
        cfg = self.cfg
        assert self.store is not None
        full = self.store.full_view(float("-inf"), now_t)  # static queries see all history
        by_attr: dict[tuple[str, str], list[str]] = defaultdict(list)
        for lk in full.links:
            by_attr[(lk.kind, lk.attr)].append(lk.user)

        rows: list[tuple[str, dict[str, Any]]] = []
        for (kind, attr), users in sorted(by_attr.items()):
            n = len(users)
            row = {"shared": attr, "ring_size": n, "members": sorted(users)}
            if kind == "device" and cfg.enable_q1_shared_device and n >= cfg.min_ring_size:
                rows.append(("Q1", row))
            elif kind == "card" and cfg.enable_q2_shared_card and n >= cfg.min_ring_size:
                rows.append(("Q2", row))
            elif (
                kind == "ip"
                and cfg.enable_q4_shared_ip
                and cfg.min_ring_size <= n < cfg.max_degree_cap
            ):
                rows.append(("Q4b", row))
        if cfg.enable_q3_money_cycle:
            out: dict[str, set[str]] = defaultdict(set)
            for t in full.txns:
                out[t.sender].add(t.receiver)
            for a in sorted(out):
                for b in out[a]:
                    if b <= a:
                        continue
                    for c in out.get(b, ()):
                        if c > a and c != b and a in out.get(c, ()):
                            rows.append(("Q3", {"a": a, "b": b, "c": c}))
        return rows

    # ─── Shared ───────────────────────────────────────────────────────

    def _to_candidate(self, query: str, row: dict[str, Any], view: GraphView) -> RingCandidate:
        if query == "Q3":
            members = {row["a"], row["b"], row["c"]}
            evidence = {"query": "Q3", "cycle": [row["a"], row["b"], row["c"], row["a"]]}
            return self._candidate(RingType.MONEY_CYCLE, members, view, 1.0, evidence)
        ring_type, key = {
            "Q1": (RingType.SHARED_DEVICE, "device_id"),
            "Q2": (RingType.SHARED_CARD, "card_id"),
            "Q4b": (RingType.SHARED_IP, "ip"),
        }[query]
        evidence = {"query": query, "shared": row["shared"], "ring_size": row["ring_size"]}
        if query == "Q4b":
            evidence["degree_cap"] = self.cfg.max_degree_cap
        return self._candidate(
            ring_type, set(row["members"]), view, 1.0, evidence, {key: row["shared"]}
        )
