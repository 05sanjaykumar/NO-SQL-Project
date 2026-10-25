"""
Neo4j-backed GraphStore: same contract as InMemoryGraphStore, executed in Cypher.

Dirty-region expansion and region snapshots are each a small number of
Cypher round trips, regardless of how many detectors run on the region.
"""

from __future__ import annotations

from fraudsim.graph.neo4j_client import Neo4jClient
from fraudsim.graph.store import GraphStore, GraphView, Link, Txn, touched_users
from fraudsim.types import Event

_ATTR_RELS = "USED_DEVICE|HAS_CARD|LOGGED_IN_FROM"
_KIND_OF_REL = {"USED_DEVICE": "device", "HAS_CARD": "card", "LOGGED_IN_FROM": "ip"}
_ATTR_ID = "coalesce(a.device_id, a.card_id, a.ip)"
_ACTIVE_DEGREE = f"COUNT {{ (a)<-[:{_ATTR_RELS}]-(x:User) WHERE x.active }}"

_EXPAND = f"""
CALL () {{
    UNWIND $frontier AS uid
    MATCH (:User {{user_id: uid}})-[:{_ATTR_RELS}]->(a)
    WITH DISTINCT a
    WHERE {_ACTIVE_DEGREE} <= $max_fanout
    MATCH (a)<-[:{_ATTR_RELS}]-(v:User)
    WHERE v.active
    RETURN v.user_id AS v
    UNION
    UNWIND $frontier AS uid
    MATCH (:User {{user_id: uid}})-[:PERFORMED]->(t:Transaction)-[:SENT_TO]->(v:User)
    WHERE t.t >= $since AND v.active
    RETURN v.user_id AS v
    UNION
    UNWIND $frontier AS uid
    MATCH (v:User)-[:PERFORMED]->(t:Transaction)-[:SENT_TO]->(:User {{user_id: uid}})
    WHERE t.t >= $since AND v.active
    RETURN v.user_id AS v
}}
RETURN DISTINCT v
"""

_REGION_LINKS = f"""
UNWIND $ids AS uid
MATCH (u:User {{user_id: uid}})-[r:{_ATTR_RELS}]->(a)
WHERE u.active
RETURN u.user_id AS u, type(r) AS rel, {_ATTR_ID} AS attr, r.t AS t, {_ACTIVE_DEGREE} AS deg
"""

_REGION_TXNS = """
UNWIND $ids AS uid
MATCH (s:User {user_id: uid})-[:PERFORMED]->(t:Transaction)-[:SENT_TO]->(r:User)
WHERE s.active AND r.active AND t.t >= $since AND r.user_id IN $ids
RETURN t.txn_id AS id, s.user_id AS s, r.user_id AS r, t.amount AS amount, t.t AS t
"""

_FULL_LINKS = f"""
MATCH (u:User)-[r:{_ATTR_RELS}]->(a)
WHERE u.active
RETURN u.user_id AS u, type(r) AS rel, {_ATTR_ID} AS attr, r.t AS t, {_ACTIVE_DEGREE} AS deg
"""

_FULL_TXNS = """
MATCH (s:User)-[:PERFORMED]->(t:Transaction)-[:SENT_TO]->(r:User)
WHERE s.active AND r.active AND t.t >= $since
RETURN t.txn_id AS id, s.user_id AS s, r.user_id AS r, t.amount AS amount, t.t AS t
"""


