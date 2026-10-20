"""
StaticCypherBaseline detector.

Wraps the original Q1-Q5 queries from queries.cypher as a proper detector
implementing the BaseDetector interface. Always runs full-graph — no
incremental optimization. Serves as the comparison baseline for all
experiments.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import structlog

from fraudsim.detectors.base import BaseDetector, DetectorConfig
from fraudsim.graph.neo4j_client import Neo4jClient
from fraudsim.types import DetectorType, RingCandidate, RingStatus, RingType

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class StaticCypherConfig(DetectorConfig):
    """Configuration for the StaticCypherBaseline detector."""

    detector_type: DetectorType = DetectorType.STATIC_CYPHER
    name: str = "StaticCypherBaseline"
    min_ring_size: int = 3
    max_degree_cap: int = 10  # the degree cap from Q4b
    enable_q1_shared_device: bool = True
    enable_q2_shared_card: bool = True
    enable_q3_money_cycle: bool = True
    enable_q4_shared_ip: bool = True


class StaticCypherBaseline(BaseDetector):
    """
    The original queries from the course project, wrapped as a detector.

    Runs the existing Q1 (shared device), Q2 (shared card), Q3 (money cycle),
    and Q4b (shared IP with degree cap) queries against the full graph.

    This detector does NOT support incremental detection — it always
    evaluates the entire graph. It exists purely as a comparison baseline.
    """

    def __init__(
        self,
        client: Neo4jClient,
        config: StaticCypherConfig | None = None,
    ) -> None:
        super().__init__(client, config or StaticCypherConfig())
        self._cfg: StaticCypherConfig = config or StaticCypherConfig()

    def detect(
        self,
        dirty_set: set[str] | None = None,
        ts: datetime | None = None,
    ) -> list[RingCandidate]:
        """
        Run all enabled queries and return detected rings.

        The dirty_set parameter is IGNORED — this baseline always does
        full-graph evaluation. This is deliberate: the latency comparison
        between this and the incremental detectors is a key result (C2).
        """
        candidates: list[RingCandidate] = []

        if self._cfg.enable_q1_shared_device:
            candidates.extend(self._q1_shared_device())

        if self._cfg.enable_q2_shared_card:
            candidates.extend(self._q2_shared_card())

        if self._cfg.enable_q3_money_cycle:
            candidates.extend(self._q3_money_cycle())

        if self._cfg.enable_q4_shared_ip:
            candidates.extend(self._q4_shared_ip())

        logger.info(
            "static_cypher_detect",
            n_candidates=len(candidates),
            queries_enabled={
                "q1": self._cfg.enable_q1_shared_device,
                "q2": self._cfg.enable_q2_shared_card,
                "q3": self._cfg.enable_q3_money_cycle,
                "q4": self._cfg.enable_q4_shared_ip,
            },
        )
        return candidates

    # ─── Q1: Shared device ring ───────────────────────────────────────

    def _q1_shared_device(self) -> list[RingCandidate]:
        """
        Three or more users on the same device.

        Original query:
            MATCH (u:User)-[:USED_DEVICE]->(d:Device)
            WITH d, collect(u.user_id) AS members, count(*) AS ring_size
            WHERE ring_size >= 3
            RETURN d.device_id AS shared_device, ring_size, members
        """
        cypher = """
        MATCH (u:User)-[:USED_DEVICE]->(d:Device)
        WITH d, collect(u.user_id) AS members, count(*) AS ring_size
        WHERE ring_size >= $min_size
        RETURN d.device_id AS shared_device, ring_size, members
        ORDER BY ring_size DESC
        """
        rows = self._client.run_query(cypher, min_size=self._cfg.min_ring_size)
        return [
            RingCandidate(
                ring_id=f"static_device_{row['shared_device']}",
                detector=DetectorType.STATIC_CYPHER,
                ring_type=RingType.SHARED_DEVICE,
                members=set(row["members"]),
                status=RingStatus.CANDIDATE,
                score=1.0,
                evidence={
                    "query": "Q1",
                    "shared_device": row["shared_device"],
                    "ring_size": row["ring_size"],
                },
                shared_attributes={"device_id": row["shared_device"]},
            )
            for row in rows
        ]

    # ─── Q2: Shared card ring ─────────────────────────────────────────

    def _q2_shared_card(self) -> list[RingCandidate]:
        """
        Three or more users on the same credit card.

        Original query:
            MATCH (u:User)-[:HAS_CARD]->(c:CreditCard)
            WHERE COUNT { (:User)-[:HAS_CARD]->(c) } >= 3
            RETURN ...
        """
        cypher = """
        MATCH (u:User)-[:HAS_CARD]->(c:CreditCard)
        WITH c, collect(u.user_id) AS members, count(*) AS ring_size
        WHERE ring_size >= $min_size
        RETURN c.card_id AS shared_card, ring_size, members
        ORDER BY ring_size DESC
        """
        rows = self._client.run_query(cypher, min_size=self._cfg.min_ring_size)
        return [
            RingCandidate(
                ring_id=f"static_card_{row['shared_card']}",
                detector=DetectorType.STATIC_CYPHER,
                ring_type=RingType.SHARED_CARD,
                members=set(row["members"]),
                status=RingStatus.CANDIDATE,
                score=1.0,
                evidence={
                    "query": "Q2",
                    "shared_card": row["shared_card"],
                    "ring_size": row["ring_size"],
                },
                shared_attributes={"card_id": row["shared_card"]},
            )
            for row in rows
        ]

    # ─── Q3: Circular money flow ──────────────────────────────────────

    def _q3_money_cycle(self) -> list[RingCandidate]:
        """
        A pays B, B pays C, C pays A (3-hop cycle).

        Original query:
            MATCH p = (a:User)-[:PERFORMED]->(:Transaction)-[:SENT_TO]->(b:User)
                      -[:PERFORMED]->(:Transaction)-[:SENT_TO]->(c:User)
                      -[:PERFORMED]->(:Transaction)-[:SENT_TO]->(a)
            WHERE a.user_id < b.user_id AND a.user_id < c.user_id
        """
        cypher = """
        MATCH (a:User)-[:PERFORMED]->(:Transaction)-[:SENT_TO]->(b:User)
              -[:PERFORMED]->(:Transaction)-[:SENT_TO]->(c:User)
              -[:PERFORMED]->(:Transaction)-[:SENT_TO]->(a)
        WHERE a.user_id < b.user_id AND a.user_id < c.user_id
        RETURN a.user_id AS a, b.user_id AS b, c.user_id AS c
        """
        rows = self._client.run_query(cypher)
        return [
            RingCandidate(
                ring_id=f"static_cycle_{row['a']}_{row['b']}_{row['c']}",
                detector=DetectorType.STATIC_CYPHER,
                ring_type=RingType.MONEY_CYCLE,
                members={row["a"], row["b"], row["c"]},
                status=RingStatus.CANDIDATE,
                score=1.0,
                evidence={
                    "query": "Q3",
                    "cycle": [row["a"], row["b"], row["c"], row["a"]],
                },
            )
            for row in rows
        ]

    # ─── Q4b: Shared IP with degree cap ──────────────────────────────

    def _q4_shared_ip(self) -> list[RingCandidate]:
        """
        Three or more users on the same IP, with a degree cap to filter hubs.

        Original query (Q4b):
            MATCH (u:User)-[:LOGGED_IN_FROM]->(ip:IPAddress)
            WHERE COUNT { (:User)-[:LOGGED_IN_FROM]->(ip) } >= 3
              AND COUNT { (:User)-[:LOGGED_IN_FROM]->(ip) } < 10
        """
        cypher = """
        MATCH (u:User)-[:LOGGED_IN_FROM]->(ip:IPAddress)
        WITH ip, collect(u.user_id) AS members, count(*) AS ring_size
        WHERE ring_size >= $min_size AND ring_size < $max_degree
        RETURN ip.ip AS shared_ip, ring_size, members
        ORDER BY ring_size DESC
        """
        rows = self._client.run_query(
            cypher,
            min_size=self._cfg.min_ring_size,
            max_degree=self._cfg.max_degree_cap,
        )
        return [
            RingCandidate(
                ring_id=f"static_ip_{row['shared_ip']}",
                detector=DetectorType.STATIC_CYPHER,
                ring_type=RingType.SHARED_IP,
                members=set(row["members"]),
                status=RingStatus.CANDIDATE,
                score=1.0,
                evidence={
                    "query": "Q4b",
                    "shared_ip": row["shared_ip"],
                    "ring_size": row["ring_size"],
                    "degree_cap": self._cfg.max_degree_cap,
                },
                shared_attributes={"ip": row["shared_ip"]},
            )
            for row in rows
        ]
