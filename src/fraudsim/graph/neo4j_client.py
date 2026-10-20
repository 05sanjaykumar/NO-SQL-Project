"""
Neo4j access layer.

Provides batched writes (UNWIND), connection pooling, index/constraint
management, and TTL-based pruning. All Neo4j interaction goes through
this module — no other code imports the neo4j driver directly.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import structlog
from neo4j import Driver, GraphDatabase, Session

from fraudsim.types import GROUND_TRUTH_ONLY_EVENTS, Event, EventType

logger = structlog.get_logger(__name__)


# ─── Schema ───────────────────────────────────────────────────────────

CONSTRAINTS = [
    "CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.user_id IS UNIQUE",
    "CREATE CONSTRAINT device_id IF NOT EXISTS FOR (d:Device) REQUIRE d.device_id IS UNIQUE",
    "CREATE CONSTRAINT card_id IF NOT EXISTS FOR (c:CreditCard) REQUIRE c.card_id IS UNIQUE",
    "CREATE CONSTRAINT ip_addr IF NOT EXISTS FOR (i:IPAddress) REQUIRE i.ip IS UNIQUE",
    "CREATE CONSTRAINT txn_id IF NOT EXISTS FOR (t:Transaction) REQUIRE t.txn_id IS UNIQUE",
]

# No index on User.ring: ring labels live in the ground-truth ledger, never on the graph.
# (Only the legacy seed.py still writes that property, for the original course demo.)
INDEXES = [
    "CREATE INDEX txn_ts IF NOT EXISTS FOR (t:Transaction) ON (t.ts)",
    "CREATE INDEX user_ts IF NOT EXISTS FOR (u:User) ON (u.created_at)",
]


class Neo4jClient:
    """
    Thin wrapper around the Neo4j driver.

    Provides connection lifecycle management, schema setup, batched writes,
    and graph queries used by detectors and the metrics subsystem.
    """

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "fraudring123",
        database: str = "neo4j",
        max_connection_pool_size: int = 50,
    ) -> None:
        self._uri = uri
        self._database = database
        self._driver: Driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            max_connection_pool_size=max_connection_pool_size,
        )
        logger.info("neo4j_client_created", uri=uri, database=database)

    def close(self) -> None:
        """Close the driver and all connections."""
        self._driver.close()
        logger.info("neo4j_client_closed")

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """Context manager for a Neo4j session."""
        with self._driver.session(database=self._database) as s:
            yield s

    def wait_for_ready(self, attempts: int = 15, delay: float = 2.0) -> None:
        """Block until Neo4j is reachable or raise SystemExit."""
        for n in range(attempts):
            try:
                self._driver.verify_connectivity()
                logger.info("neo4j_ready", attempt=n + 1)
                return
            except Exception:
                logger.warning("neo4j_not_ready", attempt=n + 1, max=attempts)
                time.sleep(delay)
        raise SystemExit("Could not reach Neo4j — is `docker compose up -d` running?")

    def ensure_schema(self) -> None:
        """Create constraints and indexes if they don't exist."""
        with self.session() as session:
            for stmt in CONSTRAINTS:
                session.run(stmt)
            for stmt in INDEXES:
                session.run(stmt)
        logger.info("neo4j_schema_ensured")

    def wipe(self) -> None:
        """Delete all nodes and relationships. Use with care."""
        with self.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        logger.warning("neo4j_wiped")

    # ─── Batched writes ───────────────────────────────────────────────

    def write_events_batch(self, events: list[Event]) -> dict[str, int]:
        """
        Write a batch of events to the graph using UNWIND for performance.

        Consecutive events of the same type are written with one UNWIND query.
        Order across types is preserved (a link followed by an unlink of the same
        edge, or a join followed by that user's first transaction, stay in order).
        Returns the number of events written per type.
        """
        counts: dict[str, int] = {}
        with self.session() as session:
            for etype, rows in group_consecutive(events):
                cypher = _WRITE_QUERIES.get(etype)
                if cypher:
                    session.run(cypher, rows=rows)
                    counts[etype.value] = counts.get(etype.value, 0) + len(rows)

        logger.info("batch_written", counts=counts, total=len(events))
        return counts

    # ─── Reads for detectors ──────────────────────────────────────────

    def run_query(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """Execute a Cypher query and return results as a list of dicts."""
        with self.session() as session:
            result = session.run(cypher, **params)
            return [dict(record) for record in result]

    def get_node_degrees(self, label: str, id_property: str) -> dict[str, int]:
        """Return {node_id: degree} for all nodes of a given label."""
        cypher = f"""
        MATCH (n:{label})
        RETURN n.{id_property} AS id, size((n)--()) AS degree
        """
        rows = self.run_query(cypher)
        return {row["id"]: row["degree"] for row in rows}

    def get_neighbors(self, node_id: str, label: str, k_hops: int = 1) -> set[str]:
        """Return user_ids within k hops of a node."""
        cypher = f"""
        MATCH (start:{label} {{user_id: $node_id}})
        CALL apoc.path.subgraphNodes(start, {{maxLevel: $k_hops}})
        YIELD node
        WHERE node:User
        RETURN node.user_id AS user_id
        """
        # Fallback if APOC is not available
        if k_hops == 1:
            cypher = f"""
            MATCH (start:{label} {{user_id: $node_id}})-[*1]-(neighbor:User)
            RETURN DISTINCT neighbor.user_id AS user_id
            """
        elif k_hops == 2:
            cypher = f"""
            MATCH (start:{label} {{user_id: $node_id}})-[*1..2]-(neighbor:User)
            RETURN DISTINCT neighbor.user_id AS user_id
            """
        else:
            cypher = f"""
            MATCH (start:{label} {{user_id: $node_id}})-[*1..{k_hops}]-(neighbor:User)
            RETURN DISTINCT neighbor.user_id AS user_id
            """

        rows = self.run_query(cypher, node_id=node_id)
        return {row["user_id"] for row in rows}

    def get_dirty_set(self, user_ids: set[str], k_hops: int = 2) -> set[str]:
        """
        Expand a set of touched user_ids to their k-hop neighborhoods.

        This is the core of incremental detection: only re-evaluate the
        subgraph that was actually affected by new events.
        """
        if not user_ids:
            return set()

        cypher = f"""
        UNWIND $user_ids AS uid
        MATCH (start:User {{user_id: uid}})-[*1..{k_hops}]-(neighbor:User)
        RETURN DISTINCT neighbor.user_id AS user_id
        """
        rows = self.run_query(cypher, user_ids=list(user_ids))
        result = {row["user_id"] for row in rows}
        result.update(user_ids)  # include the original touched nodes
        return result

    def get_graph_stats(self) -> dict[str, Any]:
        """Return current graph size statistics."""
        cypher = """
        MATCH (n) WITH labels(n)[0] AS label, count(*) AS cnt
        RETURN label, cnt ORDER BY label
        """
        rows = self.run_query(cypher)
        stats = {row["label"]: row["cnt"] for row in rows}

        rel_cypher = """
        MATCH ()-[r]->() WITH type(r) AS rel_type, count(*) AS cnt
        RETURN rel_type, cnt ORDER BY rel_type
        """
        rel_rows = self.run_query(rel_cypher)
        stats["_relationships"] = {row["rel_type"]: row["cnt"] for row in rel_rows}

        return stats

    # ─── TTL Pruning ──────────────────────────────────────────────────

    def prune_old_data(self, ttl_seconds: float) -> int:
        """
        Delete nodes and relationships older than TTL.

        Returns the number of nodes deleted.
        """
        cutoff = datetime.now(UTC).isoformat()
        # For now, prune transactions with old timestamps
        cypher = """
        MATCH (t:Transaction)
        WHERE t.ts < datetime($cutoff) - duration({seconds: $ttl})
        DETACH DELETE t
        RETURN count(*) AS deleted
        """
        try:
            rows = self.run_query(cypher, cutoff=cutoff, ttl=int(ttl_seconds))
            deleted = rows[0]["deleted"] if rows else 0
            if deleted > 0:
                logger.info("ttl_pruned", deleted=deleted, ttl_seconds=ttl_seconds)
            return deleted
        except Exception as e:
            logger.warning("ttl_prune_failed", error=str(e))
            return 0


def group_consecutive(events: list[Event]) -> list[tuple[EventType, list[dict[str, Any]]]]:
    """Split events into runs of the same type, preserving order, as UNWIND rows."""
    groups: list[tuple[EventType, list[dict[str, Any]]]] = []
    for event in events:
        if event.event_type in GROUND_TRUTH_ONLY_EVENTS:
            raise ValueError(f"refusing to write ground-truth event {event.event_type} to graph")
        row = {**event.payload, "event_id": event.event_id, "ts": event.ts.isoformat()}
        if groups and groups[-1][0] == event.event_type:
            groups[-1][1].append(row)
        else:
            groups.append((event.event_type, [row]))
    return groups


# ─── Write query templates ────────────────────────────────────────────

_WRITE_QUERIES: dict[EventType, str] = {
    EventType.USER_JOIN: """
        UNWIND $rows AS r
        MERGE (u:User {user_id: r.user_id})
        SET u.name = r.name, u.created_at = r.ts, u.event_id = r.event_id, u.active = true
    """,
    EventType.DEVICE_LINK: """
        UNWIND $rows AS r
        MERGE (u:User {user_id: r.user_id})
        MERGE (d:Device {device_id: r.device_id})
        MERGE (u)-[rel:USED_DEVICE]->(d)
        SET rel.ts = r.ts, rel.event_id = r.event_id
    """,
    EventType.CARD_LINK: """
        UNWIND $rows AS r
        MERGE (u:User {user_id: r.user_id})
        MERGE (c:CreditCard {card_id: r.card_id})
        MERGE (u)-[rel:HAS_CARD]->(c)
        SET rel.ts = r.ts, rel.event_id = r.event_id
    """,
    EventType.IP_LINK: """
        UNWIND $rows AS r
        MERGE (u:User {user_id: r.user_id})
        MERGE (i:IPAddress {ip: r.ip})
        MERGE (u)-[rel:LOGGED_IN_FROM]->(i)
        SET rel.ts = r.ts, rel.event_id = r.event_id
    """,
    EventType.USER_LEAVE: """
        UNWIND $rows AS r
        MATCH (u:User {user_id: r.user_id})
        SET u.left_at = r.ts, u.active = false
    """,
    EventType.ATTRIBUTE_UNLINK: """
        UNWIND $rows AS r
        MATCH (u:User {user_id: r.user_id})-[rel]->(a)
        WHERE (r.attr_type = 'device' AND type(rel) = 'USED_DEVICE' AND a.device_id = r.attr_id)
           OR (r.attr_type = 'card' AND type(rel) = 'HAS_CARD' AND a.card_id = r.attr_id)
           OR (r.attr_type = 'ip' AND type(rel) = 'LOGGED_IN_FROM' AND a.ip = r.attr_id)
        DELETE rel
    """,
    EventType.TRANSACTION: """
        UNWIND $rows AS r
        MATCH (s:User {user_id: r.sender})
        MATCH (x:User {user_id: r.receiver})
        CREATE (s)-[:PERFORMED]->(t:Transaction {
            txn_id: r.txn_id,
            amount: r.amount,
            ts: r.ts,
            event_id: r.event_id
        })-[:SENT_TO]->(x)
    """,
}