class Neo4jGraphStore(GraphStore):
    """GraphStore over a live Neo4j database."""

    backend = "neo4j"

    def __init__(self, client: Neo4jClient, profile_every: int = 0) -> None:
        self.client = client
        self.queries = 0  # Cypher round trips issued through this store
        # Every `profile_every`-th read query runs under PROFILE and its database
        # hits are accumulated (0 = never; profiling slows queries down).
        self.profile_every = profile_every
        self.db_hits = 0
        self.profiled_queries = 0

    def _q(self, cypher: str, **params: object) -> list[dict[str, object]]:
        self.queries += 1
        if self.profile_every and self.queries % self.profile_every == 0:
            rows, hits = self.client.run_profiled(cypher, **params)
            self.db_hits += hits
            self.profiled_queries += 1
            return rows
        return self.client.run_query(cypher, **params)

    def apply(self, events: list[Event]) -> set[str]:
        if events:
            self.queries += 1
            self.client.write_events_batch(events)
        return touched_users(events)

    def _active(self, users: set[str]) -> set[str]:
        rows = self._q(
            "UNWIND $ids AS uid MATCH (u:User {user_id: uid}) WHERE u.active RETURN u.user_id AS u",
            ids=sorted(users),
        )
        return {str(r["u"]) for r in rows}

    def expand(
        self,
        seeds: set[str],
        k: int,
        max_fanout: int,
        since_t: float,
        far_fanout: int | None = None,
    ) -> set[str]:
        region = self._active(seeds) if seeds else set()
        frontier = set(region)
        for hop in range(k):
            cap = max_fanout if hop == 0 or far_fanout is None else far_fanout
            if not frontier:
                break
            rows = self._q(
                _EXPAND,
                frontier=sorted(frontier),
                max_fanout=cap,
                since=since_t,
            )
            nxt = {str(r["v"]) for r in rows} - region
            if not nxt:
                break
            region |= nxt
            frontier = nxt
        return region

    def _build(
        self,
        link_rows: list[dict[str, object]],
        txn_rows: list[dict[str, object]],
        since_t: float,
        now_t: float,
    ) -> GraphView:
        users: set[str] = set()
        links: list[Link] = []
        degree: dict[tuple[str, str], int] = {}
        for r in link_rows:
            kind = _KIND_OF_REL[str(r["rel"])]
            attr = str(r["attr"])
            users.add(str(r["u"]))
            links.append(Link(str(r["u"]), kind, attr, float(r["t"])))  # type: ignore[arg-type]
            degree[(kind, attr)] = int(r["deg"])  # type: ignore[call-overload]
        txns = [
            Txn(str(r["id"]), str(r["s"]), str(r["r"]), float(r["amount"]), float(r["t"]))  # type: ignore[arg-type]
            for r in txn_rows
        ]
        return GraphView(now_t, since_t, users, links, degree, txns)

    def view(self, users: set[str], since_t: float, now_t: float) -> GraphView:
        ids = sorted(users)
        view = self._build(
            self._q(_REGION_LINKS, ids=ids),
            self._q(_REGION_TXNS, ids=ids, since=since_t),
            since_t,
            now_t,
        )
        # Active users with no remaining links still belong to the region.
        view.users |= self._active(users) if users - view.users else set()
        return view

    def full_view(self, since_t: float, now_t: float) -> GraphView:
        view = self._build(self._q(_FULL_LINKS), self._q(_FULL_TXNS, since=since_t), since_t, now_t)
        rows = self._q("MATCH (u:User) WHERE u.active RETURN u.user_id AS u")
        view.users |= {str(r["u"]) for r in rows}
        view.full = True
        return view

    def joined(self, users: set[str]) -> dict[str, float]:
        rows = self._q(
            "UNWIND $ids AS uid MATCH (u:User {user_id: uid}) WHERE u.joined_t IS NOT NULL "
            "RETURN u.user_id AS u, u.joined_t AS t",
            ids=sorted(users),
        )
        return {str(r["u"]): float(r["t"]) for r in rows}  # type: ignore[arg-type]

    def prune(self, cutoff_t: float) -> dict[str, int]:
        self.queries += 2
        return self.client.prune_before(cutoff_t)

    def stats(self) -> dict[str, int]:
        rows = self._q(
            "MATCH (u:User) WITH count(u) AS users, count(CASE WHEN u.active THEN 1 END) AS act "
            "CALL () { MATCH (:User)-[r:" + _ATTR_RELS + "]->() RETURN count(r) AS links } "
            "CALL () { MATCH (t:Transaction) RETURN count(t) AS txns } "
            "CALL () { MATCH (a) WHERE (a:Device OR a:CreditCard OR a:IPAddress) "
            "  AND EXISTS { (a)<--(:User) } RETURN count(a) AS attrs } "
            "RETURN users, act, links, txns, attrs"
        )
        r = rows[0]
        return {
            "users": int(r["users"]),  # type: ignore[call-overload]
            "active_users": int(r["act"]),  # type: ignore[call-overload]
            "links": int(r["links"]),  # type: ignore[call-overload]
            "attributes": int(r["attrs"]),  # type: ignore[call-overload]
            "transactions": int(r["txns"]),  # type: ignore[call-overload]
        }
