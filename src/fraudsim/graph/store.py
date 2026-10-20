"""
Graph store abstraction used by the incremental detection engine.

Two interchangeable backends implement the same contract:

- `Neo4jGraphStore` (graph/neo4j_store.py): the production path. Events are
  written with batched UNWIND, and dirty regions are fetched with Cypher.
- `InMemoryGraphStore` (this module): a pure-Python mirror with identical
  semantics. Used by unit tests (hand-built micro-graphs), by large headless
  sweeps, and as the reference that the Neo4j store is checked against.

Detectors never talk to a backend directly. They receive a `GraphView`, a
snapshot of the region to evaluate. Only the static Cypher and GDS baselines
also run backend-native queries, because measuring that is their point.

Time is simulated time in epoch seconds (`t`), taken from event timestamps.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fraudsim.types import Event, EventType

ATTR_KINDS = ("device", "card", "ip")
LINK_KIND: dict[EventType, tuple[str, str]] = {
    EventType.DEVICE_LINK: ("device", "device_id"),
    EventType.CARD_LINK: ("card", "card_id"),
    EventType.IP_LINK: ("ip", "ip"),
}

Attr = tuple[str, str]  # (kind, attribute id)


@dataclass(frozen=True, slots=True)
class Link:
    user: str
    kind: str
    attr: str
    t: float  # last time the link was (re)established


@dataclass(frozen=True, slots=True)
class Txn:
    txn_id: str
    sender: str
    receiver: str
    amount: float
    t: float


@dataclass(slots=True)
class GraphView:
    """
    Snapshot of the region a detector evaluates.

    `users` are the active users in the region. `links` are all their
    attribute links. `attr_degree` is each linked attribute's *global* number
    of active users (needed for hub weighting even when the hub's other users
    are outside the region). `txns` are transactions with both ends in the
    region and t >= since_t.
    """

    now_t: float
    since_t: float
    users: set[str]
    links: list[Link]
    attr_degree: dict[Attr, int]
    txns: list[Txn]
    full: bool = False
    _by_user: dict[str, list[Link]] | None = field(default=None, repr=False)
    _by_attr: dict[Attr, list[Link]] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        # Canonical order: detectors iterate these lists, and floating-point sums
        # depend on order, so an order that varied with set/hash iteration (or with
        # Neo4j result order) would make detection differ between processes.
        self.links.sort(key=lambda lk: (lk.user, lk.kind, lk.attr))
        self.txns.sort(key=lambda tx: (tx.t, tx.txn_id))

    @property
    def now(self) -> datetime:
        return datetime.fromtimestamp(self.now_t, tz=UTC)

    def links_by_user(self) -> dict[str, list[Link]]:
        if self._by_user is None:
            grouped: dict[str, list[Link]] = defaultdict(list)
            for link in self.links:
                grouped[link.user].append(link)
            self._by_user = dict(grouped)
        return self._by_user

    def links_by_attr(self) -> dict[Attr, list[Link]]:
        if self._by_attr is None:
            grouped: dict[Attr, list[Link]] = defaultdict(list)
            for link in self.links:
                grouped[(link.kind, link.attr)].append(link)
            self._by_attr = dict(grouped)
        return self._by_attr

    def canonical(self) -> dict[str, Any]:
        """Order-independent form, used to check that two backends agree."""
        return {
            "users": sorted(self.users),
            "links": sorted((lk.user, lk.kind, lk.attr, round(lk.t, 6)) for lk in self.links),
            "attr_degree": sorted((k, a, d) for (k, a), d in self.attr_degree.items()),
            "txns": sorted(
                (t.txn_id, t.sender, t.receiver, round(t.amount, 2), round(t.t, 6))
                for t in self.txns
            ),
        }


def event_t(event: Event) -> float:
    return event.ts.timestamp()


def touched_users(events: list[Event]) -> set[str]:
    """Users directly affected by a batch of events."""
    touched: set[str] = set()
    for e in events:
        p = e.payload
        if e.event_type == EventType.TRANSACTION:
            touched.add(p["sender"])
            touched.add(p["receiver"])
        elif "user_id" in p:
            touched.add(p["user_id"])
    return touched


class GraphStore(ABC):
    """Contract shared by the Neo4j and in-memory stores."""

    backend: str = "abstract"

    @abstractmethod
    def apply(self, events: list[Event]) -> set[str]:
        """Write a batch of events. Returns the users it touched."""

    @abstractmethod
    def expand(
        self,
        seeds: set[str],
        k: int,
        max_fanout: int,
        since_t: float,
        far_fanout: int | None = None,
    ) -> set[str]:
        """
        Active users within k user-hops of seeds (seeds included if active).

        One hop = sharing an attribute or a transaction in either direction with
        t >= since_t. The first hop crosses attributes with active degree <=
        max_fanout (so a hub's co-members are in reach); later hops only cross
        attributes with degree <= far_fanout (default max_fanout), so hubs do
        not pull in the whole graph.
        """

    @abstractmethod
    def view(self, users: set[str], since_t: float, now_t: float) -> GraphView:
        """Snapshot of the region spanned by `users`."""

    @abstractmethod
    def full_view(self, since_t: float, now_t: float) -> GraphView:
        """Snapshot of the whole active graph (full recomputation)."""

    @abstractmethod
    def joined(self, users: set[str]) -> dict[str, float]:
        """Account creation time (t of user_join) for the given users, where known."""

    @abstractmethod
    def prune(self, cutoff_t: float) -> dict[str, int]:
        """Drop transactions and attribute links older than cutoff_t (TTL)."""

    @abstractmethod
    def stats(self) -> dict[str, int]:
        """Current graph size."""


class InMemoryGraphStore(GraphStore):
    """Pure-Python graph store with the same semantics as Neo4jGraphStore."""

    backend = "memory"

    def __init__(self) -> None:
        self.active: dict[str, bool] = {}
        self.joined_t: dict[str, float] = {}
        self.user_links: dict[str, dict[Attr, float]] = defaultdict(dict)
        self.attr_users: dict[Attr, dict[str, float]] = defaultdict(dict)
        self.out_txns: dict[str, list[Txn]] = defaultdict(list)
        self.in_txns: dict[str, list[Txn]] = defaultdict(list)
        self.n_txns = 0

    # ─── Writes ───────────────────────────────────────────────────────

    def apply(self, events: list[Event]) -> set[str]:
        for e in events:
            p = e.payload
            t = event_t(e)
            et = e.event_type
            if et == EventType.USER_JOIN:
                self.active[p["user_id"]] = True
                self.joined_t[p["user_id"]] = t
            elif et == EventType.USER_LEAVE:
                if p["user_id"] in self.active:
                    self.active[p["user_id"]] = False
            elif et in LINK_KIND:
                kind, key = LINK_KIND[et]
                uid, attr = p["user_id"], (kind, p[key])
                self.active.setdefault(uid, True)
                self.user_links[uid][attr] = t
                self.attr_users[attr][uid] = t
            elif et == EventType.ATTRIBUTE_UNLINK:
                attr = (p["attr_type"], p["attr_id"])
                self.user_links[p["user_id"]].pop(attr, None)
                self.attr_users[attr].pop(p["user_id"], None)
            elif et == EventType.TRANSACTION:
                s, r = p["sender"], p["receiver"]
                if s not in self.active or r not in self.active:
                    continue  # mirrors MATCH semantics in Neo4j
                txn = Txn(p["txn_id"], s, r, float(p["amount"]), t)
                self.out_txns[s].append(txn)
                self.in_txns[r].append(txn)
                self.n_txns += 1
        return touched_users(events)

    # ─── Reads ────────────────────────────────────────────────────────

    def _degree(self, attr: Attr) -> int:
        return sum(1 for u in self.attr_users.get(attr, {}) if self.active.get(u))

    def expand(
        self,
        seeds: set[str],
        k: int,
        max_fanout: int,
        since_t: float,
        far_fanout: int | None = None,
    ) -> set[str]:
        region = {u for u in seeds if self.active.get(u)}
        frontier = set(region)
        degree_cache: dict[Attr, int] = {}
        for hop in range(k):
            cap = max_fanout if hop == 0 or far_fanout is None else far_fanout
            nxt: set[str] = set()
            for u in frontier:
                for attr in self.user_links.get(u, {}):
                    if attr not in degree_cache:
                        degree_cache[attr] = self._degree(attr)
                    if degree_cache[attr] > cap:
                        continue
                    nxt.update(self.attr_users[attr])
                for txn in self.out_txns.get(u, ()):
                    if txn.t >= since_t:
                        nxt.add(txn.receiver)
                for txn in self.in_txns.get(u, ()):
                    if txn.t >= since_t:
                        nxt.add(txn.sender)
            nxt = {v for v in nxt if self.active.get(v) and v not in region}
            if not nxt:
                break
            region |= nxt
            frontier = nxt
        return region

    def view(self, users: set[str], since_t: float, now_t: float) -> GraphView:
        region = {u for u in users if self.active.get(u)}
        links: list[Link] = []
        degree: dict[Attr, int] = {}
        for u in region:
            for attr, t in self.user_links.get(u, {}).items():
                links.append(Link(u, attr[0], attr[1], t))
                if attr not in degree:
                    degree[attr] = self._degree(attr)
        txns = [
            txn
            for u in region
            for txn in self.out_txns.get(u, ())
            if txn.t >= since_t and txn.receiver in region
        ]
        return GraphView(now_t, since_t, region, links, degree, txns)

    def joined(self, users: set[str]) -> dict[str, float]:
        return {u: self.joined_t[u] for u in users if u in self.joined_t}

    def full_view(self, since_t: float, now_t: float) -> GraphView:
        view = self.view({u for u, a in self.active.items() if a}, since_t, now_t)
        view.full = True
        return view

    def prune(self, cutoff_t: float) -> dict[str, int]:
        dropped_links = 0
        for u in list(self.user_links):
            for attr, t in list(self.user_links[u].items()):
                if t < cutoff_t:
                    del self.user_links[u][attr]
                    self.attr_users[attr].pop(u, None)
                    dropped_links += 1
        dropped_txns = 0
        for index in (self.out_txns, self.in_txns):
            for u in list(index):
                kept = [txn for txn in index[u] if txn.t >= cutoff_t]
                if index is self.out_txns:
                    dropped_txns += len(index[u]) - len(kept)
                index[u] = kept
        self.n_txns -= dropped_txns
        return {"links": dropped_links, "transactions": dropped_txns}

    def stats(self) -> dict[str, int]:
        return {
            "users": len(self.active),
            "active_users": sum(self.active.values()),
            "links": sum(len(v) for v in self.user_links.values()),
            "attributes": sum(1 for v in self.attr_users.values() if v),
            "transactions": self.n_txns,
        }
